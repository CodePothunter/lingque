"""平台无关数据类型"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ChatType(str, Enum):
    PRIVATE = "private"
    GROUP = "group"


class SenderType(str, Enum):
    USER = "user"
    BOT = "bot"


class MessageType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    RICH_TEXT = "rich_text"
    FILE = "file"
    AUDIO = "audio"
    VIDEO = "video"
    STICKER = "sticker"
    SHARE = "share"
    UNKNOWN = "unknown"


@dataclass
class Mention:
    user_id: str
    name: str
    is_bot_self: bool


@dataclass
class IncomingMessage:
    message_id: str
    chat_id: str
    chat_type: ChatType
    sender_id: str
    sender_type: SenderType
    sender_name: str
    message_type: MessageType
    text: str                             # 已完成占位符替换的最终文本
    mentions: list[Mention] = field(default_factory=list)
    is_mention_bot: bool = False
    image_keys: list[str] = field(default_factory=list)
    reply_to_id: str = ""
    timestamp: int = 0                    # Unix 毫秒
    raw: Any = None                       # 内核不访问


@dataclass
class OutgoingMessage:
    chat_id: str
    text: str = ""
    reply_to: str = ""
    mentions: list[Mention] = field(default_factory=list)
    card: dict | None = None


@dataclass
class BotIdentity:
    bot_id: str
    bot_name: str


@dataclass
class ChatMember:
    user_id: str
    name: str
    is_bot: bool


@dataclass
class Reaction:
    reaction_id: str
    chat_id: str
    message_id: str
    emoji: str
    operator_id: str
    operator_type: SenderType
    is_thinking_signal: bool = False


@dataclass
class CardAction:
    action_type: str
    value: dict = field(default_factory=dict)
    operator_id: str = ""
    message_id: str = ""
