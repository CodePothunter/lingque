"""Session management, compaction, and daily log prompt constants."""

from __future__ import annotations


# =====================================================================
# Session: Summary Injection
# =====================================================================

# {summary} -> compacted summary text
CONTEXT_SUMMARY_USER = (
    "以上是较早对话的摘要。请更关注下面最近的消息，"
    "但可以参考摘要中的事实和决定。"
)
CONTEXT_SUMMARY_ACK = "好的，我已了解之前的对话背景。"


# =====================================================================
# Compaction Prompts
# =====================================================================

# {conversation} -> formatted old messages
FLUSH_BEFORE_COMPACTION = (
    "请从以下对话中提取需要长期记住的重要信息。\n"
    "重点关注：\n"
    "- 用户明确要求记住的内容\n"
    "- 用户偏好和习惯\n"
    "- 重要的事实、决定和约定\n"
    "- 待办事项和承诺\n"
    "- 工具调用中的关键事实（如搜索到的结果、创建的日程、写入的记忆内容等）\n\n"
    "仅输出需要记住的条目，每条一行，格式为 `- 内容`。如果没有需要记住的，输出「无」。\n\n"
    "对话内容：\n{conversation}"
)

FLUSH_NO_RESULT = "无"

# {old_messages_formatted} -> formatted old messages for summary
COMPACTION_SUMMARY_PROMPT = (
    "请总结以下对话的关键内容，包括：\n"
    "1. 讨论的主要话题\n"
    "2. 做出的决定或达成的共识\n"
    "3. 使用了哪些工具完成了什么任务\n"
    "4. 用户表达的偏好或需求\n"
    "用 3-5 句话概括，保留具体细节（如日期、名字、数字）。\n\n"
    "{old_messages_formatted}"
)


# =====================================================================
# Compaction: Chat Memory Extraction Header
# =====================================================================

# {date}
COMPACTION_MEMORY_HEADER = "\n## 记忆提取 ({date})\n{extracted}\n"
COMPACTION_DAILY_HEADER = "### 会话记忆提取\n{extracted}\n"


# =====================================================================
# Flush Before Compaction: Message Role Labels
# =====================================================================

FLUSH_ROLE_TOOL_CALL = "[tool_call] 调用工具 {tool_name}，参数: {input_preview}"
FLUSH_ROLE_TOOL_RESULT = "[tool_result] ({tool_name}) {content_preview}"
FLUSH_ROLE_DEFAULT = "[{role}] {content}"


# =====================================================================
# Daily Log Formatting
# =====================================================================

# {sender_name}, {text_preview}, {status}
DAILY_LOG_PRIVATE = "- 私聊 [{sender_name}]: {text_preview}... → {status}\n"
DAILY_LOG_STATUS_OK = "已回复"
DAILY_LOG_STATUS_FAIL = "回复失败"
