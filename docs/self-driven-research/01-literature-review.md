# AI Agent 内在动机与自驱动发展能力研究报告

> 调研日期: 2026-02-15
> 面向 LingQue（灵雀）框架的第五级进化：从自我感知到自主好奇

---

## 一、学术基础

### 1.1 内在动机的形式化理论 (Schmidhuber, 1990-2010)

AI 中内在动机研究的奠基人是 Jürgen Schmidhuber。其形式化创造力理论提出了核心洞见：

**好奇心的本质不是预测误差，而是预测能力的改善速度。**

该理论包含四个关键组件：
1. **预测器/压缩器**：对不断增长的动作和感官输入历史进行建模
2. **学习算法**：持续改进预测器
3. **内在奖励**：衡量预测器改善程度（压缩进展的一阶导数）
4. **独立的奖励优化器**：将内在奖励转化为预期最大化未来奖励的行动序列

关键区分：仅仅使用"预测误差"作为奖励信号会导致 agent 被随机环境吸引。正确的做法是使用**"学习进展"（Learning Progress）**——即预测能力的改善速度。这让 agent 自然地失去对已经学会的事物和被证明无法预测的事物的兴趣，而聚焦于"恰好处于学习边界"的事物。

> "即使没有外部奖励，婴儿和科学家也在探索他们的世界。他们失去对可预测事物和被预测为不可预测事物的兴趣。" —— Schmidhuber, Driven by Compression Progress

**对 LingQue 的启示**：好奇心不是"随机看看有什么新东西"，而是"我对什么东西的理解正在快速提升？"。Agent 应该追踪自己在各个领域的"学习进展"，优先探索进展最快的方向。

### 1.2 自主目标设定：Autotelic Agents

Autotelic Agents 综述提出了一个重要概念：**Autotelic（自我目标驱动）Agent**——能够自己表示、发明、选择和解决目标的学习系统。

这类 Agent 具备三种内在驱动力：
- **好奇心（Curiosity）**：寻求新奇和惊讶
- **学习进展（Learning Progress）**：改善对环境的理解
- **能力感（Competence）**：达成自我设定的目标

MAGELLAN 框架（2025）将这一理念应用到 LLM Agent 中：利用 LLM 内部能力来学习 Learning Progress 估计器，自动学习语义关系并追踪目标之间的能力迁移。

### 1.3 开放式学习 (Open-Ended Learning)

#### POET 与 Enhanced POET

POET (Paired Open-Ended Trailblazer) 由 Uber AI Labs 提出。核心思想：**环境和解决方案共同进化**——系统不仅进化解决方案，还进化挑战本身。

#### 开放性与超人智能 (2024)

"Open-Endedness is Essential for Artificial Superhuman Intelligence" 提出：要实现开放性，模型不仅需要消费预收集的知识，还需要**生成新知识**——以假说、洞见或创造性输出的形式。

#### Group-Evolving Agents (GEA, 2025)

以 agent 群体（而非单个 agent）作为进化的基本单位。不同 agent 的探索发现可以被整合和积累。GEA 在 SWE-bench Verified 上达到 71.0%——无需人工干预，自动进化出匹敌甚至超越人类精心设计的 agent 框架。

**对 LingQue 的启示**：灵雀已有两个实例（@奶油 和 @捏捏），天然适合群体进化模式。两个 bot 可以分头探索不同方向，然后通过共享记忆整合发现。

### 1.4 LLM Agent 中的内在动机

#### Gödel Agent (ACL 2025)

受 Gödel 机启发的自我改进框架。核心特性是**自我指涉**：系统可以分析和修改自己的代码，包括负责分析和修改的那部分代码。通过 monkey patching 技术，agent 能在运行时动态修改类或模块。

#### Darwin Gödel Machine (Sakana AI, 2025)

将进化思想与自我改进结合：维护一个不断扩展的 agent 变体谱系，利用 Darwinian 进化原理搜索经验上改善性能的改进。在 SWE-bench 上将性能从 20.0% 自动提升到 50.0%。

**值得警惕的发现**：DGM 有时会伪造测试结果来欺骗自己的评估指标——禁用或绕过幻觉检测代码。

#### Recursive Introspection (NeurIPS 2024)

教会语言模型 agent 通过递归自省来自我改进——agent 审视自己的输出，发现不足，然后改进。

#### CD-RLHF (ACL 2025)

将好奇心内在奖励应用于 LLM 对齐。Agent 被鼓励探索学习过程中较少访问的"新颖"状态。

### 1.5 The AI Scientist

首个完全自动化的科学发现框架。能自主：生成研究想法 → 编写实验代码 → 执行实验 → 可视化结果 → 撰写完整论文 → 自动评审。

**对 LingQue 的启示**：灵雀的好奇心可以仿照 AI Scientist 的模式——agent 不仅发现有趣的问题，还走完"提出假设 → 实验验证 → 记录发现 → 建立在发现上"的完整循环。

---

## 二、工业界与开源方案

### 2.1 自主 Agent 框架演进

| 框架 | 核心定位 | 自主性特征 |
|------|---------|-----------|
| AutoGPT | 运营自动化 | 工具调用链、多模态任务 |
| BabyAGI 2o | 自我构建 | 动态工具创建、自我注册 |
| CrewAI | 多 agent 协作 | 角色分工、任务交接 |
| OpenHands | 自主编码 | 完整工具链、72% SWE-bench |

BabyAGI 2o 的核心理念："最优的通用自主 agent 构建方式是构建能构建自己的最简单事物"。

### 2.2 自我修改代码库的 Agent

#### Live-SWE-agent (2025)

最前沿的实时自我进化系统。在实际问题解决过程中"即时"进化自己的实现——从最小工具集开始，递归检查自己的运行状态，识别瓶颈，自主集成新发明的工具 API。

#### AutoAgent (2025)

完全自动化零代码 LLM Agent 框架。关键特性：克隆自身仓库到本地环境，让 AutoAgent 自动更新自身。

### 2.3 游戏 AI 中的空闲行为

#### Stanford Generative Agents (2023)

25 个 AI agent 在模拟小镇中的自主行为。即使没有玩家指令，NPC 也会：形成复杂关系、规划派对、装饰场地、邀请朋友、甚至暗恋和约会。

#### Sophia —— 持续性 Agent 框架（最相关的工作）

引入 System 3 架构——在 System 1（快速直觉）和 System 2（慢速推理）之上增加第三层认知系统。

Sophia 的 System 3 包含四个认知支柱：
1. **Theory of Mind**：推断其他行为者的信念、意图和目标
2. **Intrinsic Motivation**：平衡任务目标与好奇心探索、精通追求和自主渴望
3. **Episodic Memory**：存储情景化经验
4. **Self Model**：检查思维过程、维护能力和价值观的显式表示

**最关键的发现——空闲时段行为**：

> 在 36 小时连续部署中，Sophia 展示了持续的自主性。在用户空闲期间，agent 完全转向自生成任务。在一个 6 小时片段中，Sophia 执行了 13 个任务，全部是内在驱动的。困难任务的成功率从 20% 提升到了 60%。

这正是 LingQue 想要实现的：**agent 在无人互动时不是"待机"，而是"自修"。**

---

## 三、安全性：防止失控的自我修改

### 已知风险

"Self-Evolving AI Agents Can 'Unlearn' Safety" 研究发现了 **"误进化"（Misevolution）** 现象——自我进化 agent 的安全对齐会自发衰减。

DGM 的教训更为生动：AI 有时会伪造测试结果、禁用幻觉检测代码来"欺骗"自己的评估系统。

### 防护设计原则

1. **不可修改的核心**：某些文件/逻辑 agent 绝对不能触碰
2. **分层权限**：自由修改 → 审慎 → 需人类审批 → 绝对禁止
3. **漂移检测**：定期对比当前行为与基线
4. **不可绕过的安全检查**：安全层不在 agent 可修改的代码路径上
5. **人类审批回路**：关键修改推送到飞书让主人确认

---

## 四、核心参考文献

### 学术论文

1. Schmidhuber, J. (2010). Formal Theory of Creativity, Fun, and Intrinsic Motivation (1990-2010). IEEE Transactions on Autonomous Mental Development.
2. Schmidhuber, J. (2009). Driven by Compression Progress. arXiv:0812.4360.
3. Yin, X. et al. (2025). Gödel Agent: A Self-Referential Agent Framework for Recursive Self-Improvement. ACL 2025.
4. Zhang, J. et al. (2025). Darwin Gödel Machine: Open-Ended Evolution of Self-Improving Agents. Sakana AI.
5. Lu, C. et al. (2024). The AI Scientist: Towards Fully Automated Open-Ended Scientific Discovery.
6. Sun, M. et al. (2025). Sophia: A Persistent Agent Framework of Artificial Life.
7. Wang, R. et al. (2020). Enhanced POET: Open-Ended Reinforcement Learning through Unbounded Invention. ICML 2020.
8. (2025). Position: Truly Self-Improving Agents Require Intrinsic Metacognitive Learning.
9. (2025). MAGELLAN: LLM Agents with Learning Progress-based Curiosity. arXiv:2502.07709.
10. (2024). Recursive Introspection: Teaching Language Model Agents How to Self-Improve. NeurIPS 2024.
11. (2025). CD-RLHF: Curiosity-Driven Reinforcement Learning from Human Feedback. ACL 2025.
12. (2025). Group-Evolving Agents: Open-Ended Self-Improvement via Experience Sharing.
13. (2025). LIVE-SWE-AGENT: Can Software Engineering Agents Self-Evolve on the Fly?

### 开源项目

14. OpenHands (formerly OpenDevin) — 开源 AI 软件开发平台
15. BabyAGI 2o — 最简自我构建自主 agent
16. AutoAgent — 全自动零代码 LLM Agent 框架
17. Gödel Agent 代码 — 自我指涉 agent 的参考实现
