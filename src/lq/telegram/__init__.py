"""Telegram 平台适配器

将 Telegram Bot API 封装为平台无关接口。
使用长轮询 (getUpdates) 模式接收消息，支持 MarkdownV2 格式发送。
"""

from __future__ import annotations

from lq.telegram.adapter import TelegramAdapter

__all__ = ["TelegramAdapter"]
