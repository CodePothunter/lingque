# Session & Memory 架构审查报告 + 改进方案

## 一、发现的问题

### 问题 1：记忆架构缺乏 Local Memory 层（核心问题）

**现状：** 三层记忆架构中缺少「per-chat 长期记忆」层：

| 层级 | 内容 | 生命周期 | 问题 |
|------|------|----------|------|
| SOUL.md | 人格 | 永久 | OK |
| MEMORY.md | 全局记忆 | 永久 | **所有聊天共享，无法区分** |
| Daily Logs | 按 chat_id 标记 | **仅 2 天** | 过期后 chat-specific 上下文丢失 |
| Session History | 内存中消息列表 | 压缩后仅剩摘要 | **压缩信息流入 Daily Log → 2天后丢失** |

**人类类比：** 人跟不同的人聊天，脑中会有「关于这个人」的印象和记忆：
- 跟小明私聊 → 记得他喜欢什么、之前聊过什么话题
- 在群聊A → 记得群里的氛围、常聊的话题、各人的性格
- 在群聊B → 完全不同的上下文

**现在的问题：**
- `write_memory` 写入 MEMORY.md 是全局的，群聊A的信息会污染私聊的上下文
- Session 压缩时提取的记忆 → `append_daily()` → **2天后彻底消失**
- 没有 per-chat 的长期关系记忆

**代码位置：**
- `memory.py:31-62` (`build_context`) — 只注入全局 MEMORY.md
- `memory.py:170-175` (`append_daily`) — daily log 仅存 2 天
- `router.py:1147-1152` (`_compact_session`) — 提取物写入 daily log 而非持久存储

### 问题 2：群聊 Session 不执行 Compaction

**现状：**
- 私聊 `_flush_private` (router.py:395-396) 每次回复后检查 compaction
- 群聊 @mention (router.py:853-855) — **无 compaction 检查**
- 群聊主动介入 (router.py:1026-1028) — **无 compaction 检查**

**后果：** 群聊 session 消息列表会无限增长。当消息超过 50 条后，`get_messages()` 返回越来越多的历史消息，导致 API token 消耗急增，且可能超过 context window。

### 问题 3：Session Compaction 丢失关键上下文

**现状 (router.py:1147-1159)：**
1. 提取长期记忆 → 写入 daily log（2 天后过期）
2. 生成 2-3 句摘要 → 替代原始 40 条消息
3. 保留最近 10 条

**问题：**
- 摘要质量依赖单次 LLM 调用，容易遗漏重要上下文
- 提取物不会进入 MEMORY.md，只进了 daily log
- 2 天后，提取的记忆和摘要全部丢失
- 没有 accumulative 的效果——每次压缩都是独立的，不会参考之前的摘要

### 问题 4：消息顺序在 Debounce 中被混淆

**现状 (router.py:317-344)：**
```python
pending["texts"].append(text)
pending["message_id"] = message.message_id  # 只保留最后一条的 ID
pending["sender_name"] = sender_name        # 只保留最后一条的发送者
```

**问题：**
- 多条消息合并后，`message_id` 只保留最后一条 → 回复线程指向错误的消息
- `sender_name` 被覆盖 → 如果是同一用户快速发多条，还好；但理论上不同场景可能混淆
- 合并后的消息丢失了各自的时间戳，session 中只记录一个时间点

### 问题 5：save() 非原子写入

**现状 (session.py:109-114)：**
```python
def save(self) -> None:
    path = self.sessions_dir / "current.json"
    data = {cid: s.to_dict() for cid, s in self._sessions.items()}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
```

**问题：** 如果写入过程中进程崩溃，`current.json` 被截断，所有 session 丢失。应该先写临时文件再 rename。

---

## 二、改进方案

### 方案核心思想

引入 **Chat Memory（聊天记忆）** 层：每个 chat_id 拥有独立的持久化记忆文件，与全局 MEMORY.md 平行存在。

```
记忆架构：
┌─────────────────────────────────┐
│  SOUL.md (人格，永久，全局)       │
├─────────────────────────────────┤
│  MEMORY.md (全局记忆，永久)       │  ← 通用知识、跨聊天的事实
├─────────────────────────────────┤
│  chat_memories/{chat_id}.md     │  ← NEW: per-chat 长期记忆
│  (聊天记忆，永久，per-chat)       │
├─────────────────────────────────┤
│  memory/YYYY-MM-DD.md           │  ← 每日日志（2天有效）
│  (日志，短期，按 chat_id 标记)     │
├─────────────────────────────────┤
│  sessions/current.json          │  ← 活跃对话历史
│  (会话，runtime，per-chat)        │
└─────────────────────────────────┘
```

### 改动 1：新增 Chat Memory 持久化（memory.py）

新增文件结构 `~/.lq-{slug}/chat_memories/{chat_id}.md`

每个文件内容格式：
```markdown
# 聊天记忆

## 基本信息
- 类型: 私聊/群聊
- 对话对象: 小明
- 首次交互: 2026-01-15

## 关于此对话
- 用户偏好：简洁回复，不喜欢emoji
- 常聊话题：技术、日程安排

## 历史摘要
- 2026-02-10: 讨论了项目部署方案，用户选择了方案B
- 2026-02-12: 帮用户设置了每日提醒
```

**具体改动：**

1. `MemoryManager` 新增方法：
   - `read_chat_memory(chat_id: str) -> str` — 读取 per-chat 记忆
   - `update_chat_memory(chat_id: str, section: str, content: str) -> None` — 更新 per-chat 记忆中的分区
   - `append_chat_memory(chat_id: str, content: str) -> None` — 追加到 per-chat 记忆

2. `build_context(chat_id)` 中注入 `<chat_memory>` 段：
   ```python
   if chat_id:
       chat_mem = self.read_chat_memory(chat_id)
       if chat_mem:
           parts.append(f"<chat_memory>\n{chat_mem}\n</chat_memory>")
   ```

### 改动 2：新增 write_chat_memory 工具（router.py）

在 TOOLS 中新增：
```python
{
    "name": "write_chat_memory",
    "description": "将信息写入当前聊天窗口的专属记忆。用于记住与当前对话相关的信息，如对方的偏好、聊天历史中的要点等。与 write_memory（全局记忆）不同，chat_memory 只在当前聊天窗口中可见。",
    "input_schema": {
        "type": "object",
        "properties": {
            "section": {"type": "string", "description": "记忆分区名"},
            "content": {"type": "string", "description": "要记录的内容"}
        },
        "required": ["section", "content"]
    }
}
```

### 改动 3：Compaction 时写入 Chat Memory（router.py）

修改 `_compact_session`：
- 提取物不仅写入 daily log，还写入 `chat_memories/{chat_id}.md` 的「历史摘要」分区
- 这样即使 daily log 过期，per-chat 记忆仍然保留

### 改动 4：群聊 Session 也要 Compact（router.py）

在群聊 @mention 和群聊主动介入的回复后，增加 compaction 检查：
```python
if reply_text and self.session_mgr:
    session = self.session_mgr.get_or_create(chat_id)
    session.add_message("assistant", reply_text, sender_name="你")
    if session.should_compact():                    # ← 新增
        await self._compact_session(session)        # ← 新增
```

### 改动 5：System Prompt 中区分 Global vs Local 记忆指引

修改 `_flush_private` 和群聊处理中的 system prompt，增加指引：
```
- 使用 write_memory 记录跨聊天通用的信息（如用户的生日、公司信息）
- 使用 write_chat_memory 记录仅与当前对话相关的信息（如对方的偏好、聊天中的约定）
```

### 改动 6：Debounce 保留首条 message_id（router.py）

修改 debounce 逻辑，回复第一条消息而非最后一条：
```python
if pending:
    pending["texts"].append(text)
    # 不覆盖 message_id，保留首条消息的 ID 用于回复线程
    # pending["message_id"] = message.message_id  ← 移除
```

### 改动 7：原子化 Session 保存（session.py）

```python
def save(self) -> None:
    path = self.sessions_dir / "current.json"
    tmp = path.with_suffix(".tmp")
    data = {cid: s.to_dict() for cid, s in self._sessions.items()}
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)  # 原子替换
```

---

## 三、实施优先级

| 优先级 | 改动 | 原因 |
|--------|------|------|
| P0 | 改动 1+2+3: Chat Memory | 核心问题，直接影响拟人效果 |
| P0 | 改动 4: 群聊 Compaction | 无上限增长是 bug |
| P1 | 改动 5: Prompt 指引 | 引导 LLM 正确使用 local vs global 记忆 |
| P1 | 改动 6: Debounce message_id | 对话线程准确性 |
| P2 | 改动 7: 原子 save | 数据安全性 |
