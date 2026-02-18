"""PlatformAdapter — 平台适配器抽象基类"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod

from lq.platform.types import (
    BotIdentity,
    ChatMember,
    OutgoingMessage,
)


class PlatformAdapter(ABC):
    """所有平台适配器的基类。

    核心 8 个方法（必须实现）+ 4 个可选行为（默认空实现）。
    内核直接调方法、看返回值，不查询能力、不做条件分支。
    """

    # ── 身份 ──

    @abstractmethod
    async def get_identity(self) -> BotIdentity:
        """我是谁。启动时调用，内核用 bot_id 过滤自己的消息。"""
        ...

    # ── 感知 ──

    @abstractmethod
    async def connect(self, queue: asyncio.Queue) -> None:
        """开始感知世界。

        建立与平台的连接，将所有事件转换为标准格式后投入 queue。
        适配器负责维护连接存活性（自动重连），对内核透明。
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """停止感知，释放资源。"""
        ...

    # ── 表达 ──

    @abstractmethod
    async def send(self, message: OutgoingMessage) -> str | None:
        """说话。返回 message_id，失败返回 None。"""
        ...

    # ── 存在感 ──

    @abstractmethod
    async def start_thinking(self, message_id: str) -> str | None:
        """表达"我收到了，正在处理"。返回 handle 用于 stop_thinking。"""
        ...

    @abstractmethod
    async def stop_thinking(self, message_id: str, handle: str) -> None:
        """清除"正在处理"信号。"""
        ...

    # ── 感官 ──

    @abstractmethod
    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        """获取消息中的媒体内容。返回 (base64_data, mime_type) 或 None。"""
        ...

    # ── 认知 ──

    @abstractmethod
    async def resolve_name(self, user_id: str) -> str:
        """这个 ID 是谁？查不到返回 ID 尾部截断。"""
        ...

    @abstractmethod
    async def list_members(self, chat_id: str) -> list[ChatMember]:
        """这个群里有谁？返回完整成员列表（含 bot）。"""
        ...

    # ── 可选行为（默认空实现）──

    async def react(self, message_id: str, emoji: str) -> str | None:
        """对一条消息做出表情反应。"""
        return None

    async def unreact(self, message_id: str, handle: str) -> bool:
        """撤销之前的表情反应。"""
        return False

    async def edit(self, message_id: str, new_content: OutgoingMessage) -> bool:
        """修改已发的消息。不支持时返回 False。"""
        return False

    async def unsend(self, message_id: str) -> bool:
        """撤回已发的消息。不支持时返回 False。"""
        return False

    async def notify_queued(self, chat_id: str, count: int) -> None:
        """通知适配器：用户消息已加入防抖队列（共 count 条）。"""
        pass
