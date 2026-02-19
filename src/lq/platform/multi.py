"""MultiAdapter — 多平台复合适配器

同时连接多个平台，根据消息来源自动路由回复。
每个平台的事件都汇入同一个 queue；发送时根据 chat_id 路由到来源适配器。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from lq.platform.adapter import PlatformAdapter
from lq.platform.types import BotIdentity, ChatMember, OutgoingMessage

logger = logging.getLogger(__name__)


class MultiAdapter(PlatformAdapter):
    """组合多个 PlatformAdapter，统一对外暴露为单一适配器。

    - connect: 每个子适配器各自连内部队列，forwarder 任务汇入主队列
    - send/start_thinking/stop_thinking: 根据 chat_id/message_id 路由到来源适配器
    - 未知 chat_id 时 fallback 到 primary 适配器
    """

    def __init__(self, adapters: list[PlatformAdapter], primary: PlatformAdapter) -> None:
        self._adapters = adapters
        self._primary = primary
        # chat_id → adapter 映射（由 forwarder 自动建立）
        self._chat_adapter: dict[str, PlatformAdapter] = {}
        # message_id → adapter 映射
        self._msg_adapter: dict[str, PlatformAdapter] = {}
        self._forwarder_tasks: list[asyncio.Task] = []

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        return await self._primary.get_identity()

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        self._main_queue = queue
        for adapter in self._adapters:
            inner_queue: asyncio.Queue = asyncio.Queue()
            await adapter.connect(inner_queue)
            task = asyncio.create_task(
                self._forward(adapter, inner_queue),
                name=f"multi-fwd-{type(adapter).__name__}",
            )
            self._forwarder_tasks.append(task)
        logger.info(
            "MultiAdapter 已连接 %d 个适配器: %s",
            len(self._adapters),
            ", ".join(type(a).__name__ for a in self._adapters),
        )

    async def _forward(self, adapter: PlatformAdapter, inner_queue: asyncio.Queue) -> None:
        """从子适配器的内部队列转发事件到主队列，同时建立路由映射。"""
        while True:
            try:
                event = await inner_queue.get()
            except asyncio.CancelledError:
                return

            # 从事件中提取 chat_id / message_id，建立路由映射
            msg = event.get("message")
            if msg is not None:
                chat_id = getattr(msg, "chat_id", None)
                message_id = getattr(msg, "message_id", None)
                if chat_id:
                    self._chat_adapter[chat_id] = adapter
                if message_id:
                    self._msg_adapter[message_id] = adapter

            # reaction / interaction 事件也可能携带 chat_id
            for key in ("reaction", "interaction"):
                obj = event.get(key)
                if obj is not None:
                    chat_id = getattr(obj, "chat_id", None)
                    if chat_id:
                        self._chat_adapter[chat_id] = adapter

            await self._main_queue.put(event)

    async def disconnect(self) -> None:
        for task in self._forwarder_tasks:
            task.cancel()
        if self._forwarder_tasks:
            await asyncio.gather(*self._forwarder_tasks, return_exceptions=True)
        self._forwarder_tasks.clear()
        for adapter in self._adapters:
            await adapter.disconnect()

    # ── 路由辅助 ──

    def _for_chat(self, chat_id: str) -> PlatformAdapter:
        adapter = self._chat_adapter.get(chat_id)
        if adapter:
            return adapter
        # 根据 chat_id 格式推断归属适配器，避免错误路由
        guessed = self._guess_adapter(chat_id)
        if guessed:
            return guessed
        return self._primary

    def _guess_adapter(self, chat_id: str) -> PlatformAdapter | None:
        """根据 chat_id 格式推断归属适配器。

        飞书 chat_id 以 'oc_'/'ou_'/'on_' 开头；Discord chat_id 是纯数字。
        如果格式与可用适配器不匹配，记录警告并返回 None。
        """
        is_feishu_format = chat_id.startswith(("oc_", "ou_", "on_"))
        is_discord_format = chat_id.isdigit()

        for adapter in self._adapters:
            cls_name = type(adapter).__name__
            if is_feishu_format and "Feishu" in cls_name:
                return adapter
            if is_discord_format and "Discord" in cls_name:
                return adapter

        # 格式与可用适配器不匹配时记录警告
        if is_feishu_format:
            logger.warning(
                "chat_id '%s' 是飞书格式但未启用飞书适配器，将回退到 primary",
                chat_id[:20] + "..." if len(chat_id) > 20 else chat_id,
            )
        elif is_discord_format:
            logger.warning(
                "chat_id '%s' 是 Discord 格式但未启用 Discord 适配器，将回退到 primary",
                chat_id,
            )

        return None

    def _for_msg(self, message_id: str) -> PlatformAdapter:
        return self._msg_adapter.get(message_id, self._primary)

    # ── 表达 ──

    async def send(self, message: OutgoingMessage) -> str | None:
        adapter = self._for_chat(message.chat_id)
        return await adapter.send(message)

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        adapter = self._for_msg(message_id)
        return await adapter.start_thinking(message_id)

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        adapter = self._for_msg(message_id)
        await adapter.stop_thinking(message_id, handle)

    # ── 感官 ──

    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        adapter = self._for_msg(message_id)
        return await adapter.fetch_media(message_id, resource_key)

    # ── 认知 ──

    async def resolve_name(self, user_id: str) -> str:
        # 尝试所有适配器，返回第一个非截断结果
        for adapter in self._adapters:
            name = await adapter.resolve_name(user_id)
            if name and name != user_id[-8:]:
                return name
        return await self._primary.resolve_name(user_id)

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        adapter = self._for_chat(chat_id)
        return await adapter.list_members(chat_id)

    # ── 可选行为 ──

    async def react(self, message_id: str, emoji: str) -> str | None:
        adapter = self._for_msg(message_id)
        return await adapter.react(message_id, emoji)

    async def unreact(self, message_id: str, handle: str) -> bool:
        # message_id 可能也用于 react handle 的查找
        adapter = self._for_msg(message_id)
        return await adapter.unreact(message_id, handle)

    async def edit(self, message_id: str, new_content: OutgoingMessage) -> bool:
        adapter = self._for_msg(message_id)
        return await adapter.edit(message_id, new_content)

    async def unsend(self, message_id: str) -> bool:
        adapter = self._for_msg(message_id)
        return await adapter.unsend(message_id)

    async def notify_queued(self, chat_id: str, count: int) -> None:
        adapter = self._for_chat(chat_id)
        await adapter.notify_queued(chat_id, count)

    # ── 透传属性（供 gateway 的 hasattr 检查使用）──

    def _find_adapter_with_attr(self, attr: str) -> PlatformAdapter | None:
        for adapter in self._adapters:
            if hasattr(adapter, attr):
                return adapter
        return None

    def __getattr__(self, name: str) -> Any:
        """透传未定义属性到拥有该属性的子适配器。

        使 gateway 的 hasattr(adapter, '_sender') / hasattr(adapter, 'feishu_client')
        等检查能正确工作。
        """
        adapter = self._find_adapter_with_attr(name)
        if adapter is not None:
            return getattr(adapter, name)
        raise AttributeError(f"MultiAdapter 及其子适配器均无属性 '{name}'")
