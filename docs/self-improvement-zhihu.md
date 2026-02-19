# 如何让一个 AI 助手在运行中「进化」自己的源代码？LingQue 自我改进框架深度解析

> 知乎专栏 · AI 工程实践

---

## 前言：一个不太一样的思路

大多数 AI 应用的迭代路径是：开发者写代码 → 部署 → 收集反馈 → 手动改代码 → 重新部署。

LingQue（灵雀）做了一件不太一样的事：**让 AI 助手在运行期间自主分析自身代码、提出改进方案、修改源代码、验证安全性——如果改挂了，自动回滚**。

这不是 AutoGPT 那种"让 LLM 写 Python 脚本然后 exec"的玩法，而是一个有严格安全边界的、基于 Git checkpoint 的工程化方案。本文会从心跳调度、决策流程、安全机制三个层面详细拆解。

---

## 一、整体架构：三层循环

LingQue 的自我提升不是一个单一模块，而是三个不同时间尺度的循环嵌套运转：

```
┌──────────────────────────────────────────────────────────┐
│  第一层：对话级反思（秒级）                                  │
│  每次私聊回复后 → 150 token 的自我评估 → 提取好奇心信号       │
├──────────────────────────────────────────────────────────┤
│  第二层：心跳级自主行动（小时级，每 3600 秒）                  │
│  收集信号 → LLM 决策 → 探索外部知识 或 修改自身代码           │
├──────────────────────────────────────────────────────────┤
│  第三层：启动级安全验证（重启时）                              │
│  检测上次进化是否导致崩溃 → 自动回滚 → 记录失败教训            │
└──────────────────────────────────────────────────────────┘
```

---

## 二、第一层：对话级反思

### 触发时机

每次私聊回复完成后，`router/private.py` 中的 `_reflect_on_reply()` 方法会被调用。

### 做了什么

用一个轻量级 LLM 调用（最多 150 token）对刚才的回复做自我评估，输出格式：

```
[质量:好] 准确回答了用户的技术问题
[好奇:Rust 异步运行时的调度策略]
```

这里有两个信息被提取出来：
- **质量评估**：写入 `logs/reflections-{today}.jsonl`，每天积累，在自主行动周期作为上下文参考
- **好奇心信号**：通过正则 `\[好奇[:：]\s*(.+?)\]` 提取话题，写入 `logs/curiosity-signals-{today}.jsonl`

群聊也有类似机制但更轻量——通过启发式规则检测最近 5 条消息中是否有"怎么做""能不能"等动作词，不调用 LLM，避免噪声。

### 设计意图

这一层的核心价值不在于反思本身，而在于**持续产生信号**。这些信号会在小时级的自主行动周期中被聚合，成为 LLM 决策的输入。

---

## 三、第二层：心跳与自主行动周期

这是整个自我提升机制的核心。

### 心跳调度

心跳由 `heartbeat.py` 中的 `HeartbeatScheduler` 驱动：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| 间隔 | 3600 秒（1 小时） | 可配置，写在 config.json |
| 活跃时段 | 8:00–23:00 CST | 凌晨不触发，避免深夜跑费 |
| 日任务 | 每天第一次心跳 | 发日报/早安问候 |
| 周任务 | 每周一第一次心跳 | 周度回顾 |

每次心跳触发三件事：

1. **执行 HEARTBEAT.md 定义的周期任务**（偏移检测、习惯维护等）
2. **日报/问候**（仅每天第一次）
3. **自主行动周期**（每次都跑）

### 自主行动周期的完整流程

```python
async def _run_autonomous_cycle(self, router):
    # 1. 预算检查
    #    curiosity_budget($1.0) + evolution_budget($2.0) = $3.0/天
    #    如果今日花费已超过 cost_alert_daily - autonomous_budget，跳过

    # 2. 信号收集
    signals = 最近 20 条好奇心信号
    curiosity_md = CURIOSITY.md 全文
    evolution_md = EVOLUTION.md 全文
    source_summary = 源代码目录树 + 文件大小
    git_log = 最近 10 条 git commit
    reflections = 今日最近 15 条反思
    tool_stats = 工具使用成功率 + 最近错误

    # 3. 前置条件检查
    #    如果没有任何信号、没有好奇心、没有待办改进 → 跳过

    # 4. LLM 决策
    #    注入所有上下文到 CURIOSITY_EXPLORE_PROMPT
    #    LLM 自主选择：探索 or 进化 or "无"

    # 5. 执行 & 变更检测
    #    如果 EVOLUTION.md 有变化 → 记录进化尝试
    #    如果 CURIOSITY.md 有改进建议 → 通知主人

    # 6. 日志压缩
    #    如果 EVOLUTION.md 条目过多 → 异步压缩归档
```

### LLM 看到的决策上下文

prompt 里给了 LLM 两个方向：

**方向一：探索与学习**
- 用 `web_search`、`web_fetch` 研究一个好奇的话题
- 成果记入 CURIOSITY.md
- 如果发现对用户有用的能力，可以用 `create_custom_tool` 创建新工具

**方向二：自我进化**（有次数限制，默认 3 次/天）
1. 用 `read_file` 读源代码，结合反思和工具统计找改进点
2. 规划改进（优先级：修 bug > 补功能 > 优化 > 重构）
3. 用 `run_claude_code` 在源码仓库里执行修改
4. 用 `run_bash` 验证 `from lq.gateway import AssistantGateway` 能否通过
5. 用 `write_self_file` 更新 EVOLUTION.md，通知主人

**决策优先级规则**：
- 如果 EVOLUTION.md 有待办改进且今日还有改进次数 → 优先进化
- 如果次数用完 → 只能探索
- 两边都没什么可做 → 输出"无"

### 一个真实的进化示例

假设 LLM 在反思中多次注意到 `web_search` 工具出错率偏高，工具统计里也印证了这一点。在下一个心跳周期：

1. LLM 阅读了 `router/web_tools.py`，发现超时设置过短（10 秒）
2. 决定把超时改成 30 秒并加一层重试
3. 用 `run_claude_code` 提交修改，commit 信息 `🧬【进化】：增加 web_search 超时时间并补充重试`
4. 验证通过 → 更新 EVOLUTION.md：待办 → 已完成
5. 下次重启生效

---

## 四、第三层：启动安全验证

进化最危险的部分是**改了代码以后系统可能起不来**。LingQue 用 Git checkpoint 解决这个问题。

### 时序图

```
进化前                   进化后                    下次启动
  │                        │                        │
  ├─ git rev-parse HEAD    │                        │
  ├─ 保存 checkpoint.json  │                        │
  │                        ├─ 修改代码               │
  │                        ├─ git commit             │
  │                        │                        ├─ 读取 checkpoint
  │                        │                        ├─ 检查 .clean-shutdown
  │                        │                        │
  │                        │                  ┌─────┤ 正常关闭？
  │                        │                  │ YES  ├─ 进化成功，清除 checkpoint
  │                        │                  │ NO   ├─ 跑健康检查（4 个 import）
  │                        │                  │      │   ├─ 全部通过 → 进化OK
  │                        │                  │      │   └─ 任一失败 → git reset --hard
  │                        │                  │      │               记录失败到 EVOLUTION.md
  │                        │                  │      │               清除 checkpoint
```

### 健康检查的四个模块

```python
checks = [
    "from lq.gateway import AssistantGateway",
    "from lq.router.core import MessageRouter",
    "from lq.memory import MemoryManager",
    "from lq.session import SessionManager",
]
# 每个在子进程中运行，15 秒超时
```

这四个 import 覆盖了系统的核心链路：如果任何一个挂了，说明进化引入了致命错误。

### 回滚后的记录

回滚不是静默发生的。`_rollback()` 会：
1. 用 `git log checkpoint..HEAD` 列出所有被回滚的 commit
2. 将失败信息写入 EVOLUTION.md 的「失败记录」section
3. 下次自主周期时，LLM 能看到这些失败教训，避免重蹈覆辙

---

## 五、日志压缩：防止上下文膨胀

EVOLUTION.md 是长期运行的日志文件，如果不压缩会无限膨胀、撑爆 prompt context。

压缩规则：

| Section | 阈值 | 保留最近 | 压缩为 |
|---------|------|---------|--------|
| 已完成 | > 10 条 | 5 条 | 📦 历史改进归档 |
| 失败记录 | > 5 条 | 3 条 | ⚠️ 历史失败教训 |

压缩方式是用 LLM 做摘要：提炼改动要点、保留 commit hash（可用 `git show` 回溯）、提炼失败模式。这样即使旧记录被压缩，LLM 仍然能通过 `run_bash` + `git show <hash>` 查看具体 diff。

---

## 六、安全边界设计

自我修改代码听起来很危险，LingQue 做了多层防护：

### 频次控制
- 进化次数：默认 3 次/天，可配置
- 自主行为预算：curiosity $1.0 + evolution $2.0 = $3.0/天
- 活跃时段限制：凌晨 23:00–08:00 不触发

### 代码安全
- 只改框架代码，不改 config.json 和实例文件
- 要求向后兼容，不删已有功能
- 敏感操作（改 SOUL.md 等）需要先通知主人

### 运行时安全
- 进化前保存 Git checkpoint
- 修改后立即做 import 验证
- 崩溃后下次启动自动回滚 + 记录教训
- 自定义工具有 AST 静态分析，禁止 import `subprocess`、`shutil`、`ctypes`、`signal`、`multiprocessing`

### 信息溯源
- 所有进化 commit 使用 `🧬【进化】：` 前缀，可以在 git log 里一眼找到
- EVOLUTION.md 记录每次尝试的时间、改动内容、commit hash
- 失败教训永久保留（压缩但不丢弃）

---

## 七、数据流全景

把上面所有部分串起来，完整的数据流如下：

```
用户对话
  │
  ├─ 私聊回复 ──→ 反思（150 token）──→ 质量评估 ──→ reflections.jsonl
  │                                  └──→ 好奇心话题 ──→ curiosity-signals.jsonl
  │
  ├─ 群聊观察 ──→ 启发式提取 ──→ curiosity-signals.jsonl
  │
  └─ 工具调用 ──→ 成功/失败统计 ──→ stats.jsonl
                                            │
                                            ▼
  每 3600 秒心跳 ─────────────────→ 信号聚合 ─────→ LLM 决策
                                                      │
                                         ┌────────────┼────────────┐
                                         ▼            ▼            ▼
                                       "无"        探索学习      自我进化
                                                     │            │
                                                     ▼            ▼
                                              CURIOSITY.md   checkpoint
                                              新工具创建        │
                                                               ▼
                                                          修改源代码
                                                          git commit
                                                               │
                                                               ▼
                                                          import 验证
                                                         ┌─────┴─────┐
                                                         ▼           ▼
                                                       通过        失败
                                                         │           │
                                                         ▼           ▼
                                                    EVOLUTION.md  git checkout .
                                                    记录已完成     回滚
                                                                     │
                                                                     ▼
                                                              下次重启验证
                                                              如需回滚到 checkpoint
```

---

## 八、配置参考

关键配置项及默认值（`config.json`）：

```json
{
  "heartbeat_interval": 3600,
  "active_hours": [8, 23],
  "cost_alert_daily": 5.0,
  "curiosity_budget": 1.0,
  "evolution_max_daily": 3,
  "evolution_budget": 2.0
}
```

---

## 结语

LingQue 的自我改进框架本质上是一个**有安全边界的 AI-in-the-loop DevOps 循环**。它不是让 AI 自由地修改一切，而是：

1. 通过持续反思产生改进信号（不是凭空想象）
2. 通过预算和频次控制约束行为边界
3. 通过 Git checkpoint + 健康检查保证可回滚
4. 通过压缩归档保持长期记忆不膨胀

它真正有意思的地方在于：这些失败教训不会被丢弃，而是被提炼后保留。LLM 下次尝试进化时能看到"上次改 session 压缩逻辑的时候因为 import 循环挂了"，从而避免同类错误。

这让整个系统形成了一个**可以从失败中学习的进化循环**——虽然每次进化的幅度很小，但方向是持续向好的。

---

*本文基于 LingQue 开源代码分析，项目地址见 GitHub。如有技术讨论欢迎评论区交流。*
