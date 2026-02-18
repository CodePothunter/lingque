"""工具执行分发 + 多模态内容构建"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from lq.platform import IncomingMessage, OutgoingMessage
from lq.prompts import (
    RESULT_GLOBAL_MEMORY_WRITTEN, RESULT_CHAT_MEMORY_WRITTEN,
    RESULT_CARD_SENT, RESULT_FILE_EMPTY, RESULT_FILE_UPDATED,
    RESULT_SEND_FAILED, RESULT_SCHEDULE_OK,
    ERR_CALENDAR_NOT_LOADED, ERR_TOOL_REGISTRY_NOT_LOADED,
    ERR_CC_NOT_LOADED, ERR_BASH_NOT_LOADED, ERR_UNKNOWN_TOOL,
    ERR_TIME_FORMAT_INVALID, ERR_TIME_PAST, ERR_CODE_VALIDATION_OK,
    SCHEDULED_ACTION_PROMPT,
)

logger = logging.getLogger(__name__)


class ToolExecMixin:
    """工具执行分发与多模态内容构建。"""

    async def _execute_tool(self, name: str, input_data: dict, chat_id: str) -> dict:
        """执行单个工具调用"""
        logger.info("执行工具: %s(%s)", name, json.dumps(input_data, ensure_ascii=False)[:100])

        try:
            if name == "write_memory":
                self.memory.update_memory(
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_GLOBAL_MEMORY_WRITTEN}

            elif name == "write_chat_memory":
                self.memory.update_chat_memory(
                    chat_id,
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_CHAT_MEMORY_WRITTEN}

            elif name == "calendar_create_event":
                if not self.calendar:
                    return {"success": False, "error": ERR_CALENDAR_NOT_LOADED}
                result = await self.calendar.create_event(
                    summary=input_data["summary"],
                    start_time=input_data["start_time"],
                    end_time=input_data["end_time"],
                    description=input_data.get("description", ""),
                )
                return result

            elif name == "calendar_list_events":
                if not self.calendar:
                    return {"success": False, "error": ERR_CALENDAR_NOT_LOADED}
                events = await self.calendar.list_events(
                    input_data["start_time"],
                    input_data["end_time"],
                )
                return {"success": True, "events": events}

            elif name == "send_card":
                card = {
                    "type": "info",
                    "title": input_data["title"],
                    "content": input_data["content"],
                    "color": input_data.get("color", "blue"),
                }
                await self.adapter.send(OutgoingMessage(chat_id, card=card))
                return {"success": True, "message": RESULT_CARD_SENT}

            elif name == "read_self_file":
                content = self.memory.read_self_file(input_data["filename"])
                if not content:
                    return {"success": True, "content": RESULT_FILE_EMPTY}
                return {"success": True, "content": content}

            elif name == "write_self_file":
                self.memory.write_self_file(
                    input_data["filename"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_FILE_UPDATED.format(filename=input_data['filename'])}

            elif name == "create_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.create_tool(
                    input_data["name"],
                    input_data["code"],
                )

            elif name == "list_custom_tools":
                if not self.tool_registry:
                    return {"success": True, "tools": []}
                return {"success": True, "tools": self.tool_registry.list_tools()}

            elif name == "test_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                errors = self.tool_registry.validate_code(input_data["code"])
                if errors:
                    return {"success": False, "errors": errors}
                return {"success": True, "message": ERR_CODE_VALIDATION_OK}

            elif name == "delete_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.delete_tool(input_data["name"])

            elif name == "toggle_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.toggle_tool(
                    input_data["name"],
                    input_data["enabled"],
                )

            elif name == "send_message":
                target = input_data.get("chat_id", "")
                if not target.startswith(("oc_", "ou_", "on_")) or len(target) < 20:
                    target = chat_id  # LLM 给了无效或截断的 ID，回退到当前会话
                text_to_send = input_data["text"]
                msg_id = await self.adapter.send(
                    OutgoingMessage(target, text_to_send)
                )
                if msg_id:
                    return {"success": True, "message_id": msg_id}
                return {"success": False, "error": RESULT_SEND_FAILED}

            elif name == "schedule_message":
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td

                send_at_str = input_data["send_at"]
                try:
                    send_at = _dt.fromisoformat(send_at_str)
                except ValueError:
                    return {"success": False, "error": ERR_TIME_FORMAT_INVALID.format(value=send_at_str)}

                cst = _tz(_td(hours=8))
                now = _dt.now(cst)
                if send_at.tzinfo is None:
                    send_at = send_at.replace(tzinfo=cst)
                delay = (send_at - now).total_seconds()
                if delay < 0:
                    return {"success": False, "error": ERR_TIME_PAST}

                target_chat_id = input_data.get("chat_id", "")
                if not target_chat_id.startswith(("oc_", "ou_", "on_")) or len(target_chat_id) < 20:
                    target_chat_id = chat_id  # LLM 给了无效或截断的 ID，回退到当前会话
                instruction = input_data["text"]
                router_ref = self

                async def _delayed_action():
                    await asyncio.sleep(delay)
                    try:
                        system = router_ref.memory.build_context(
                            chat_id=target_chat_id,
                        )
                        system += SCHEDULED_ACTION_PROMPT.format(
                            instruction=instruction, chat_id=target_chat_id,
                        )
                        messages = [{"role": "user", "content": instruction}]
                        result = await router_ref._reply_with_tool_loop(
                            system, messages, target_chat_id, None,
                        )
                        logger.info(
                            "定时任务已执行: chat=%s result=%s",
                            target_chat_id, (result or "")[:80],
                        )
                    except Exception:
                        logger.exception("定时任务执行失败: chat=%s", target_chat_id)

                asyncio.ensure_future(_delayed_action())
                return {"success": True, "message": RESULT_SCHEDULE_OK.format(send_at=send_at_str)}

            elif name == "run_claude_code":
                if not self.cc_executor:
                    return {"success": False, "error": ERR_CC_NOT_LOADED}
                result = await self.cc_executor.execute_with_context(
                    prompt=input_data["prompt"],
                    working_dir=input_data.get("working_dir", ""),
                    timeout=input_data.get("timeout", 300),
                )
                return result

            elif name == "run_bash":
                if not self.bash_executor:
                    return {"success": False, "error": ERR_BASH_NOT_LOADED}
                result = await self.bash_executor.execute(
                    command=input_data["command"],
                    working_dir=input_data.get("working_dir", ""),
                    timeout=input_data.get("timeout", 60),
                )
                return result

            elif name == "web_search":
                return await self._tool_web_search(
                    input_data["query"],
                    input_data.get("max_results", 5),
                )

            elif name == "web_fetch":
                return await self._tool_web_fetch(
                    input_data["url"],
                    input_data.get("max_length", 8000),
                )

            elif name == "run_python":
                return await self._tool_run_python(
                    input_data["code"],
                    input_data.get("timeout", 30),
                )

            elif name == "read_file":
                return self._tool_read_file(
                    input_data["path"],
                    input_data.get("max_lines", 500),
                )

            elif name == "write_file":
                return self._tool_write_file(
                    input_data["path"],
                    input_data["content"],
                )

            elif name == "get_my_stats":
                return self._tool_get_my_stats(
                    input_data.get("category", "today"),
                )

            else:
                # 尝试自定义工具注册表
                if self.tool_registry and self.tool_registry.has_tool(name):
                    import httpx
                    async with httpx.AsyncClient() as http_client:
                        context = {
                            "adapter": self.adapter,
                            "memory": self.memory,
                            "calendar": self.calendar,
                            "http": http_client,
                        }
                        return await self.tool_registry.execute(name, input_data, context)
                return {"success": False, "error": ERR_UNKNOWN_TOOL.format(name=name)}

        except Exception as e:
            logger.exception("工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    # ── 多模态内容构建 ──

    async def _build_multimodal_content(
        self, msg: IncomingMessage, text: str,
    ) -> str | list[dict]:
        """构建多模态内容：如果消息含图片则返回 content blocks 列表，否则返回纯文本。

        返回格式兼容 Anthropic Messages API：
        - 纯文本: "hello"
        - 多模态: [{"type": "image", "source": {...}}, {"type": "text", "text": "hello"}]

        图片下载失败时会在文本中附带提示，让 LLM 知道有图片未能加载。
        """
        if not msg.image_keys:
            return text

        blocks: list[dict] = []
        failed_count = 0

        for key in msg.image_keys:
            result = await self.adapter.fetch_media(msg.message_id, key)
            if result:
                b64_data, media_type = result
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                failed_count += 1

        # 构建文本部分，附带下载失败提示
        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            # 无图片也无文本
            return ""
        elif not any(b["type"] == "text" for b in blocks):
            # 有图片但没文本，加个默认提示
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks

    async def _build_image_content(
        self, image_messages: list[IncomingMessage], text: str,
    ) -> str | list[dict]:
        """从多条图片消息中下载图片，与文本合并为 content blocks。

        用于防抖合并场景：多条消息（可能混合文本和图片）合并后统一处理。
        图片下载失败时会在文本中附带提示。
        """
        blocks: list[dict] = []
        failed_count = 0
        for msg in image_messages:
            for key in msg.image_keys:
                result = await self.adapter.fetch_media(msg.message_id, key)
                if result:
                    b64_data, media_type = result
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    })
                else:
                    failed_count += 1

        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            return ""
        elif not any(b.get("type") == "text" for b in blocks):
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks
