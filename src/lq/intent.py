"""意图检测器 — LLM 判断未执行的工具调用"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# PostProcessor 可补救的工具列表及描述
RECOVERABLE_TOOLS = {
    "write_memory": "用户要求记住某件事，但助手没有调用 write_memory 工具",
    "schedule_message": "用户要求定时提醒，但助手没有调用 schedule_message 工具",
    "calendar_create_event": "用户要求创建日程/会议，但助手没有调用 calendar_create_event 工具",
}


@dataclass
class DetectedIntent:
    """一个被检测到但未被 LLM 执行的意图"""
    intent_type: str
    tool_name: str
    confidence: float  # 0.0 ~ 1.0
    extracted_params: dict = field(default_factory=dict)
    needs_subagent: bool = False
    evidence: str = ""


class IntentDetector:
    """LLM 意图检测：判断用户消息中是否有未执行的工具调用意图"""

    def __init__(self, executor: Any) -> None:
        self.executor = executor

    async def detect(
        self,
        user_message: str,
        llm_response: str,
        tools_called: list[str],
    ) -> list[DetectedIntent]:
        """检测用户消息中未被 LLM 工具调用覆盖的意图。"""
        # 快速跳过：如果没有可补救的工具空间，直接返回
        uncalled = {k: v for k, v in RECOVERABLE_TOOLS.items() if k not in tools_called}
        if not uncalled:
            return []

        tool_desc = "\n".join(f"- {name}: {desc}" for name, desc in uncalled.items())

        prompt = (
            "判断以下对话中，用户是否明确要求执行某个操作，但助手的回复中没有实际执行。\n\n"
            f"用户消息：{user_message}\n"
            f"助手回复：{llm_response}\n"
            f"助手已调用的工具：{', '.join(tools_called) if tools_called else '无'}\n\n"
            f"可能遗漏的操作：\n{tool_desc}\n\n"
            "注意：\n"
            "- 只有用户**明确要求**执行的操作才算遗漏（如「记住我的生日」「5分钟后提醒我」）\n"
            "- 用户在**描述**、**询问**、**闲聊**时提到「记住」「提醒」等词不算遗漏\n"
            "- 「你记住了吗」「这么快就记住了」等陈述/疑问句不是指令\n"
            "- 如果助手已经通过工具完成了操作，不算遗漏\n\n"
            '输出 JSON：{"missed": [{"tool": "工具名"}]} 或 {"missed": []}\n'
            "只输出 JSON。"
        )

        try:
            raw = await self.executor.quick_judge(prompt)
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
            result = json.loads(raw)

            intents = []
            for item in result.get("missed", []):
                tool_name = item.get("tool", "")
                if tool_name in uncalled:
                    # 映射 tool_name → intent_type
                    intent_type = {
                        "write_memory": "memory_write",
                        "schedule_message": "schedule_reminder",
                        "calendar_create_event": "calendar_create",
                    }.get(tool_name, tool_name)
                    intents.append(DetectedIntent(
                        intent_type=intent_type,
                        tool_name=tool_name,
                        confidence=0.8,
                        needs_subagent=True,
                        evidence="LLM 判断",
                    ))
            return intents

        except (json.JSONDecodeError, Exception):
            logger.warning("IntentDetector LLM 判断失败")
            return []
