"""确定性意图检测器 — 纯正则，零 LLM 成本"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class DetectedIntent:
    """一个被检测到但未被 LLM 执行的意图"""
    intent_type: str
    tool_name: str
    confidence: float  # 0.0 ~ 1.0
    extracted_params: dict = field(default_factory=dict)
    needs_subagent: bool = False
    evidence: str = ""


@dataclass
class IntentRule:
    """单条意图检测规则"""
    intent_type: str
    tool_name: str
    user_patterns: list[re.Pattern]
    response_patterns: list[re.Pattern]
    param_extractor: Callable[[str, str], dict] | None = None


def _extract_memory_params(user_msg: str, _llm_resp: str) -> dict:
    """尝试从用户消息中提取 write_memory 参数"""
    # "记住X" / "别忘了X" → content = X
    for pat in (
        r"(?:记住|记一下|别忘了|帮我记)\s*[，,：:]*\s*(.+)",
        r"(.+?)\s*(?:记住|记一下|别忘了)",
    ):
        m = re.search(pat, user_msg, re.DOTALL)
        if m:
            content = m.group(1).strip()
            if content:
                return {"section": "重要信息", "content": content}
    return {}


def _extract_reminder_params(user_msg: str, _llm_resp: str) -> dict:
    """尝试从用户消息中提取 schedule_message 的文本部分"""
    # "提醒我X" / "X分钟后提醒我Y"
    for pat in (
        r"提醒[我]?\s*(.+)",
        r"(.+?)\s*提醒[我]?",
    ):
        m = re.search(pat, user_msg)
        if m:
            raw = m.group(1).strip()
            # 去掉时间前缀部分，保留动作
            cleaned = re.sub(
                r"^(\d+|[一二两三四五六七八九十]+)\s*(?:个)?\s*(?:分钟|小时|天)\s*(?:后|之后)?\s*",
                "", raw,
            )
            cleaned = re.sub(
                r"^(?:今天|明天|后天|大后天)?\s*(?:凌晨|早上|上午|中午|下午|傍晚|晚上|晚)?"
                r"\s*(?:\d{1,2}|[一二两三四五六七八九十]+)\s*(?:点|时|:|：)"
                r"\s*(?:(?:\d{1,2}|半)\s*(?:分)?)?\s*",
                "", cleaned,
            )
            if cleaned:
                return {"text": cleaned}
    return {}


def _extract_calendar_params(user_msg: str, _llm_resp: str) -> dict:
    """尝试从用户消息中提取 calendar_create_event 的标题"""
    for pat in (
        r"(?:建|创建|安排|加|添加)\s*(?:一个|一场)?\s*(.+?)\s*(?:会议|日程|活动|事件)",
        r"(?:会议|日程|活动|事件)\s*[：:]*\s*(.+)",
    ):
        m = re.search(pat, user_msg)
        if m:
            summary = m.group(1).strip()
            if summary:
                return {"summary": summary}
    return {}


# ──── 内置规则 ────

_BUILTIN_RULES: list[IntentRule] = [
    IntentRule(
        intent_type="memory_write",
        tool_name="write_memory",
        user_patterns=[
            re.compile(r"记住|记一下|别忘了|帮我记|记得|请记"),
        ],
        response_patterns=[
            re.compile(r"记住了|好的|知道了|明白|了解|没问题|已经记|记下"),
        ],
        param_extractor=_extract_memory_params,
    ),
    IntentRule(
        intent_type="schedule_reminder",
        tool_name="schedule_message",
        user_patterns=[
            re.compile(
                r"(\d+|[一二两三四五六七八九十半]+)\s*(?:个)?\s*(?:分钟|小时|天)\s*(?:后|之后)?\s*提醒"
            ),
            re.compile(
                r"(?:今天|明天|后天|大后天)?\s*(?:凌晨|早上|上午|中午|下午|傍晚|晚上|晚)?"
                r"\s*(?:\d{1,2}|[一二两三四五六七八九十]+)\s*(?:点|时|:|：).*提醒"
            ),
            re.compile(r"提醒我?.*(?:分钟|小时|天|点|时)"),
            re.compile(r"(?:分钟|小时|天)\s*(?:后|之后)\s*(?:提醒|叫我|通知)"),
        ],
        response_patterns=[
            re.compile(r"好的|没问题|设置|提醒|知道了|放心|已|搞定|安排"),
        ],
        param_extractor=_extract_reminder_params,
    ),
    IntentRule(
        intent_type="calendar_create",
        tool_name="calendar_create_event",
        user_patterns=[
            re.compile(r"(?:建|创建|安排|加|添加)\s*(?:一个|一场)?\s*.*?(?:会议|日程|活动|事件)"),
            re.compile(r"(?:会议|日程|活动|事件)\s*.*?(?:建|创建|安排|加|添加)"),
        ],
        response_patterns=[
            re.compile(r"好的|已经|创建|安排|没问题|搞定|知道了|已"),
        ],
        param_extractor=_extract_calendar_params,
    ),
]


class IntentDetector:
    """确定性意图检测：用户消息 + LLM 回复 → 未执行的意图列表"""

    def __init__(self) -> None:
        self._rules: list[IntentRule] = list(_BUILTIN_RULES)

    def add_rule(self, rule: IntentRule) -> None:
        self._rules.append(rule)

    def detect(
        self,
        user_message: str,
        llm_response: str,
        tools_called: list[str],
    ) -> list[DetectedIntent]:
        """检测用户消息中未被 LLM 工具调用覆盖的意图。

        如果 tool_name 已在 tools_called 中 → 跳过（不重复执行）。
        """
        results: list[DetectedIntent] = []

        for rule in self._rules:
            # 已执行 → 跳过
            if rule.tool_name in tools_called:
                continue

            # 用户消息匹配
            user_match = None
            for pat in rule.user_patterns:
                user_match = pat.search(user_message)
                if user_match:
                    break
            if not user_match:
                continue

            # LLM 回复匹配（LLM 表示要做但没调工具）
            resp_match = None
            for pat in rule.response_patterns:
                resp_match = pat.search(llm_response)
                if resp_match:
                    break
            if not resp_match:
                continue

            # 尝试提取参数
            params: dict = {}
            if rule.param_extractor:
                params = rule.param_extractor(user_message, llm_response)

            needs_subagent = not params
            confidence = 0.9 if params else 0.7

            results.append(DetectedIntent(
                intent_type=rule.intent_type,
                tool_name=rule.tool_name,
                confidence=confidence,
                extracted_params=params,
                needs_subagent=needs_subagent,
                evidence=f"user='{user_match.group()[:40]}' resp='{resp_match.group()[:40]}'",
            ))

        return results
