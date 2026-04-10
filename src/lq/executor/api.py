"""LLM API 执行器（Anthropic / OpenAI / Responses，含重试与统计）"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import re

import anthropic
import openai

from lq.config import APIConfig

logger = logging.getLogger(__name__)

# 清理模型输出中的推理标签（<think>...</think> 及 GLM 的 <|TG|>...<|TC|>）
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_GLM_THINK_RE = re.compile(r"<\|TG\|>.*?<\|TC\|>", re.DOTALL)


def _clean_output(text: str) -> str:
    """移除模型输出中的推理标签和残留片段"""
    text = _THINK_RE.sub("", text)
    text = _GLM_THINK_RE.sub("", text)
    text = text.replace("</think>", "")
    text = text.replace("<|TG|>", "").replace("<|TC|>", "")
    return text.strip()


def _extract_text(content: list) -> str:
    """从 Anthropic 响应 content blocks 中提取文本"""
    for block in content:
        if block.type == "text":
            return block.text
    return ""


# 可重试的 HTTP 状态码
RETRYABLE_STATUS = {429, 500, 502, 503, 529}
MAX_RETRIES = 3
BASE_DELAY = 1.0  # 秒

# 每百万 token 价格（USD），input / output
MODEL_PRICING: dict[str, tuple[float, float]] = {
    # Anthropic
    "claude-opus-4-6": (15.0, 75.0),
    "claude-sonnet-4-20250514": (3.0, 15.0),
    "claude-haiku-4-20250414": (0.80, 4.0),
    "claude-3-5-sonnet-20241022": (3.0, 15.0),
    "claude-3-5-haiku-20241022": (0.80, 4.0),
    # GLM（智谱）
    "glm-5": (1.0, 3.2),
    # OpenAI
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.4, 1.6),
    "gpt-4.1-nano": (0.1, 0.4),
    "o3": (2.0, 8.0),
    "o3-mini": (1.1, 4.4),
    "o4-mini": (1.1, 4.4),
    # Gemini
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.15, 0.6),
    "gemini-2.0-flash": (0.1, 0.4),
    "gemini-2.0-flash-lite": (0.075, 0.3),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """根据模型和 token 数估算费用（USD）"""
    prices = MODEL_PRICING.get(model)
    if not prices:
        for key, val in MODEL_PRICING.items():
            if key in model or model in key:
                prices = val
                break
    if not prices:
        return 0.0
    input_price, output_price = prices
    return (input_tokens * input_price + output_tokens * output_price) / 1_000_000


# ─── 重试逻辑 ───

async def _retry_api_call(fn, *args, **kwargs):
    """Anthropic SDK 指数退避重试"""
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


async def _retry_openai_call(fn, *args, **kwargs):
    """OpenAI SDK 指数退避重试"""
    last_exc = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            return await fn(*args, **kwargs)
        except openai.RateLimitError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 限流，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
        except openai.InternalServerError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 服务器错误，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
        except openai.APIConnectionError as e:
            last_exc = e
            if attempt < MAX_RETRIES:
                delay = BASE_DELAY * (2 ** attempt)
                logger.warning("API 连接错误，%0.1fs 后重试 (%d/%d)", delay, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(delay)
    raise last_exc


# ─── Anthropic 执行器 ───

class DirectAPIExecutor:
    """通过 Anthropic SDK 调用 LLM（支持智谱兼容接口）"""

    def __init__(self, api_config: APIConfig, model: str) -> None:
        self.model = model
        self.mcp_key: str = api_config.mcp_key or api_config.api_key
        self.client = anthropic.AsyncAnthropic(
            api_key=api_config.api_key,
            base_url=api_config.base_url,
            default_headers={"Authorization": ""},
        )
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
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=4096,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        self._record_usage(resp, "reply")
        text = _clean_output(_extract_text(resp.content))
        logger.debug("API 回复 (%d tokens): %s...", resp.usage.output_tokens, text[:80])
        return text

    async def reply_with_history(
        self, system: str, messages: list[dict[str, str]], max_tokens: int = 4096,
    ) -> str:
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        self._record_usage(resp, "reply_with_history")
        return _clean_output(_extract_text(resp.content))

    async def quick_judge(self, prompt: str) -> str:
        resp = await _retry_api_call(
            self.client.messages.create,
            model=self.model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp, "quick_judge")
        return _clean_output(_extract_text(resp.content))

    async def reply_with_tools(
        self, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int = 4096,
    ) -> ToolResponse:
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
        logger.info("API 返回: stop_reason=%s text_len=%d preview=%s",
                    resp.stop_reason, len(combined_text), combined_text[:150])

        if not pending_tools or resp.stop_reason == "end_turn":
            truncated = not pending_tools and resp.stop_reason == "tool_use"
            if truncated:
                logger.warning("tool_use 截断: stop_reason=tool_use 但无 tool_use 块, text=%s",
                              combined_text[:150])
            return ToolResponse(
                text=combined_text, tool_calls=tool_calls,
                tool_use_truncated=truncated, messages=msgs,
            )

        tool_calls.extend(pending_tools)
        return ToolResponse(
            text=combined_text, tool_calls=tool_calls,
            pending=True, raw_response=resp, messages=msgs,
        )

    async def continue_after_tools(
        self, system: str, messages: list[dict], tools: list[dict],
        tool_results: list[dict], raw_response: Any, max_tokens: int = 4096,
    ) -> ToolResponse:
        msgs = list(messages)
        msgs.append({"role": "assistant", "content": raw_response.content})
        content_blocks = []
        for r in tool_results:
            if r.get("type") == "text":
                content_blocks.append({"type": "text", "text": r["text"]})
            else:
                content_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": r["tool_use_id"],
                    "content": r["content"],
                })
        msgs.append({"role": "user", "content": content_blocks})
        return await self.reply_with_tools(system, msgs, tools, max_tokens)


# ─── OpenAI 格式转换 ───

def _tools_to_chat(tools: list[dict]) -> list[dict]:
    """Anthropic → Chat Completions 工具格式"""
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


def _tools_to_responses(tools: list[dict]) -> list[dict]:
    """Anthropic → Responses API 工具格式（扁平结构）"""
    return [
        {
            "type": "function",
            "name": t["name"],
            "description": t.get("description", ""),
            "parameters": t.get("input_schema", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _block_attr(block: Any, attr: str, default: Any = None) -> Any:
    """从 SDK 对象或 dict 中取属性"""
    return getattr(block, attr, None) or (block.get(attr, default) if isinstance(block, dict) else default)


def _messages_to_chat(system: str, messages: list[dict]) -> list[dict]:
    """Anthropic 消息 → Chat Completions 消息格式"""
    oai: list[dict] = []
    if system:
        oai.append({"role": "system", "content": system})

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        if role == "user" and isinstance(content, list):
            tool_results = []
            text_parts = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    tool_results.append({
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": block.get("content", ""),
                    })
                elif isinstance(block, dict) and block.get("type") == "text":
                    text_parts.append(block["text"])
                elif isinstance(block, str):
                    text_parts.append(block)
            oai.extend(tool_results)
            if text_parts:
                oai.append({"role": "user", "content": "\n".join(text_parts)})
        elif role == "assistant" and isinstance(content, list):
            text_parts = []
            tool_calls = []
            for block in content:
                bt = _block_attr(block, "type")
                if bt == "text":
                    text_parts.append(_block_attr(block, "text", ""))
                elif bt == "tool_use":
                    tool_calls.append({
                        "id": _block_attr(block, "id", ""),
                        "type": "function",
                        "function": {
                            "name": _block_attr(block, "name", ""),
                            "arguments": json.dumps(
                                _block_attr(block, "input", {}), ensure_ascii=False,
                            ),
                        },
                    })
            oai_msg: dict[str, Any] = {"role": "assistant"}
            oai_msg["content"] = "\n".join(text_parts) if text_parts else None
            if tool_calls:
                oai_msg["tool_calls"] = tool_calls
            oai.append(oai_msg)
        else:
            oai.append({"role": role, "content": content})

    return oai


def _messages_to_responses(messages: list[dict]) -> list[dict]:
    """Anthropic 消息 → Responses API input 格式"""
    items: list[dict] = []

    for msg in messages:
        role = msg.get("role", "user")
        content = msg.get("content")

        if role == "user" and isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    items.append({
                        "type": "function_call_output",
                        "call_id": block["tool_use_id"],
                        "output": block.get("content", ""),
                    })
                elif isinstance(block, dict) and block.get("type") == "text":
                    items.append({"role": "user", "content": block["text"]})
                elif isinstance(block, str):
                    items.append({"role": "user", "content": block})
        elif role == "assistant" and isinstance(content, list):
            text_parts = []
            for block in content:
                bt = _block_attr(block, "type")
                if bt == "text":
                    text_parts.append(_block_attr(block, "text", ""))
                elif bt == "tool_use":
                    items.append({
                        "type": "function_call",
                        "call_id": _block_attr(block, "id", ""),
                        "name": _block_attr(block, "name", ""),
                        "arguments": json.dumps(
                            _block_attr(block, "input", {}), ensure_ascii=False,
                        ),
                    })
            if text_parts:
                items.append({"role": "assistant", "content": "\n".join(text_parts)})
        else:
            items.append({"role": role, "content": content})

    return items


# ─── OpenAI 执行器（Chat Completions / Responses API 双模式） ───

class OpenAIExecutor:
    """通过 OpenAI SDK 调用 LLM。

    api_format="openai"     → Chat Completions API
    api_format="responses"  → Responses API
    """

    def __init__(self, api_config: APIConfig, model: str, api_format: str = "openai") -> None:
        self.model = model
        self.mcp_key: str = api_config.mcp_key or api_config.api_key
        self._use_responses = (api_format == "responses")
        self.client = openai.AsyncOpenAI(
            api_key=api_config.api_key,
            base_url=api_config.base_url,
        )
        self.stats: Any = None

    def _record_usage(self, resp: Any, call_type: str) -> None:
        if not self.stats:
            return
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        # Responses API: input_tokens/output_tokens
        # Chat Completions: prompt_tokens/completion_tokens
        input_tokens = getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0) or 0
        output_tokens = getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0) or 0
        cost = _estimate_cost(self.model, input_tokens, output_tokens)
        self.stats.record(
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            call_type=call_type,
            cost_usd=cost,
        )

    # ── reply ──

    async def reply(self, system: str, user_message: str) -> str:
        if self._use_responses:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "input": user_message,
                "max_output_tokens": 4096,
            }
            if system:
                kwargs["instructions"] = system
            resp = await _retry_openai_call(self.client.responses.create, **kwargs)
            self._record_usage(resp, "reply")
            return _clean_output(resp.output_text or "")

        msgs: list[dict] = []
        if system:
            msgs.append({"role": "system", "content": system})
        msgs.append({"role": "user", "content": user_message})
        resp = await _retry_openai_call(
            self.client.chat.completions.create,
            model=self.model, max_tokens=4096, messages=msgs,
        )
        self._record_usage(resp, "reply")
        return _clean_output(resp.choices[0].message.content or "")

    # ── reply_with_history ──

    async def reply_with_history(
        self, system: str, messages: list[dict[str, str]], max_tokens: int = 4096,
    ) -> str:
        if self._use_responses:
            input_items = _messages_to_responses(messages)
            kwargs: dict[str, Any] = {
                "model": self.model,
                "input": input_items,
                "max_output_tokens": max_tokens,
            }
            if system:
                kwargs["instructions"] = system
            resp = await _retry_openai_call(self.client.responses.create, **kwargs)
            self._record_usage(resp, "reply_with_history")
            return _clean_output(resp.output_text or "")

        oai_msgs = _messages_to_chat(system, messages)
        resp = await _retry_openai_call(
            self.client.chat.completions.create,
            model=self.model, max_tokens=max_tokens, messages=oai_msgs,
        )
        self._record_usage(resp, "reply_with_history")
        return _clean_output(resp.choices[0].message.content or "")

    # ── quick_judge ──

    async def quick_judge(self, prompt: str) -> str:
        if self._use_responses:
            resp = await _retry_openai_call(
                self.client.responses.create,
                model=self.model, input=prompt, max_output_tokens=256,
            )
            self._record_usage(resp, "quick_judge")
            return _clean_output(resp.output_text or "")

        resp = await _retry_openai_call(
            self.client.chat.completions.create,
            model=self.model, max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        self._record_usage(resp, "quick_judge")
        return _clean_output(resp.choices[0].message.content or "")

    # ── reply_with_tools ──

    async def reply_with_tools(
        self, system: str, messages: list[dict], tools: list[dict],
        max_tokens: int = 4096,
    ) -> ToolResponse:
        if self._use_responses:
            return await self._tools_responses(system, messages, tools, max_tokens)
        return await self._tools_chat(system, messages, tools, max_tokens)

    async def _tools_responses(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int,
    ) -> ToolResponse:
        input_items = _messages_to_responses(messages)
        resp_tools = _tools_to_responses(tools)
        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": input_items,
            "tools": resp_tools,
            "max_output_tokens": max_tokens,
        }
        if system:
            kwargs["instructions"] = system
        resp = await _retry_openai_call(self.client.responses.create, **kwargs)
        self._record_usage(resp, "reply_with_tools")

        text_parts: list[str] = []
        pending_tools: list[dict] = []
        for item in resp.output:
            itype = getattr(item, "type", None)
            if itype == "message":
                for part in item.content:
                    if getattr(part, "type", None) == "output_text":
                        text_parts.append(part.text)
            elif itype == "function_call":
                try:
                    args = json.loads(item.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                pending_tools.append({
                    "id": item.call_id,
                    "name": item.name,
                    "input": args,
                })

        combined_text = _clean_output("\n".join(text_parts))
        status = getattr(resp, "status", "completed")
        logger.info("API 返回 (responses): status=%s text_len=%d tools=%d preview=%s",
                    status, len(combined_text), len(pending_tools), combined_text[:150])

        if not pending_tools:
            return ToolResponse(
                text=combined_text, tool_calls=[],
                tool_use_truncated=(status == "incomplete"),
                messages=messages,
            )

        return ToolResponse(
            text=combined_text, tool_calls=pending_tools,
            pending=True, raw_response=resp, messages=messages,
        )

    async def _tools_chat(
        self, system: str, messages: list[dict], tools: list[dict], max_tokens: int,
    ) -> ToolResponse:
        oai_msgs = _messages_to_chat(system, messages)
        oai_tools = _tools_to_chat(tools)
        resp = await _retry_openai_call(
            self.client.chat.completions.create,
            model=self.model, max_tokens=max_tokens,
            messages=oai_msgs, tools=oai_tools,
        )
        self._record_usage(resp, "reply_with_tools")

        choice = resp.choices[0]
        message = choice.message
        combined_text = _clean_output(message.content or "")
        logger.info("API 返回 (chat): finish_reason=%s text_len=%d preview=%s",
                    choice.finish_reason, len(combined_text), combined_text[:150])

        pending_tools: list[dict] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                pending_tools.append({
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })

        if not pending_tools or choice.finish_reason == "stop":
            truncated = not pending_tools and choice.finish_reason == "tool_calls"
            if truncated:
                logger.warning("tool_use 截断 (chat): finish_reason=tool_calls 但无工具")
            return ToolResponse(
                text=combined_text, tool_calls=[],
                tool_use_truncated=truncated, messages=messages,
            )

        return ToolResponse(
            text=combined_text, tool_calls=pending_tools,
            pending=True, raw_response=resp, messages=messages,
        )

    # ── continue_after_tools ──

    async def continue_after_tools(
        self, system: str, messages: list[dict], tools: list[dict],
        tool_results: list[dict], raw_response: Any, max_tokens: int = 4096,
    ) -> ToolResponse:
        if self._use_responses:
            return await self._continue_responses(
                system, messages, tools, tool_results, raw_response, max_tokens,
            )
        return await self._continue_chat(
            system, messages, tools, tool_results, raw_response, max_tokens,
        )

    async def _continue_responses(
        self, system: str, messages: list[dict], tools: list[dict],
        tool_results: list[dict], raw_response: Any, max_tokens: int,
    ) -> ToolResponse:
        """Responses API: 将 output items 转为 Anthropic 格式追加到历史"""
        msgs = list(messages)

        # 将 Responses output → Anthropic assistant message
        assistant_content: list[dict] = []
        for item in raw_response.output:
            itype = getattr(item, "type", None)
            if itype == "message":
                for part in item.content:
                    if getattr(part, "type", None) == "output_text":
                        assistant_content.append({"type": "text", "text": part.text})
            elif itype == "function_call":
                try:
                    args = json.loads(item.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                assistant_content.append({
                    "type": "tool_use",
                    "id": item.call_id,
                    "name": item.name,
                    "input": args,
                })
        msgs.append({"role": "assistant", "content": assistant_content})

        # 工具结果 → Anthropic tool_result
        content_blocks: list[dict] = []
        for r in tool_results:
            if r.get("type") == "text":
                content_blocks.append({"type": "text", "text": r["text"]})
            else:
                content_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": r["tool_use_id"],
                    "content": r["content"],
                })
        msgs.append({"role": "user", "content": content_blocks})

        return await self.reply_with_tools(system, msgs, tools, max_tokens)

    async def _continue_chat(
        self, system: str, messages: list[dict], tools: list[dict],
        tool_results: list[dict], raw_response: Any, max_tokens: int,
    ) -> ToolResponse:
        """Chat Completions: 将 message + tool results 转为 Anthropic 格式追加到历史"""
        msgs = list(messages)

        # 将 Chat response → Anthropic assistant message
        prev = raw_response.choices[0].message
        assistant_content: list[dict] = []
        if prev.content:
            assistant_content.append({"type": "text", "text": prev.content})
        if prev.tool_calls:
            for tc in prev.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except (json.JSONDecodeError, TypeError):
                    args = {}
                assistant_content.append({
                    "type": "tool_use",
                    "id": tc.id,
                    "name": tc.function.name,
                    "input": args,
                })
        msgs.append({"role": "assistant", "content": assistant_content})

        # 工具结果 → Anthropic tool_result
        content_blocks: list[dict] = []
        for r in tool_results:
            if r.get("type") == "text":
                content_blocks.append({"type": "text", "text": r["text"]})
            else:
                content_blocks.append({
                    "type": "tool_result",
                    "tool_use_id": r["tool_use_id"],
                    "content": r["content"],
                })
        msgs.append({"role": "user", "content": content_blocks})

        return await self.reply_with_tools(system, msgs, tools, max_tokens)


# ─── 工厂函数 ───

def create_executor(api_config: APIConfig, model: str) -> DirectAPIExecutor | OpenAIExecutor:
    """根据 api_config.api_format 选择执行器。

    - "anthropic" → DirectAPIExecutor（Anthropic SDK）
    - "openai"    → OpenAIExecutor（Chat Completions API）
    - "responses" → OpenAIExecutor（Responses API）
    """
    fmt = api_config.api_format
    if fmt in ("openai", "responses"):
        logger.info("使用 OpenAI 执行器 (%s): model=%s base_url=%s",
                    fmt, model, api_config.base_url)
        return OpenAIExecutor(api_config, model, api_format=fmt)
    logger.info("使用 Anthropic 执行器: model=%s", model)
    return DirectAPIExecutor(api_config, model)


# ─── 响应类 ───

class ToolResponse:
    """工具调用响应"""

    def __init__(
        self,
        text: str = "",
        tool_calls: list[dict] | None = None,
        pending: bool = False,
        raw_response: Any = None,
        messages: list[dict] | None = None,
        tool_use_truncated: bool = False,
    ) -> None:
        self.text = text
        self.tool_calls = tool_calls or []
        self.pending = pending
        self.raw_response = raw_response
        self.messages = messages or []
        self.tool_use_truncated = tool_use_truncated
