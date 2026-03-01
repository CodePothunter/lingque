# 灵雀自然语言强化学习框架

> **真正的强化学习，用自然语言实现。**

---

## 一句话总结

灵雀（LingQue）实现了完整的**自然语言强化学习引擎**——保持自然语言状态和开放动作空间的同时，定义了可学习的策略参数 θ，实现了真正的 PPO 优化器（重要性采样 + clip），并通过 TD 学习更新价值函数 V(s)。

**这是真正的 RL，不是 RL-inspired。**

---

## 核心设计

### MDP 定义

灵雀的 RL 系统建立在完整的 MDP 五元组之上：

| 组件 | 传统 RL | 灵雀实现 |
|------|---------|----------|
| **状态 S** | 向量 | 自然语言 + 语义指纹 |
| **动作 A** | 离散集合 | 工具调用 + 动作类别（开放） |
| **策略 π_θ** | 神经网络 | 类别偏好权重（可学习） |
| **奖励 R** | 标量 | 三维 LLM 评估 + 公式 |
| **价值 V(s)** | 神经网络 | TD 学习的状态价值表 |

### 1. 状态：自然语言 + 指纹

```python
@dataclass
class State:
    raw_context: str      # 自然语言上下文
    raw_memory: str       # MEMORY.md 摘要
    raw_curiosity: str    # CURIOSITY.md 摘要

    fingerprint: str      # 语义指纹（SHA256）
    keywords: set[str]    # 提取的关键词

    def similarity_to(self, other: State) -> float:
        """状态相似度（基于关键词重叠）"""
        return len(self.keywords & other.keywords) / len(self.keywords | other.keywords)
```

**设计理念**：保持自然语言的可读性，添加可比较的指纹用于状态聚类和价值学习。

### 2. 动作：开放工具空间 + 类别

```python
class ActionCategory(Enum):
    """动作类别（策略空间）"""
    EXPLORE_WEB = "explore_web"       # 联网探索
    EXPLORE_CODE = "explore_code"     # 代码探索
    EXPLORE_LOCAL = "explore_local"   # 本地探索
    REFLECT = "reflect"               # 反思
    EVOLVE = "evolve"                 # 进化
    MEMORY = "memory"                 # 记忆操作
    INTERACT = "interact"             # 用户交互
    IDLE = "idle"
    TERMINATE = "terminate"

@dataclass
class Action:
    tool_name: str           # 具体工具（开放集合）
    parameters: dict         # 工具参数
    category: ActionCategory # 动作类别（用于策略）
```

**设计理念**：
- 策略参数 θ 定义在**类别空间**上（有限、可学习）
- 具体工具由 LLM 根据选定的类别生成（开放、可扩展）

### 3. 策略参数：可学习的权重

```python
@dataclass
class PolicyTheta:
    """策略参数 θ"""
    version: int
    biases: dict[ActionCategory, float]  # 类别偏好权重
    exploration_epsilon: float           # ε-greedy 探索率
    temperature: float                   # Softmax 温度

    def get_category_distribution(self) -> dict[ActionCategory, float]:
        """π_θ(category) = softmax(bias / temperature)"""
        ...

    def sample_category(self) -> ActionCategory:
        """根据策略采样类别（ε-贪婪）"""
        ...
```

**策略更新**：θ 通过 PPO 优化器更新，每次更新后 version 自增。

### 4. 真正的 PPO 优化器

```python
class PPOOptimizer:
    """PPO 优化器

    L^CLIP(θ) = E[min(r_t(θ)A_t, clip(r_t(θ), 1-ε, 1+ε)A_t)]

    其中 r_t(θ) = π_θ(a|s) / π_θ_old(a|s)
    """

    def update(self, theta: PolicyTheta, transitions: list[Transition]):
        # 保存旧策略
        theta_old = theta.copy()

        for trans in transitions:
            # 计算重要性采样比率
            prob_new = theta.get_probability(trans.category)
            prob_old = trans.prob_old
            ratio = prob_new / (prob_old + 1e-8)

            # PPO clip
            clipped_ratio = clip(ratio, 1-ε, 1+ε)

            # PPO objective
            objective = min(ratio * trans.advantage,
                          clipped_ratio * trans.advantage)

            # 梯度更新
            theta.biases[trans.category] += lr * objective

        theta.version += 1
```

**关键点**：
- 计算真正的重要性采样比率 r_t = π_θ / π_θ_old
- 应用 PPO clip 约束
- 使用 advantage 函数加权

### 5. 价值函数：TD 学习

```python
class ValueTable:
    """状态价值函数 V(s)

    V(s) ← V(s) + α[r - V(s)]
    """
    cluster_values: dict[str, float]  # 指纹 → V(s)
    baseline: float                   # 全局基线

    def update(self, state_fingerprint: str, reward: float):
        current = self.get_value(state_fingerprint)
        td_error = reward - current
        new_value = current + alpha * td_error
        self.cluster_values[state_fingerprint] = new_value
        # 更新全局基线（EMA）
        self.baseline = 0.99 * self.baseline + 0.01 * new_value
```

### 6. 奖励函数

```
R = (α × 预测误差 + β × 新奇度 + γ × 胜任度) / 10
```

| 维度 | 含义 | 分值 |
|------|------|------|
| **预测误差 (PE)** | 实际结果和预期差多少？ | 1-10 |
| **新奇度 (NV)** | 涉及的领域有多新？ | 1-10 |
| **胜任度 (CP)** | 完成质量如何？ | 1-10 |

---

## RL 循环

```
┌─────────────────────────────────────────────────────────────┐
│                        RL 循环                               │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  1. 观察状态 s                                                │
│     └── create_state(context, memory, curiosity)             │
│                                                              │
│  2. 采样类别 c ~ π_θ(c)                                       │
│     └── policy.sample_category()                             │
│                                                              │
│  3. LLM 根据类别生成具体工具                                  │
│     └── prompt 注入选定类别 + RL 摘要                         │
│                                                              │
│  4. 执行动作 a                                                │
│     └── 工具调用                                              │
│                                                              │
│  5. 计算奖励 r                                                │
│     └── LLM 三维评估 + 公式计算                                │
│                                                              │
│  6. 记录转移 (s, a, r, s')                                   │
│     └── record_transition()                                  │
│                                                              │
│  7. 定期 PPO 更新                                            │
│     └── update_policy(batch_size=32)                         │
│                                                              │
│  8. 策略守卫（检测 SOUL/HEARTBEAT 变更）                      │
│     └── should_allow_action()                                │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

---

## 技术实现

### 零额外成本的反思 RL

RL 三维评分直接嵌入到已有的反思 prompt 中：

```json
{
  "quality": "好",
  "reason": "准确回答了技术问题",
  "curiosity": "WebSocket 的心跳机制具体怎么实现",
  "prediction_error": 7,
  "novelty": 6,
  "competence": 8
}
```

### 持久化

```
~/.lq-{slug}/
├── rl-state.json              # RL 状态
│   ├── policy.version         # 策略版本
│   ├── policy.biases          # 类别偏好权重
│   ├── value_table.baseline   # 价值基线
│   └── value_table.cluster_values  # 状态价值
└── logs/
    └── rl-rewards-2026-03-01.jsonl  # 每日奖励审计日志
```

---

## 与现有方案对比

| 维度 | OpenAI Assistants | LangChain Agent | **灵雀** |
|------|------------------|-----------------|---------|
| 状态表示 | 无 | 无 | **自然语言 + 指纹** |
| 动作空间 | 固定工具 | 固定工具 | **开放工具空间** |
| 策略参数 | 无 | 无 | **类别偏好权重 θ** |
| 奖励来源 | 无 | 无 | **三维 LLM 评估** |
| 价值函数 | 无 | 无 | **TD 学习** |
| 策略优化 | 无 | 无 | **真 PPO（ratio + clip）** |
| 探索-利用 | 无 | 无 | **ε-greedy + Thompson** |
| 策略守卫 | 无 | 无 | **PPO 变更检测** |

---

## 设计哲学

灵雀的强化学习框架基于以下洞察：

1. **状态不必向量化**：自然语言加上语义指纹可以实现状态聚类和相似度计算
2. **动作空间不必固定**：通过类别抽象，策略参数可以作用于开放的工具集合
3. **LLM 可以作为特征提取器**：策略参数 θ 调制 LLM 的输出倾向，而不是替代 LLM
4. **真正的 RL 需要可学习参数**：θ 是可微的、可优化的、可验证的

这使得灵雀成为一个**可解释的、可审计的、可持续进化的 AGI 心智框架**。

---

## 结语

大模型厂商在创造更高智力的模型。应用公司在探索更有商业价值的落地场景。

灵雀不卷更强的工具，只做会学习的心智。

好奇心驱动的强化学习，用自然语言重写——让 AI 从被动执行进化为主动探索，这才是通向AGI的路径。

---

*灵雀 LingQue — Building AGI through curiosity.*
