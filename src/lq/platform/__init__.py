"""平台抽象层 — 定义 LingQue 与任意聊天平台的通信协议"""

from __future__ import annotations

from lq.platform.types import (
    ChatType,
    SenderType,
    MessageType,
    Mention,
    IncomingMessage,
    OutgoingMessage,
    BotIdentity,
    ChatMember,
    Reaction,
    CardAction,
)
from lq.platform.adapter import PlatformAdapter

__all__ = [
    "ChatType",
    "SenderType",
    "MessageType",
    "Mention",
    "IncomingMessage",
    "OutgoingMessage",
    "BotIdentity",
    "ChatMember",
    "Reaction",
    "CardAction",
    "PlatformAdapter",
]
