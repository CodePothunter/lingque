"""PostProcessor — 确定性后处理管线编排"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Awaitable

from lq.intent import IntentDetector, DetectedIntent
from lq.subagent import SubAgent
from lq.timeparse import parse_time_expression, to_iso8601, CST

logger = logging.getLogger(__name__)

# 成功通知模板
_NOTIFY_TEMPLATES: dict[str, str] = {
    "write_memory": "[已记入记忆 — {section}]",
    "schedule_message": "[已设置提醒: {send_at_short} — {text}]",
    "calendar_create_event": "[已创建日程: {summary}]",
}


class PostProcessor:
    """LLM 回复后的确定性后处理管线。

    流程：detect → 分层参数解析 → execute → notify
    常规路径（LLM 正常调用工具）：零成本（纯正则扫描）。
    回退路径（LLM 没调用工具）：SubAgent 成本约 $0.002/次。
    """

    def __init__(
        self,
        detector: IntentDetector,
        subagent: SubAgent,
        execute_tool_fn: Callable[..., Awaitable[dict]],
        send_fn: Callable[..., Awaitable[None]],
    ) -> None:
        self.detector = detector
        self.subagent = subagent
        self._execute_tool = execute_tool_fn
        self._send = send_fn

    async def process(
        self,
        user_message: str,
        llm_response: str,
        tools_called: list[str],
        chat_id: str,
        reply_to_msg_id: str | None,
    ) -> list[dict]:
        """后处理入口。

        Returns:
            执行结果列表（可能为空）。
        """
        # 1) 确定性意图检测
        intents = self.detector.detect(user_message, llm_response, tools_called)
        if not intents:
            return []

        logger.info(
            "PostProcessor 检测到 %d 个未执行意图: %s",
            len(intents),
            [(i.intent_type, i.tool_name) for i in intents],
        )

        results: list[dict] = []
        for intent in intents:
            result = await self._handle_intent(
                intent, user_message, llm_response, chat_id, reply_to_msg_id,
            )
            if result:
                results.append(result)

        return results

    async def _handle_intent(
        self,
        intent: DetectedIntent,
        user_message: str,
        llm_response: str,
        chat_id: str,
        reply_to_msg_id: str | None,
    ) -> dict | None:
        """处理单个意图：参数解析 → 执行 → 通知"""
        params = dict(intent.extracted_params)

        # 分层参数解析
        if intent.intent_type == "schedule_reminder":
            params = await self._resolve_schedule_params(
                params, intent, user_message, llm_response, chat_id,
            )
        elif intent.intent_type == "calendar_create":
            params = await self._resolve_calendar_params(
                params, intent, user_message, llm_response, chat_id,
            )
        elif intent.intent_type == "memory_write":
            if not params.get("content"):
                params = await self._resolve_via_subagent(
                    intent, user_message, llm_response, chat_id,
                )

        if not params:
            logger.info("PostProcessor: 参数解析失败，跳过 %s", intent.intent_type)
            return None

        # 执行
        result = await self._execute(intent.tool_name, params, chat_id)
        if not result:
            return None

        # 通知
        await self._notify(intent.tool_name, params, chat_id, reply_to_msg_id)
        return result

    async def _resolve_schedule_params(
        self,
        params: dict,
        intent: DetectedIntent,
        user_message: str,
        llm_response: str,
        chat_id: str,
    ) -> dict:
        """schedule_message 特殊处理：timeparse 确定性解析 → SubAgent 回退"""
        # 第一层：确定性时间解析
        now = datetime.now(CST)
        dt = parse_time_expression(user_message, now)

        if dt:
            send_at = to_iso8601(dt)
            text = params.get("text", "")
            if not text:
                # 尝试 SubAgent 提取文本
                sub_params = await self._resolve_via_subagent(
                    intent, user_message, llm_response, chat_id,
                )
                text = sub_params.get("text", "提醒") if sub_params else "提醒"
            return {
                "chat_id": chat_id,
                "text": text,
                "send_at": send_at,
            }

        # 第二层：SubAgent 提取（含 time_expr）
        sub_params = await self._resolve_via_subagent(
            intent, user_message, llm_response, chat_id,
        )
        if not sub_params:
            return {}

        # 尝试解析 SubAgent 返回的 time_expr
        time_expr = sub_params.get("time_expr", "")
        if time_expr:
            dt = parse_time_expression(time_expr, now)
        if not dt:
            # 最后尝试原始消息
            dt = parse_time_expression(user_message, now)
        if not dt:
            return {}

        return {
            "chat_id": chat_id,
            "text": sub_params.get("text", "提醒"),
            "send_at": to_iso8601(dt),
        }

    async def _resolve_calendar_params(
        self,
        params: dict,
        intent: DetectedIntent,
        user_message: str,
        llm_response: str,
        chat_id: str,
    ) -> dict:
        """calendar_create_event 参数解析"""
        now = datetime.now(CST)
        dt = parse_time_expression(user_message, now)

        if not params.get("summary") or not dt:
            sub_params = await self._resolve_via_subagent(
                intent, user_message, llm_response, chat_id,
            )
            if sub_params:
                params.update(sub_params)
                if not dt and sub_params.get("time_expr"):
                    dt = parse_time_expression(sub_params["time_expr"], now)

        if not dt:
            dt = parse_time_expression(user_message, now)
        if not dt or not params.get("summary"):
            return {}

        duration = params.get("duration_minutes", 60)
        end_dt = dt + timedelta(minutes=duration)

        return {
            "summary": params["summary"],
            "start_time": to_iso8601(dt),
            "end_time": to_iso8601(end_dt),
            "description": "",
        }

    async def _resolve_via_subagent(
        self,
        intent: DetectedIntent,
        user_message: str,
        llm_response: str,
        chat_id: str,
    ) -> dict:
        """通过 SubAgent 提取参数"""
        result = await self.subagent.extract_params(
            intent.intent_type, user_message, llm_response, chat_id,
        )
        return result or {}

    async def _execute(
        self,
        tool_name: str,
        params: dict,
        chat_id: str,
    ) -> dict | None:
        """执行工具调用"""
        try:
            result = await self._execute_tool(tool_name, params, chat_id)
            if result.get("success"):
                logger.info("PostProcessor 执行成功: %s", tool_name)
                return result
            else:
                logger.warning("PostProcessor 执行失败: %s → %s", tool_name, result)
                return None
        except Exception:
            logger.exception("PostProcessor 执行异常: %s", tool_name)
            return None

    async def _notify(
        self,
        tool_name: str,
        params: dict,
        chat_id: str,
        reply_to_msg_id: str | None,
    ) -> None:
        """发送成功通知给用户"""
        template = _NOTIFY_TEMPLATES.get(tool_name)
        if not template:
            return

        # 构造模板变量
        fmt_vars = dict(params)
        if "send_at" in fmt_vars:
            # 提取 HH:MM 用于简洁展示
            try:
                dt = datetime.fromisoformat(fmt_vars["send_at"])
                fmt_vars["send_at_short"] = dt.strftime("%H:%M")
            except (ValueError, TypeError):
                fmt_vars["send_at_short"] = fmt_vars["send_at"]

        try:
            msg = template.format_map(fmt_vars)
        except KeyError:
            msg = f"[已执行: {tool_name}]"

        try:
            await self._send(msg, chat_id, reply_to_msg_id)
        except Exception:
            logger.exception("PostProcessor 通知发送失败")
