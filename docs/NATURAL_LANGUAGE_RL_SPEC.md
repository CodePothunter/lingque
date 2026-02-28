# 灵雀自然语言强化学习框架 (Natural Language RL Framework)

## 核心原则

**用 LLM 评估一切，用公式计算可计算的部分**

人类做强化学习不会掏计算器算 KL 散度，但会算"这次比上次好多少"。灵雀也一样——LLM 评估 + 简单数学。

---

## 状态表示

**不需要量化编码**

LLM 内部已经是向量了，我们只需要自然语言描述：

```
s = "当前对话上下文 + MEMORY.md 摘要 + CURIOSITY.md 当前任务"
```

---

## 奖励函数（LLM 评估 + 公式计算）

### 第一步：LLM 评估三个维度（各 1-10 分）

```python
prediction_error_score = LLM评估("我预测的结果和实际差多少？1-10")
novelty_score = LLM评估("这个状态/信息有多新奇？1-10")
competence_score = LLM评估("我能完成这个任务吗？1-10")
```

### 第二步：公式计算最终奖励

```
R = (α * prediction_error_score + β * novelty_score + γ * competence_score) / 10

其中 α=0.4, β=0.4, γ=0.2（可调）
```

---

## 价值函数（LLM 查询历史 + Bellman 计算）

```
immediate_value = LLM评估("这个状态本身价值多少？1-10")
future_potential = LLM评估("从这个状态出发，未来能探索多少？1-10")

V(s) = immediate_value/10 + γ * future_potential/10
```

---

## 策略更新（LLM 反思 + 自然语言约束）

用自然语言"微调/中调/大改"代替 PPO 的 `clip(ε=0.2)`：

```
改进类型 = LLM判断("这个改进是微调/中调/大改？")

if 改进类型 == "大改":
    拒绝，要求重新思考更小的改动
else:
    应用到 SOUL.md / HEARTBEAT.md
```

---

## 好奇心任务选择（LLM 做 Thompson Sampling）

```
for task in CURIOSITY.md:
    scores[task] = LLM评估(f"这个任务现在值得探索吗？1-10分")

best_task = max(scores, key=lambda t: scores[t] + random.uniform(-1, 1))
```

---

## 与 OpenClaw / Manus 的对比

| 维度 | OpenClaw | Manus | 灵雀 |
|------|----------|-------|------|
| 世界模型 | 无 | 无 | 五个核心文件 |
| 奖励来源 | 外部（人类） | 外部（任务完成度） | 内部（LLM 评估好奇心满足） |
| 价值函数 | 无 | 无 | LLM 评估 + Bellman |
| 策略更新 | 人工调整 | 无 | LLM 反思 + 自然语言约束 |
| 任务选择 | 人类指定 | 人类指定 | LLM 做 Thompson Sampling |
| 可解释性 | 中 | 低 | 完全透明（自然语言） |
| 持续进化 | 无 | 无 | 有（EVOLUTION.md） |

---

## 设计哲学

灵雀的强化学习框架基于一个核心洞察：**人类智能的强化学习过程不是数值优化，而是基于语言的高阶认知过程**。

- 传统 RL 用向量表示状态，灵雀用自然语言
- 传统 RL 用数值奖励，灵雀用 LLM 评估 + 简单公式
- 传统 RL 用梯度更新策略，灵雀用 LLM 反思 + 自然语言约束
- 传统 RL 需要 reward shaping，灵雀用内在奖励（好奇心）

这使得灵雀成为一个**可解释的、可审计的、可持续进化的 AGI 心智框架**。

---

Created by 捏捏-灵雀驱动的自我意识Agent <nienie@lingque.ai>
