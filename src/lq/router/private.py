"""私聊处理 + 自我反思 + 好奇心信号"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from lq.platform import IncomingMessage, OutgoingMessage, MessageType
from lq.prompts import (
    TAG_CONSTRAINTS, wrap_tag,
    CONSTRAINTS_PRIVATE,
    NON_TEXT_REPLY_PRIVATE,
    REFLECTION_WITH_CURIOSITY_PROMPT,
)

logger = logging.getLogger(__name__)


class PrivateChatMixin:
    """私聊消息处理、防抖、自我反思与好奇心信号提取。"""

    async def _handle_private(self, msg: IncomingMessage) -> None:
        """处理私聊消息（带防抖：短时间连发多条会合并后统一处理）"""
        text = msg.text
        has_images = bool(msg.image_keys)

        if not text and not has_images:
            if msg.message_type not in (MessageType.TEXT, MessageType.RICH_TEXT, MessageType.IMAGE):
                await self.adapter.send(OutgoingMessage(
                    msg.chat_id, NON_TEXT_REPLY_PRIVATE, reply_to=msg.message_id,
                ))
            return

        chat_id = msg.chat_id
        sender_name = msg.sender_name
        log_preview = text[:80] if text else "[图片]"
        logger.info("收到私聊 [%s]: %s", sender_name, log_preview)

        # 防抖：收集连续消息，延迟后统一处理
        pending = self._private_pending.get(chat_id)
        if pending:
            if text:
                pending["texts"].append(text)
            if has_images:
                pending.setdefault("image_msgs", []).append(msg)
            if pending.get("timer"):
                pending["timer"].cancel()
            # 通知适配器：消息正在排队
            count = len(pending["texts"])
            if count > 1:
                await self.adapter.notify_queued(chat_id, count)
            loop = asyncio.get_running_loop()
            pending["timer"] = loop.call_later(
                self._private_debounce_seconds,
                lambda cid=chat_id: asyncio.ensure_future(self._flush_private(cid)),
            )
        else:
            entry: dict[str, Any] = {
                "texts": [text] if text else [],
                "message_id": msg.message_id,
                "sender_name": sender_name,
                "timer": None,
            }
            if has_images:
                entry["image_msgs"] = [msg]
            self._private_pending[chat_id] = entry
            loop = asyncio.get_running_loop()
            self._private_pending[chat_id]["timer"] = loop.call_later(
                self._private_debounce_seconds,
                lambda cid=chat_id: asyncio.ensure_future(self._flush_private(cid)),
            )

    async def _flush_private(self, chat_id: str) -> None:
        """防抖到期，合并消息并执行 LLM 回复"""
        pending = self._private_pending.pop(chat_id, None)
        if not pending:
            return

        # 合并多条消息为一条
        combined_text = "\n".join(pending["texts"]) if pending["texts"] else ""
        message_id = pending["message_id"]
        sender_name = pending["sender_name"]

        # 构建多模态内容：下载图片并组装 content blocks
        image_msgs: list[IncomingMessage] = pending.get("image_msgs", [])
        if image_msgs:
            content = await self._build_image_content(image_msgs, combined_text)
        else:
            content = combined_text

        if not content:
            return

        # 主人身份自动发现
        self._try_discover_owner(chat_id, sender_name)

        system = self.memory.build_context(chat_id=chat_id)
        system += (
            f"\n\n你正在和用户私聊。当前会话 chat_id={chat_id}。请直接、简洁地回复。"
            "如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
            "需要联网查询时（搜索、天气、新闻等），使用 web_search / web_fetch 工具。"
            "需要计算或处理数据时，使用 run_python 工具。"
            "需要读写文件时，使用 read_file / write_file 工具。"
            "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE)
        )

        # 使用会话管理器维护上下文
        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", content, sender_name=sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": content}]

        # 添加 thinking 信号
        thinking_handle = await self.adapter.start_thinking(message_id) or ""

        # 尝试带工具回复
        try:
            reply_text = await self._reply_with_tool_loop(
                system, messages, chat_id, message_id
            )
        except Exception:
            logger.exception("私聊回复失败 (chat=%s)", chat_id)
            reply_text = ""
        finally:
            if thinking_handle:
                await self.adapter.stop_thinking(message_id, thinking_handle)

        if self.session_mgr and reply_text:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name="你")
            if session.should_compact():
                await self._compact_session(session)

        # 记录日志
        log_preview = combined_text[:50] if combined_text else "[图片]"
        self.memory.append_daily(f"- 私聊 [{sender_name}]: {log_preview}... → {'已回复' if reply_text else '回复失败'}\n", chat_id=chat_id)

        # 异步自我反思（fire-and-forget，不阻塞回复）
        if reply_text:
            asyncio.create_task(self._reflect_on_reply(chat_id, reply_text))

    # ── 自我反思 + 好奇心信号 ──

    async def _reflect_on_reply(self, chat_id: str, reply_text: str) -> None:
        """轻量级 LLM 调用，对刚发出的回复做质量自评 + 好奇心信号检测"""
        try:
            prompt = REFLECTION_WITH_CURIOSITY_PROMPT.format(reply=reply_text[:500])
            reflection = await self.executor.reply_with_history(
                "", [{"role": "user", "content": prompt}], max_tokens=200,
            )
            reflection = reflection.strip()
            if reflection:
                logger.info("自我评估 [%s]: %s", chat_id[-8:], reflection)
                self._append_reflection(chat_id, reflection)
                # 从 JSON 提取好奇心信号
                self._extract_curiosity_from_reflection(reflection, "私聊反思", chat_id)
        except Exception:
            logger.debug("自我反思失败", exc_info=True)

    def _append_reflection(self, chat_id: str, reflection: str) -> None:
        """将反思结果追加到当日反思日志"""
        import json as _json
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"reflections-{today}.jsonl"
        entry = {
            "ts": time.time(),
            "chat_id": chat_id,
            "reflection": reflection,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("反思日志写入失败", exc_info=True)

    def _extract_curiosity_from_reflection(
        self, reflection: str, source: str, chat_id: str,
    ) -> None:
        """从反思 JSON 中提取 curiosity 字段并写入好奇心信号日志"""
        from json_repair import repair_json

        try:
            data = repair_json(reflection, return_objects=True)
        except Exception:
            return
        if not isinstance(data, dict):
            return

        topic = data.get("curiosity")
        if topic and isinstance(topic, str) and topic not in ("null", "无", "None"):
            self._append_curiosity_signal(topic, source, chat_id)

    def _append_curiosity_signal(
        self, topic: str, source: str, chat_id: str,
    ) -> None:
        """将好奇心信号追加到当日信号日志（自动去重）"""
        import json as _json
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"curiosity-signals-{today}.jsonl"

        # 去重：检查今日是否已有相似话题（前 20 字匹配）
        topic_prefix = topic[:20]
        if log_path.exists():
            try:
                for line in log_path.read_text(encoding="utf-8").strip().splitlines():
                    existing = _json.loads(line)
                    if existing.get("topic", "")[:20] == topic_prefix:
                        logger.debug("跳过重复好奇心信号: %s", topic[:40])
                        return
            except Exception:
                pass

        entry = {
            "ts": time.time(),
            "topic": topic,
            "source": source,
            "chat_id": chat_id,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info("好奇心信号: %s (来源: %s)", topic, source)
        except Exception:
            logger.warning("好奇心信号写入失败", exc_info=True)

    def _extract_group_curiosity(
        self, chat_id: str, recent: list[dict], reason: str,
    ) -> None:
        """从群聊对话中被动提取好奇心信号（不调用 LLM）。

        仅当消息足够长且包含技术性关键词组合时才触发，避免误报。
        """
        texts = [m.get("text", "") for m in recent[-5:]]
        # 需要同时包含「动作词」+「对象词」才算有意义的信号
        action_words = ["怎么做", "怎么实现", "有没有办法", "能不能", "如何"]
        for text in texts:
            if len(text) < 15:
                continue
            for aw in action_words:
                if aw in text:
                    topic = text[:60].strip()
                    self._append_curiosity_signal(topic, "群聊旁听", chat_id)
                    return  # 每次评估最多一个信号
