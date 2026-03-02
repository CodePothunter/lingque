"""User-facing messages, result strings, error strings, and UI constants."""

from __future__ import annotations


# =====================================================================
# UI / User-Facing Messages
# =====================================================================

NON_TEXT_REPLY_PRIVATE = "目前只能处理文字和图片消息，文件、语音什么的还处理不了。有事直接打字或发图给我就好。"
NON_TEXT_REPLY_GROUP = "目前只能处理文字和图片消息，文件、语音什么的还处理不了。有事打字或发图说就好。"
EMPTY_AT_FALLBACK = "（@了我但没说具体内容）"


# =====================================================================
# Tool Execution Result Messages
# =====================================================================

RESULT_GLOBAL_MEMORY_WRITTEN = "已写入全局记忆"
RESULT_CHAT_MEMORY_WRITTEN = "已写入当前聊天记忆"
RESULT_CARD_SENT = "卡片已发送"
RESULT_FILE_EMPTY = "(文件为空或不存在)"
RESULT_FILE_UPDATED = "{filename} 已更新"
RESULT_SEND_FAILED = "消息发送失败"
RESULT_SCHEDULE_OK = "已计划在 {send_at} 发送消息"
RESULT_FILE_WRITTEN = "已写入文件: {path} ({size} 字节)"

# Error messages
ERR_MODULE_NOT_LOADED = "{module}未加载"
ERR_CALENDAR_NOT_LOADED = "日历模块未加载"
ERR_TOOL_REGISTRY_NOT_LOADED = "工具注册表未加载"
ERR_CC_NOT_LOADED = "Claude Code 执行器未加载"
ERR_BASH_NOT_LOADED = "Bash 执行器未加载"
ERR_UNKNOWN_TOOL = "未知工具: {name}"
ERR_TIME_FORMAT_INVALID = "时间格式无效: {value}，请使用 ISO 8601 格式"
ERR_TIME_PAST = "计划时间已过去"
ERR_FILE_NOT_ALLOWED_READ = "不允许读取 {filename}，可读文件: {allowed}"
ERR_FILE_NOT_ALLOWED_WRITE = "不允许写入 {filename}，可写文件: {allowed}"
ERR_CODE_VALIDATION_OK = "代码校验通过"
ERR_FILE_NOT_FOUND = "文件不存在: {path}"
ERR_FILE_READ_FAILED = "文件读取失败: {error}"
ERR_FILE_WRITE_FAILED = "文件写入失败: {error}"


# =====================================================================
# Action Preamble Detection
# =====================================================================

PREAMBLE_STARTS = (
    "好的，我", "好，我", "我来", "稍等", "马上",
    "让我", "我去", "好的，让我", "好，让我",
)

ACTION_NUDGE = "继续，直接调用工具即可。"

TOOL_USE_TRUNCATED_NUDGE = (
    "你的上一次工具调用似乎不完整（被截断了）。请重新调用工具，确保参数完整。"
)

FAKE_TOOL_CALL_NUDGE = (
    "你刚才把工具调用写成了文本，而不是真正调用工具。"
    "请通过 API 的 tool_use 机制实际调用工具，不要用文字描述工具调用。"
    "直接调用工具即可，不需要任何解释。"
)


# =====================================================================
# Sender Name Labels  (used in session history)
# =====================================================================

SENDER_SELF = "你"
SENDER_UNKNOWN = "未知"
SENDER_GROUP = "群聊"


# =====================================================================
# Tool Status Labels  (used in self-awareness listing)
# =====================================================================

TOOL_STATUS_ENABLED = "启用"
TOOL_STATUS_DISABLED = "禁用"


# =====================================================================
# Intent Detection Labels
# =====================================================================

TOOLS_CALLED_NONE = "无"
EVIDENCE_LLM = "LLM 判断"


# =====================================================================
# Bot Poll Judgment
# =====================================================================

# {bot_name}
BOT_POLL_AT_REASON = "被其他 bot 以文本方式 @{bot_name}"
