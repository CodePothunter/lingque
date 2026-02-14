"""意图检测器 — LLM 判断未执行的工具调用"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from lq.prompts import RECOVERABLE_TOOL_DESCRIPTIONS, INTENT_DETECT_PROMPT, TOOLS_CALLED_NONE, EVIDENCE_LLM

logger = logging.getLogger(__name__)


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
        uncalled = {k: v for k, v in RECOVERABLE_TOOL_DESCRIPTIONS.items() if k not in tools_called}
        if not uncalled:
            return []

        # 互斥：定时消息和日历事件是相似意图，已调一个就不检测另一个
        if "schedule_message" in tools_called:
            uncalled.pop("calendar_create_event", None)
        if "calendar_create_event" in tools_called:
            uncalled.pop("schedule_message", None)
        # 如果已调用自定义工具或 create_custom_tool，说明 LLM 已在执行任务，不检测日历
        if "create_custom_tool" in tools_called or any(
            t not in RECOVERABLE_TOOL_DESCRIPTIONS and t not in (
                "write_memory", "send_message", "send_card",
                "read_self_file", "write_self_file",
                "list_custom_tools", "test_custom_tool",
                "delete_custom_tool", "toggle_custom_tool",
            ) for t in tools_called
        ):
            uncalled.pop("calendar_create_event", None)
            uncalled.pop("schedule_message", None)
        if not uncalled:
            return []

        tool_desc = "\n".join(f"- {name}: {desc}" for name, desc in uncalled.items())

        prompt = INTENT_DETECT_PROMPT.format(
            user_message=user_message,
            llm_response=llm_response,
            tools_called=', '.join(tools_called) if tools_called else TOOLS_CALLED_NONE,
            tool_desc=tool_desc,
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
                        evidence=EVIDENCE_LLM,
                    ))
            return intents

        except (json.JSONDecodeError, Exception):
            logger.warning("IntentDetector LLM 判断失败")
            return []
