"""Anthropic API 直调执行器（含重试与统计）"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import re

import anthropic

from lq.config import APIConfig

logger = logging.getLogger(__name__)

# 清理模型输出中的推理标签（<think>...</think>）
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def _clean_output(text: str) -> str:
    """移除模型输出中的推理标签和残留片段"""
    text = _THINK_RE.sub("", text)
    # 处理不完整的 </think> 标签（模型被截断时）
    text = text.replace("</think>", "")
    return text.strip()

# 可重试的 HTTP 状态码
RETRYABLE_STATUS = {429, 500, 502, 503, 529}
MAX_RETRIES = 3
BASE_DELAY = 1.0  # 秒

# 每百万 token 价格（USD），input / output
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-20250414": (0.80, 4.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """根据模型和 token 数估算费用（USD）"""
    prices = MODEL_PRICING.get(model)
    if not prices:
        # 对未知模型尝试模糊匹配
        for key, val in MODEL_PRICING.items():
            if key in model or model in key:
                prices = val
                break
    if not prices:
        return 0.0
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


async def _retry_api_call(fn, *args, **kwargs):
    """指数退避重试"""
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await fn(*args, **kwargs)
        except anthropic.RateLimitError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 限流，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
        except anthropic.InternalServerError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 服务器错误，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
        except anthropic.APIConnectionError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 连接错误，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
    raise last_exc


class DirectAPIExecutor:
    """通过 Anthropic SDK 调用 LLM（支持智谱兼容接口）"""

    def __init__(self, api_config: APIConfig, model: str) -> None:
        self.model = model
        self.client = anthropic.AsyncAnthropic(
            api_key=api_config.api_key,
            base_url=api_config.base_url,
            # 智谱 API 只认 X-Api-Key，SDK 自动附加的 Authorization: Bearer
            # 会干扰认证（Bearer token 来自环境变量 ANTHROPIC_API_KEY），清空它
            default_headers={"Authorization": ""},
        )
        # 可选统计跟踪器（由 gateway 注入）
        self.stats: Any = None

    def _record_usage(self, resp: Any, call_type: str) -> None:
        if self.stats and hasattr(resp, "usage"):
            input_tokens = resp.usage.input_tokens
            output_tokens = resp.usage.output_tokens
            cost = _estimate_cost(self.model, input_tokens, output_tokens)
            self.stats.record(
                model=self.model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                call_type=call_type,
                cost_usd=cost,
            )

    async def reply(self, system: str, user_message: str) -> str:
        """纯文本回复，无 tool use"""
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        self._record_usage(resp, "reply")
        text = _clean_output(resp.content[0].text)
        logger.debug("API 回复 (%d tokens): %s...", resp.usage.output_tokens, text[:80])
        return text

    async def reply_with_history(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 4096,
    ) -> str:
        """带完整对话历史的回复"""
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        self._record_usage(resp, "reply_with_history")
        return _clean_output(resp.content[0].text)

    async def quick_judge(self, prompt: str) -> str:
        """低成本快速判断（用于介入评估等）"""
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp, "quick_judge")
        return _clean_output(resp.content[0].text)

    async def reply_with_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        max_tokens: int = 4096,
    ) -> ToolResponse:
        """带工具调用的回复"""
        msgs = list(messages)
        tool_calls: list[dict] = []

        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=msgs,
            tools=tools,
        )
        self._record_usage(resp, "reply_with_tools")

        text_parts = []
        pending_tools = []
        for block in resp.content:
            if block.type == "text":
                text_parts.append(block.text)
            elif block.type == "tool_use":
                pending_tools.append({
                    "id": block.id,
                    "name": block.name,
                    "input": block.input,
                })

        combined_text = _clean_output("\n".join(text_parts))

        if not pending_tools or resp.stop_reason == "end_turn":
            return ToolResponse(
                text=combined_text,
                tool_calls=tool_calls,
            )

        tool_calls.extend(pending_tools)
        return ToolResponse(
            text=combined_text,
            tool_calls=tool_calls,
            pending=True,
            raw_response=resp,
            messages=msgs,
        )

    async def continue_after_tools(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict],
        tool_results: list[dict],
        raw_response: Any,
        max_tokens: int = 4096,
    ) -> ToolResponse:
        """工具执行完成后继续对话"""
        msgs = list(messages)
        msgs.append({"role": "assistant", "content": raw_response.content})
        msgs.append({
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": r["tool_use_id"],
                    "content": r["content"],
                }
                for r in tool_results
            ],
        })
        return await self.reply_with_tools(system, msgs, tools, max_tokens)


class ToolResponse:
    """工具调用响应"""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[dict] | None = None,
        pending: bool = False,
        raw_response: Any = None,
        messages: list[dict] | None = None,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls or []
        self.pending = pending
        self.raw_response = raw_response
        self.messages = messages or []
