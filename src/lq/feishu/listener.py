"""飞书 WebSocket 事件接收"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import lark_oapi as lark
from lark_oapi.event.dispatcher_handler import EventDispatcherHandlerBuilder

logger = logging.getLogger(__name__)


def _noop(data: Any) -> None:
    """静默忽略不需要处理的事件"""


class FeishuListener:
    """在独立线程中运行飞书 WS 长连接，通过 call_soon_threadsafe 桥接到主 asyncio loop"""

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        queue: asyncio.Queue,
        main_loop: asyncio.AbstractEventLoop,
    ) -> None:
        self.app_id = app_id
        self.app_secret = app_secret
        self.queue = queue
        self.main_loop = main_loop

    def _on_message(self, data: Any) -> None:
        """同步回调 → 通过 call_soon_threadsafe 桥接到主 loop"""
        try:
            event = data.event
            header = data.header
            payload = {
                "event_type": header.event_type,
                "event_id": header.event_id,
                "event": event,
                "header": header,
            }
            self.main_loop.call_soon_threadsafe(self.queue.put_nowait, payload)
            logger.info("事件入队: %s (id=%s)", header.event_type, header.event_id)
        except Exception:
            logger.exception("处理飞书事件失败")

    def _on_reaction(self, data: Any) -> None:
        """Reaction 事件回调 — 用于 bot 间意图信号感知"""
        try:
            event = data.event
            # 提取 reaction 信息
            message_id = getattr(event, "message_id", "") or ""
            operator_type = getattr(event, "operator_type", "") or ""
            reaction_type = getattr(event, "reaction_type", None)
            emoji_type = getattr(reaction_type, "emoji_type", "") if reaction_type else ""
            operator_id = ""
            if hasattr(event, "user_id"):
                uid = event.user_id
                operator_id = getattr(uid, "open_id", "") or ""

            payload = {
                "event_type": "reaction.created",
                "message_id": message_id,
                "operator_id": operator_id,
                "operator_type": operator_type,
                "emoji_type": emoji_type,
            }
            self.main_loop.call_soon_threadsafe(self.queue.put_nowait, payload)
            logger.debug("Reaction 事件入队: %s on %s by %s", emoji_type, message_id[-8:], operator_id[-6:])
        except Exception:
            logger.exception("处理 reaction 事件失败")

    def _on_card_action(self, data: Any) -> Any:
        """卡片交互回调"""
        try:
            payload = {
                "event_type": "card.action.trigger",
                "event": data,
                "header": None,
            }
            self.main_loop.call_soon_threadsafe(self.queue.put_nowait, payload)
        except Exception:
            logger.exception("处理卡片回调失败")
        return None

    def start_blocking(self) -> None:
        """构建 WS 客户端并阻塞运行（在 daemon 线程中调用）"""
        builder = EventDispatcherHandlerBuilder("", "")

        # ── 需要处理的事件 ──
        builder.register_p2_im_message_receive_v1(self._on_message)

        # ── 已订阅但无需处理的事件（注册 _noop 避免 SDK 报错）──
        # IM
        builder.register_p2_im_message_message_read_v1(_noop)
        builder.register_p2_im_message_recalled_v1(_noop)
        builder.register_p2_im_message_reaction_created_v1(self._on_reaction)
        builder.register_p2_im_message_reaction_deleted_v1(_noop)
        builder.register_p2_im_chat_disbanded_v1(_noop)
        builder.register_p2_im_chat_updated_v1(_noop)
        builder.register_p2_im_chat_member_bot_added_v1(_noop)
        builder.register_p2_im_chat_member_bot_deleted_v1(_noop)
        builder.register_p2_im_chat_member_user_added_v1(_noop)
        builder.register_p2_im_chat_member_user_deleted_v1(_noop)
        builder.register_p2_im_chat_member_user_withdrawn_v1(_noop)
        builder.register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(_noop)
        # Calendar
        builder.register_p2_calendar_calendar_changed_v4(_noop)
        builder.register_p2_calendar_calendar_event_changed_v4(_noop)
        builder.register_p2_calendar_calendar_acl_created_v4(_noop)
        builder.register_p2_calendar_calendar_acl_deleted_v4(_noop)

        handler = builder.build()

        ws_client = lark.ws.Client(
            app_id=self.app_id,
            app_secret=self.app_secret,
            event_handler=handler,
            log_level=lark.LogLevel.INFO,
        )

        logger.info("飞书 WebSocket 连接启动中...")
        ws_client.start()  # 阻塞
