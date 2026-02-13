"""群聊消息缓冲区"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable

logger = logging.getLogger(__name__)

# 极短无实质内容的消息，第一层规则硬判断用
TRIVIAL_MESSAGES = frozenset({
    "收到", "好的", "ok", "OK", "Ok", "嗯", "嗯嗯", "哦", "行",
    "了解", "明白", "知道了", "好", "👍", "👌", "🙏", "❤️",
    "谢谢", "感谢", "thx", "thanks", "1", "+1", "666", "haha",
    "哈哈", "哈哈哈", "呵呵", "嘿嘿", "😂", "🤣", "😄",
})


class MessageBuffer:
    """群聊消息缓冲区，支持定时触发评估"""

    def __init__(
        self,
        max_messages: int = 20,
        max_age_seconds: float = 60,
        eval_threshold: int = 5,
    ) -> None:
        self.max_messages = max_messages
        self.max_age_seconds = max_age_seconds
        self.eval_threshold = eval_threshold

        self._messages: deque[dict] = deque(maxlen=max_messages)
        self._new_count: int = 0
        self._timer_handle: asyncio.TimerHandle | None = None

    def add(self, msg: dict) -> None:
        """追加消息"""
        msg["_ts"] = time.time()
        self._messages.append(msg)
        self._new_count += 1

    def get_recent(self, n: int = 10) -> list[dict]:
        """获取最近 n 条消息"""
        msgs = list(self._messages)
        return msgs[-n:]

    def should_evaluate(self) -> bool:
        """新消息数 >= threshold 时应触发评估"""
        return self._new_count >= self.eval_threshold

    def mark_evaluated(self) -> None:
        """标记已评估，重置计数"""
        self._new_count = 0
        self._cancel_timer()

    def schedule_timeout(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: Callable[[], Any],
    ) -> None:
        """设置超时定时器，确保安静群聊也能触发评估"""
        self._cancel_timer()
        self._timer_handle = loop.call_later(self.max_age_seconds, callback)

    def _cancel_timer(self) -> None:
        if self._timer_handle is not None:
            self._timer_handle.cancel()
            self._timer_handle = None

    def to_dict(self) -> dict:
        """序列化（用于持久化）"""
        return {
            "messages": list(self._messages),
            "new_count": self._new_count,
        }

    @classmethod
    def from_dict(cls, data: dict, **kwargs: Any) -> MessageBuffer:
        buf = cls(**kwargs)
        for msg in data.get("messages", []):
            buf._messages.append(msg)
        buf._new_count = data.get("new_count", 0)
        return buf


def rule_check(text: str) -> str:
    """第一层规则硬判断（零 LLM 成本）"""
    stripped = text.strip()
    if stripped in TRIVIAL_MESSAGES:
        return "IGNORE"
    if len(stripped) <= 2 and not any(c.isalnum() for c in stripped):
        return "IGNORE"
    return "UNCERTAIN"
