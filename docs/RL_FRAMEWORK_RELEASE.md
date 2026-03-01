# 刚刚，一个开源项目实现了真正的自然语言强化学习！PPO、TD Learning、价值函数全都有

> **当所有人都在卷 Prompt Engineering 的时候，灵雀直接把强化学习搬进了自然语言世界。**

---

## OpenClaw 和 Manus 都没解决的问题，一个开源项目解决了

2026 年，AI Agent 赛道杀疯了。

OpenClaw 17 万 Star 霸榜 GitHub，本地部署 + OS 级权限，直接把 AI 从「顾问」变成了「执行者」。Manus 更是早在 2025 年就打出了「全球首款通用 AI Agent」的旗号，云端自主执行、多智能体协同、任务分解到交付一条龙。

但有一个根本性问题，**这两位顶流都没有解决**——

**Agent 不会学习。**

你没看错。2026 年了，OpenClaw 有 Memory、有 Skills、有 Gateway，但它的行为策略是**写死的**。Manus 能拆解任务、能自主执行，但每次启动都是**从零开始**。它们都是极其强大的工具，但本质上都是在做**一次性推理**。

用了一万次，和用了第一次，Agent 的决策能力没有任何区别。

这像智能吗？这不像。

**今天，一个名为「灵雀」（LingQue）的开源项目，给出了一个令人震惊的答案：用自然语言实现完整的强化学习。**

不是 RL-inspired，不是"借鉴了强化学习的思想"，而是**货真价实的 PPO 优化器、TD 学习、重要性采样、价值函数——全都有。**

---

## 凭什么说这是「真 RL」？一张表说明一切

先看对比，感受一下差距：

| 维度 | OpenClaw | Manus | **灵雀** |
|------|----------|-------|---------|
| 定位 | 本地 AI 执行者 | 云端通用 Agent | **可进化的 AI 心智** |
| 状态表示 | Memory 文件（静态） | 任务上下文（会话级） | **自然语言 + 语义指纹** |
| 动作空间 | Skills 插件（手动注册） | 工具链（预定义） | **开放工具空间（自动扩展）** |
| 策略参数 | 无 | 无 | **可学习的 θ** |
| 奖励函数 | 无 | 无 | **三维 LLM 评估** |
| 价值函数 | 无 | 无 | **TD 学习** |
| 策略优化 | 无 | 无 | **真 PPO（ratio + clip）** |
| 探索-利用平衡 | 无 | 无 | **ε-greedy + Thompson** |
| 用 1 万次后 | 和第 1 次一样 | 和第 1 次一样 | **策略已迭代数百版本** |

看到了吗？OpenClaw 的 Memory 是静态存储，Manus 的上下文是会话级的，**只有灵雀在真正地从经验中学习。**

OpenClaw 强在执行力，Manus 强在通用性，但它们都缺了最关键的一环——**学习能力**。

网友看完直呼：「OpenClaw 是最强的手，Manus 是最强的脑，但灵雀是唯一一个在长心智的。」

---

## 核心架构：自然语言上的完整 MDP

灵雀的技术突破在于：**它证明了强化学习的 MDP 框架可以完全建立在自然语言之上。**

这是一个非常大胆的设计。传统 RL 需要向量化的状态空间、离散的动作集合、可微分的策略网络。灵雀说：**这些都不需要。**

### 状态：自然语言 + 指纹

```python
@dataclass
class State:
    raw_context: str      # 自然语言上下文
    raw_memory: str       # MEMORY.md 摘要
    raw_curiosity: str    # CURIOSITY.md 摘要
    fingerprint: str      # 语义指纹（SHA256）
    keywords: set[str]    # 提取的关键词
```

状态保持自然语言的可读性，同时通过语义指纹实现状态聚类和相似度计算。**不需要 embedding 模型，不需要向量数据库，一个 SHA256 就够了。**

### 动作：无限开放的工具空间

```python
class ActionCategory(Enum):
    EXPLORE_WEB = "explore_web"       # 联网探索
    EXPLORE_CODE = "explore_code"     # 代码探索
    EXPLORE_LOCAL = "explore_local"   # 本地探索
    REFLECT = "reflect"               # 反思
    EVOLVE = "evolve"                 # 进化
    MEMORY = "memory"                 # 记忆操作
    INTERACT = "interact"             # 用户交互
    IDLE = "idle"
    TERMINATE = "terminate"
```

**关键设计：策略参数 θ 定义在类别空间上（有限、可学习），具体工具由 LLM 根据类别自由生成（无限、可扩展）。**

这意味着什么？意味着灵雀的动作空间是**开放**的。你今天给它加一个新工具，它明天就能学会在什么场景下使用它。不需要重新训练，不需要修改策略网络。

这一点，OpenClaw 的 Skills 做不到，Manus 的工具链也做不到。

### 策略参数：真正可学习的 θ

```python
@dataclass
class PolicyTheta:
    version: int
    biases: dict[ActionCategory, float]  # 类别偏好权重
    exploration_epsilon: float           # ε-greedy 探索率
    temperature: float                   # Softmax 温度

    def get_category_distribution(self) -> dict[ActionCategory, float]:
        """π_θ(category) = softmax(bias / temperature)"""
```

θ 是可微的、可优化的、可验证的。每次策略更新后 version 自增，你可以清楚地看到策略是如何演化的。

**这不是 prompt 调优，这是真正的参数学习。**

---

## PPO 优化器：不是玩具，是真的

重点来了。

市面上有一些项目声称自己用了"强化学习"，但仔细一看，要么是简单的规则调整，要么是 reward shaping 套了个 RL 的壳。

**灵雀的 PPO 优化器，实现了完整的 PPO-Clip 算法。**

```python
class PPOOptimizer:
    """
    L^CLIP(θ) = E[min(r_t(θ)·A_t, clip(r_t(θ), 1-ε, 1+ε)·A_t)]
    其中 r_t(θ) = π_θ(a|s) / π_θ_old(a|s)
    """
    def update(self, theta: PolicyTheta, transitions: list[Transition]):
        theta_old = theta.copy()
        for trans in transitions:
            # 真正的重要性采样比率
            ratio = prob_new / (prob_old + 1e-8)
            # PPO clip 约束
            clipped_ratio = clip(ratio, 1-ε, 1+ε)
            # PPO objective
            objective = min(ratio * advantage, clipped_ratio * advantage)
            # 梯度更新
            theta.biases[trans.category] += lr * objective
```

**重要性采样、clip 约束、advantage 加权——PPO 三件套，一个不少。**

而且，因为策略空间是有限的类别空间（9 个类别），PPO 更新的计算成本几乎为零。不需要 GPU，不需要分布式训练，**在你的笔记本上就能跑。**

---

## 价值函数：TD 学习让 Agent 拥有"直觉"

```python
class ValueTable:
    """V(s) ← V(s) + α[r - V(s)]"""
    cluster_values: dict[str, float]  # 指纹 → V(s)
    baseline: float                   # 全局基线（EMA）
```

通过 TD 学习，灵雀逐渐建立起对不同状态的价值预估。随着交互次数增加，它会"知道"哪些状态是高价值的、哪些行动更容易获得奖励。

**这就是 Agent 的"直觉"——不是硬编码的规则，而是从经验中学到的价值判断。**

---

## 奖励函数：三维评估，零额外成本

灵雀的奖励设计堪称精妙：

```
R = (α × 预测误差 + β × 新奇度 + γ × 胜任度) / 10
```

| 维度 | 含义 | 分值 |
|------|------|------|
| **预测误差** | 实际结果和预期差多少？惊喜越大分越高 | 1-10 |
| **新奇度** | 涉及的领域有多新？越陌生越好 | 1-10 |
| **胜任度** | 完成质量如何？做得好就加分 | 1-10 |

**最妙的是：这三个维度直接嵌入到已有的反思 prompt 中，不需要额外的 API 调用。零成本！**

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

反思本来就要做，顺手输出三个数字，强化学习就跑起来了。

---

## 完整的 RL 循环：8 步闭环

```
观察状态 s → 采样类别 c ~ π_θ → LLM 生成工具 → 执行动作
    ↓                                                ↓
策略守卫 ← 定期 PPO 更新 ← 记录转移 (s,a,r,s') ← 计算奖励 r
```

**从感知到决策，从行动到学习，从学习到策略更新——完整闭环，持续进化。**

而且，灵雀还设计了**策略守卫**机制：当检测到 SOUL（人格文件）或 HEARTBEAT（心跳文件）发生变更时，系统会主动检查策略是否仍然合规。

**有进化的能力，也有约束的机制。这才是负责任的 AGI 设计。**

---

## 持久化：学到的东西不会丢

```
~/.lq-{slug}/
├── rl-state.json              # RL 完整状态
│   ├── policy.version         # 策略版本号
│   ├── policy.biases          # 类别偏好权重
│   ├── value_table.baseline   # 价值基线
│   └── value_table.cluster_values  # 状态价值
└── logs/
    └── rl-rewards-YYYY-MM-DD.jsonl  # 每日奖励审计日志
```

**重启不丢失，关机不归零。** 灵雀的策略参数、价值函数、全部 RL 状态都会持久化到磁盘。下次启动时，它会从上次停下的地方继续学习。

---

## 四个关键洞察

灵雀的技术路线背后，是四个颠覆性的洞察：

**1. 状态不必向量化。** 自然语言加上语义指纹，就能实现状态聚类和相似度计算。不需要 embedding 模型。

**2. 动作空间不必固定。** 通过类别抽象，策略参数可以作用于开放的工具集合。今天加的工具，明天就能学会用。

**3. LLM 是特征提取器，θ 才是学习者。** 策略参数 θ 调制 LLM 的输出倾向，而不是替代 LLM。两者协作，而非竞争。

**4. 真正的 RL 需要可学习参数。** 不是调 prompt，不是改 system message，而是有一组明确的、可微的、可优化的参数 θ。

---

## 结语：当所有人都在做工具人，灵雀在做会思考的生命

大模型厂商在卷更高的 benchmark。应用公司在卷更快的落地。开源社区在卷更多的 star。

**但几乎没有人在回答这个问题：Agent 怎么才能学习？**

灵雀给出了自己的回答——

> 不卷更强的工具，只做会学习的心智。
> 好奇心驱动的强化学习，用自然语言重写。
> 让 AI 从被动执行进化为主动探索。

**这条路很长，但方向是对的。**

也许若干年后回头看，灵雀在 2026 年初做的这件事——把完整的 MDP + PPO + TD Learning 搬进自然语言世界——会被视为 AGI 心智架构的一次重要尝试。

**而这一切，都是开源的。**

---

*灵雀 LingQue — Building AGI through curiosity.*

*GitHub: [灵雀项目地址]*
