# 自驱动好奇心引擎：实现细节

> 实现日期: 2026-02-15
> 基于设计方案 03-design-proposal.md

---

## 变更总览

| 文件 | 变更行数 | 变更类型 |
|------|---------|---------|
| `src/lq/prompts.py` | +77 | 4 个新模板常量 |
| `src/lq/memory.py` | +19 | CURIOSITY.md 白名单 + 主人身份注入 |
| `src/lq/config.py` | +4 | 新增 `curiosity_budget` 和 `owner_name` 字段 |
| `src/lq/gateway.py` | +113 | 好奇心探索引擎 + stats_provider 扩展 |
| `src/lq/router.py` | +202 | 信号采集 + 审批机制 + 主人自动发现 |
| `src/lq/cli.py` | +20 | `--owner` 参数 + CURIOSITY.md 模板生成 |
| `~/.lq-naiyou/CURIOSITY.md` | 新建 | 初始好奇心日志 |
| `~/.lq-nienie/CURIOSITY.md` | 新建 | 初始好奇心日志 |

---

## 一、Prompt 模板层 (`prompts.py`)

### 1.1 REFLECTION_WITH_CURIOSITY_PROMPT

扩展原有 `REFLECTION_PROMPT`，在质量自评之后追加好奇心信号检测。

```
格式：
[质量:好/中/差] 原因
[好奇:话题] 或 [好奇:无]
```

LLM 返回中的 `[好奇:...]` 标记被 `router.py` 的正则 `\[好奇[:：]\s*(.+?)\]` 解析，非「无」的话题写入当日信号日志。

### 1.2 CURIOSITY_EXPLORE_PROMPT

心跳探索 prompt，接收两个参数：

- `{signals}` — 当日好奇心信号列表（最多 20 条）
- `{curiosity_md}` — 当前 CURIOSITY.md 全文

Prompt 中内嵌安全约束：
- 敏感操作（SOUL.md 修改、bash、联网工具创建）需先 send_message 通知主人
- 不直接修改源代码
- 改进建议写入 CURIOSITY.md，不执行

### 1.3 OWNER_IDENTITY_TEMPLATE

注入到 `<self_awareness>` 标签中，格式：

```
### 你的主人
你的主人是 {owner_name}（chat_id: {owner_chat_id}）。
```

设计选择：不硬编码审批类别，让 LLM 自主判断什么需要确认。

### 1.4 CURIOSITY_INIT_TEMPLATE

CURIOSITY.md 的初始结构：

```markdown
# 好奇心日志
## 当前兴趣
## 正在探索
## 已完成的探索
## 暂时搁置
## 改进建议
```

---

## 二、上下文构建层 (`memory.py`)

### 2.1 EDITABLE_FILES 白名单

```python
EDITABLE_FILES = {"SOUL.md", "MEMORY.md", "HEARTBEAT.md", "CURIOSITY.md"}
```

同时更新 `_build_self_awareness()` 中的文件列表遍历，使 bot 在自我认知中看到 CURIOSITY.md。

### 2.2 主人身份注入

在 `_build_self_awareness()` 末尾，从 `stats_provider` 获取 `owner_name` + `owner_chat_id`，若均存在则追加 `OWNER_IDENTITY_TEMPLATE` 到 awareness 内容中。

注入位置在姐妹实例感知之后、`wrap_tag(TAG_SELF_AWARENESS, ...)` 之前。

### 2.3 工具描述更新

`TOOL_DESC_READ_SELF_FILE` 和 `TOOL_DESC_WRITE_SELF_FILE` 的可操作文件列表增加 `CURIOSITY.md`，以及 `SELF_AWARENESS_TEMPLATE` 的文件说明增加 CURIOSITY.md 条目。

---

## 三、配置层 (`config.py`)

### 3.1 新字段

```python
@dataclass
class LQConfig:
    curiosity_budget: float = 1.0   # 每日好奇心探索预算 (USD)
    owner_name: str = ""            # 主人的飞书名
```

`from_dict` 中增加解析：
```python
cfg.curiosity_budget = d.get("curiosity_budget", 1.0)
cfg.owner_name = d.get("owner_name", "")
```

`to_dict` 无需修改 — `asdict()` 自动处理新 dataclass 字段。

---

## 四、探索引擎 (`gateway.py`)

### 4.1 stats_provider 扩展

闭包 `_stats_provider()` 新增两个返回字段：

```python
"owner_name": owner_name,
"owner_chat_id": owner_chat_id,
```

`owner_name` 解析优先级：
1. `config.owner_name`（手动配置）
2. 从 `session_mgr` 的会话历史中推断（遍历 owner_chat_id 对应 session 的 user 消息，取第一个 `sender_name`）

### 4.2 _run_curiosity_exploration()

核心探索方法，在心跳回调中 `_run_heartbeat_tasks()` 之后调用。

**执行流程：**

```
心跳触发
  │
  ├─ 预算检查: today_cost > (cost_alert_daily - curiosity_budget) → 跳过
  │
  ├─ 读取信号: logs/curiosity-signals-{date}.jsonl (最近 20 条)
  │
  ├─ 读取 CURIOSITY.md (不存在则从模板创建)
  │
  ├─ 快速判断: 无信号 且 无当前兴趣 → 跳过
  │
  ├─ 构建 system prompt: build_context() + CURIOSITY_EXPLORE_PROMPT
  │
  ├─ 执行探索: router._reply_with_tool_loop() (已有 20 轮上限)
  │
  └─ 后处理:
     ├─ 写入日志
     └─ 对比探索前后 CURIOSITY.md，检测新增改进建议 → 通知主人
```

**预算控制逻辑（修正后）：**

```python
exploration_ceiling = config.cost_alert_daily - config.curiosity_budget
if today_cost > exploration_ceiling:
    return  # 跳过探索
```

含义：为好奇心探索保留 `curiosity_budget` 额度的空间。默认 `cost_alert_daily=5.0, curiosity_budget=1.0`，即总花费超过 $4.0 时停止探索。

**改进建议检测（鲁棒实现）：**

```python
# 对比探索前后内容差异
if new_curiosity != old_curiosity and "改进建议" in new_curiosity:
    # 用正则提取，兼容 ## 改进建议 / ##改进建议 等格式
    m = re.search(r"##\s*改进建议\s*\n(.*?)(?:\n##|\Z)", new_curiosity, re.DOTALL)
```

---

## 五、信号采集 + 审批 (`router.py`)

### 5.1 反思好奇心检测

`_reflect_on_reply()` 改用 `REFLECTION_WITH_CURIOSITY_PROMPT`，`max_tokens` 从 100 提升到 150 以容纳好奇心输出。

新增 `_extract_curiosity_from_reflection()`：用正则 `\[好奇[:：]\s*(.+?)\]` 提取话题。

### 5.2 好奇心信号日志

文件格式：`{workspace}/logs/curiosity-signals-{date}.jsonl`

每条记录：
```json
{"ts": 1739587200.0, "topic": "话题内容", "source": "私聊反思|群聊旁听", "chat_id": "oc_xxx"}
```

**去重机制：** 写入前读取当日所有信号，若已有前 20 字匹配的话题则跳过。

### 5.3 群聊被动信号

`_extract_group_curiosity()` 在 `_evaluate_buffer()` 的「不介入」分支触发。

**关键设计选择：** 不额外调用 LLM——仅通过关键词匹配从已有消息中提取。关键词收紧为动作类短语（"怎么做"、"怎么实现"、"有没有办法"、"能不能"、"如何"），要求消息长度 ≥15 字，每次评估最多产生 1 个信号。

### 5.4 审批机制

三个方法构成完整审批流程：

```
_request_owner_approval(action_desc, callback_id)
  │  构建 build_confirm_card + 发送到 owner_chat_id
  │  写入 logs/pending-approvals.jsonl (status: pending)
  │
  ▼
_handle_card_action() 扩展
  │  检测 value.type == "approval"
  │  调用 _update_approval_status(id, "approved"|"rejected")
  │
  ▼
_check_approval(callback_id) → "approved" | "rejected" | None
```

审批记录格式：
```json
{"id": "uuid", "ts": 1739587200.0, "action": "描述", "status": "pending|approved|rejected"}
```

注意：当前实现中审批机制主要作为基础设施提供。在心跳探索中，安全约束通过 prompt 引导 LLM 自行判断是否先 send_message 主人，而非强制经过卡片审批流程。这是设计方案中「由模型自主判断什么需要审批」原则的体现。

### 5.5 主人身份自动发现

`_try_discover_owner()` 在 `_flush_private()` 中首次处理私聊消息时触发。

```
收到私聊消息
  │
  ├─ config.feishu.owner_chat_id 已有 → 跳过
  │
  ├─ config.owner_name 已设置 → 只匹配该名字的用户
  │
  └─ config.owner_name 为空 → 首个私聊用户自动成为主人
     │
     └─ 写入 config.json + 刷新自我认知缓存
```

---

## 六、CLI 扩展 (`cli.py`)

### 6.1 --owner 参数

```bash
uv run lq init --name 奶油 --from-env .env --owner 张三
```

留空则首个私聊用户自动成为主人。init 完成后输出安全提示。

### 6.2 CURIOSITY.md 自动创建

init 时从 `CURIOSITY_INIT_TEMPLATE` 生成初始文件。

---

## 七、数据流总图

```
用户私聊 ──→ _flush_private ──→ _reply_with_tool_loop ──→ _reflect_on_reply
                │                                              │
                │ _try_discover_owner                          │ [好奇:话题]
                │ (首次绑定主人)                                  ▼
                │                                    _append_curiosity_signal
                │                                              │
群聊消息 ──→ _evaluate_buffer ──→ 不介入 ──→ _extract_group_curiosity
                                                               │
                                                               ▼
                                              logs/curiosity-signals-{date}.jsonl
                                                               │
                                                               │ (心跳触发)
                                                               ▼
                                              _run_curiosity_exploration
                                                    │
                                           ┌────────┴────────┐
                                           │                 │
                                      探索执行           改进建议检测
                                  (tool loop)         (diff CURIOSITY.md)
                                           │                 │
                                           ▼                 ▼
                                      CURIOSITY.md     send_message 主人
                                      MEMORY.md
```

---

## 八、安全考量

1. **主人身份绑定** — `--owner` 在 init 时指定飞书名，限制自动发现范围。未指定时首个私聊用户成为主人，README 中需警示。

2. **探索安全** — 不硬编码审批类别，通过 prompt 引导 LLM 自主判断敏感操作。探索 prompt 明确禁止直接修改源代码。

3. **成本控制** — 三层防护：
   - 心跳间隔（默认 1 小时）限制探索频率
   - 每日预算检查（`cost_alert_daily - curiosity_budget`）
   - tool loop 20 轮上限

4. **信号防 spam** — 去重（前 20 字匹配）+ 收紧群聊关键词 + 每次评估最多 1 个信号。
