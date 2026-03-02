"""Intent detection and subagent extraction prompt constants."""

from __future__ import annotations


# =====================================================================
# Intent Detection Prompts  (intent.py)
# =====================================================================

RECOVERABLE_TOOL_DESCRIPTIONS = {
    "write_memory": "用户要求记住某件事，但助手没有调用 write_memory 工具",
    "schedule_message": "用户要求定时提醒，但助手没有调用 schedule_message 工具",
    "calendar_create_event": "用户要求创建日程/会议，但助手没有调用 calendar_create_event 工具",
}

# {user_message}, {llm_response}, {tools_called}, {tool_desc}
INTENT_DETECT_PROMPT = (
    "判断以下对话中，用户是否明确要求执行某个操作，但助手的回复中没有实际执行。\n\n"
    "用户消息：{user_message}\n"
    "助手回复：{llm_response}\n"
    "助手已调用的工具：{tools_called}\n\n"
    "可能遗漏的操作：\n{tool_desc}\n\n"
    "注意：\n"
    "- 只有用户**明确要求**执行的操作才算遗漏（如「记住我的生日」「5分钟后提醒我」）\n"
    "- 用户在**描述**、**询问**、**闲聊**时提到「记住」「提醒」等词不算遗漏\n"
    "- 「你记住了吗」「这么快就记住了」等陈述/疑问句不是指令\n"
    "- 如果助手已经通过工具完成了操作，不算遗漏\n\n"
    '输出 JSON：{{"missed": [{{"tool": "工具名"}}]}} 或 {{"missed": []}}\n'
    "只输出 JSON。"
)


# =====================================================================
# SubAgent Extraction Prompts  (subagent.py)
# =====================================================================

SUBAGENT_SYSTEM = (
    "你是一个参数提取器。根据用户消息和指令，提取结构化参数并以 JSON 格式输出。"
    "严格只输出 JSON 对象，不要包含任何其他文字、解释或 markdown 格式。"
)

# =====================================================================
# SubAgent Context Labels
# =====================================================================

# {message}
SUBAGENT_CONTEXT_USER = "用户消息：{message}"
# {reply}
SUBAGENT_CONTEXT_ASSISTANT = "助手回复：{reply}"


EXTRACTION_PROMPTS = {
    "memory_write": (
        "从用户消息中提取要记住的内容。\n"
        '输出 JSON：{{"section": "分区名", "content": "要记住的内容"}}\n'
        "分区名可选：重要信息、用户偏好、备忘、待办事项。根据内容选择合适分区。\n"
        "只输出 JSON，不要其他文字。"
    ),
    "schedule_reminder": (
        "从用户消息中提取定时任务的参数。\n"
        '输出 JSON：{{"text": "任务指令（描述到时间后要做什么）", "time_expr": "原始时间表达式"}}\n'
        "time_expr 保留用户的原始表达（如 '5分钟后'、'明天下午3点'）。\n"
        "text 是任务内容（去掉时间部分），可以是提醒内容也可以是要执行的动作。\n"
        "只输出 JSON，不要其他文字。"
    ),
    "calendar_create": (
        "从用户消息中提取日历事件参数。\n"
        '输出 JSON：{{"summary": "事件标题", "time_expr": "原始时间表达式", '
        '"duration_minutes": 60}}\n'
        "summary 是事件的简短标题。\n"
        "time_expr 保留用户的原始时间表达。\n"
        "duration_minutes 是持续时间（分钟），默认60。\n"
        "只输出 JSON，不要其他文字。"
    ),
}
