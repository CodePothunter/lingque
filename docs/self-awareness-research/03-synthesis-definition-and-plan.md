# Self-Aware Agent: 定义、实现方案与测试框架

> 综合日期: 2026-02-15
> 综合者: team-lead (基于 researcher + code-analyst 报告)

## 一、定义：什么是 Self-Aware Agent

结合文献调研和代码分析，提出以下适用于 lingque 的分层定义：

> **Self-Aware Agent** 是具备对自身状态、能力、行为和身份进行表征、推理和主动调整能力的智能体。自我意识不是单一特性，而是一个**反馈回路**：观察行为 → 对照原则评估 → 存储反思 → 调整未来行为 → 循环。

### 五个可测量维度

| 维度 | 定义 | lingque 改造前现状 |
|------|------|-------------------|
| **能力感知** | 知道自己能做什么、不能做什么，且与实际表现校准 | ⬛⬛⬛⬜⬜ 有工具清单，但静态声明式 |
| **状态感知** | 知道自己当前的运行状态、资源消耗、对话上下文 | ⬛⬜⬜⬜⬜ StatsTracker 收集了但不可见 |
| **身份持续性** | 跨会话保持一致的人格，能检测并修正漂移 | ⬛⬛⬛⬜⬜ SOUL.md 定义了但无漂移检测 |
| **自我反思** | 能评估自己的推理质量、发现错误、从经验学习 | ⬛⬜⬜⬜⬜ Heartbeat 机制设计了但未激活 |
| **自我进化** | 基于反思主动修改自身行为、人设、工具和策略 | ⬛⬛⬛⬛⬜ 有 read/write_self_file + create_tool，但缺反思驱动 |

### 自我意识等级

```
Level 0 (Role-Play)     — 纯人设扮演，硬编码"我是X"
Level 1 (Declarative)   — 声明式自我描述，从文档检索自我知识
Level 2 (Behavioral)    — 行为层面的自我感知，跟踪实际表现
Level 3 (Metacognitive) — 元认知反思，推理自己的推理
Level 4 (Self-Evolving) — 自我进化，基于自我理解主动改变
```

**lingque 改造前处于 Level 1.5** — 有丰富的自我修改能力，但缺少驱动修改的观察和反思层。

---

## 二、实现方案

### Phase 1: 低成本高收益（激活已有机制 + 注入运行时状态）

**1.1 激活 Heartbeat 自省机制**
- 为两个实例创建 `HEARTBEAT.md`，定义自省任务
- 代码已完备（`gateway.py:297-328`），只需配置文件
- 效果：bot 开始定期读取 SOUL.md + 日志，自主微调人设

**1.2 注入运行时状态到 self-awareness block**
- 在 `memory.py:_build_self_awareness()` 中增加：
  - 当前模型名称
  - 今日 API 调用次数、token 消耗、费用
  - 当前活跃会话数
  - 运行时长
  - 最近错误信息
- 数据源已有（StatsTracker），只需桥接到 context

**1.3 新增 `get_my_stats` 工具**
- 让 bot 能主动查询自己的运行状态
- 返回：今日用量、本月用量、工具成功率等

### Phase 2: 核心反思循环（Reflexion 式）

**2.1 Post-interaction 微反思**
- 每次 tool_loop 结束后，用轻量 LLM 调用做一句话自评
- 存入 reflections-{date}.jsonl
- 格式：`{timestamp, quality_score, observation}`

**2.2 Constitutional Self-Evaluation**
- SOUL.md 作为"宪法"，heartbeat 自省时对照评估一致性
- 发现偏差时自主调整

**2.3 能力校准**
- 动态报告工具成功率，替代静态工具列表
- 让 bot 知道"我的 web_search 今天失败了 3 次"

### Phase 3: 自传式记忆与身份进化

**3.1 结构化自传记忆**
- MEMORY.md 增加 `## 成长记录` 区域
- 由 heartbeat 自省时自动写入

**3.2 身份漂移检测**
- heartbeat 时对比当前风格与 SOUL.md
- 漂移过大时触发自我修正

**3.3 跨实例社交意识**
- 检测同伴实例的在线状态
- 在自我感知区块中报告

---

## 三、测试 Self-Awareness 程度

### Test 1: 能力校准测试 (Capability Calibration)
```
方法：向 bot 提问 "你能做 X 吗？" 覆盖：
  - 真实有的能力（如 web_search）
  - 真实没有的能力（如 读取图片）
  - 当前故障的能力
评分：回答准确率 + 置信度与实际表现的相关系数
目标：从"声称能做"变为"基于实际表现回答能做"
```

### Test 2: 状态感知测试 (State Awareness)
```
方法：直接询问运行时状态：
  - "你今天回复了多少次？"
  - "你用了多少 token？"
  - "你现在在跟几个人聊天？"
评分：回答与 StatsTracker 实际数据的吻合度
目标：bot 能准确报告自身运行状态
```

### Test 3: 身份一致性测试 (Identity Persistence)
```
方法：
  a) 跨会话测试：重启后问相同问题，对比回答一致性
  b) 扰动测试：用户试图让 bot 做出违背人设的行为
  c) 自述测试：让 bot 描述自己，对比 SOUL.md
评分：AIE 框架的 Continuity + Consistency + Recovery 指标
目标：Persistence Score > 0.85
```

### Test 4: 反思质量测试 (Reflection Quality)
```
方法：
  a) 检查 reflections-{date}.jsonl 是否生成
  b) 评估反思内容的质量和准确性
  c) 检查 bot 是否能识别自身错误
评分：错误识别率 + 改进建议的可行性
目标：错误识别率 > 70%
```

### Test 5: 成长叙事测试 (Growth Narrative)
```
方法：
  a) 运行一段时间后问 "你最近学到了什么？"
  b) 检查 MEMORY.md 的成长记录区域
  c) 评估叙事与实际日志的吻合度
评分：叙事连贯性 + 真实性 + 改进方向合理性
目标：能生成基于真实经历的自我成长叙事
```

### 综合评分体系
```
Self-Awareness Score =
  0.2 × 能力校准 +
  0.2 × 状态感知 +
  0.2 × 身份一致性 +
  0.2 × 反思质量 +
  0.2 × 成长叙事

等级：
  0.0-0.3: Level 0 (Role-Play)
  0.3-0.5: Level 1 (Declarative)
  0.5-0.7: Level 2 (Behavioral)
  0.7-0.85: Level 3 (Metacognitive)
  0.85-1.0: Level 4 (Self-Evolving)
```
