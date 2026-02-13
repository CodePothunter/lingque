"""会话管理"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

MAX_MESSAGES = 50  # compaction 阈值


class Session:
    """单个会话的消息历史"""

    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.messages: list[dict] = []
        self._summary: str = ""

    def add_message(self, role: str, content: str, sender_name: str = "") -> None:
        msg: dict = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if sender_name:
            msg["sender_name"] = sender_name
        self.messages.append(msg)

    def get_messages(self) -> list[dict[str, str]]:
        """返回用于 API 调用的消息列表（包含摘要前缀和时间戳）"""
        msgs = []
        if self._summary:
            msgs.append({
                "role": "user",
                "content": (
                    f"[之前的对话摘要] {self._summary}\n\n"
                    "注意：以上是较早的对话摘要，请更关注下面最近的消息。"
                ),
            })
            msgs.append({
                "role": "assistant",
                "content": "好的，我已了解之前的对话内容。请继续。",
            })
        for m in self.messages:
            ts = m.get("timestamp")
            name = m.get("sender_name", "")
            # 用 XML 属性注入元数据，LLM 能理解但不会模仿到回复里
            parts = []
            if ts:
                t = datetime.fromtimestamp(ts, tz=CST).strftime("%H:%M")
                parts.append(f"time={t}")
            if name:
                parts.append(f"from={name}")
            if parts:
                meta = " ".join(parts)
                content = f"<msg {meta}>{m['content']}</msg>"
            else:
                content = m["content"]
            msgs.append({"role": m["role"], "content": content})
        return msgs

    def should_compact(self) -> bool:
        return len(self.messages) >= MAX_MESSAGES

    def compact(self, summary: str) -> None:
        """压缩旧消息为摘要，保留最近 N 条"""
        keep = 10
        self._summary = summary
        self.messages = self.messages[-keep:]
        logger.info("会话 %s 已压缩，保留 %d 条", self.chat_id, keep)

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "messages": self.messages,
            "summary": self._summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        s = cls(data["chat_id"])
        s.messages = data.get("messages", [])
        s._summary = data.get("summary", "")
        return s


class SessionManager:
    """管理所有活跃会话"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.sessions_dir = workspace / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._load()

    def get_or_create(self, chat_id: str) -> Session:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = Session(chat_id)
        return self._sessions[chat_id]

    def save(self) -> None:
        """保存所有活跃会话"""
        path = self.sessions_dir / "current.json"
        data = {cid: s.to_dict() for cid, s in self._sessions.items()}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def archive(self, chat_id: str, slug: str = "") -> None:
        """归档会话"""
        session = self._sessions.get(chat_id)
        if not session:
            return
        archive_dir = self.sessions_dir / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{chat_id}_{date.today().isoformat()}"
        if slug:
            fname += f"_{slug}"
        fname += ".json"
        with open(archive_dir / fname, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        del self._sessions[chat_id]

    def _load(self) -> None:
        path = self.sessions_dir / "current.json"
        if not path.exists():
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for cid, sdata in data.items():
                self._sessions[cid] = Session.from_dict(sdata)
            logger.info("加载了 %d 个活跃会话", len(self._sessions))
        except (json.JSONDecodeError, KeyError):
            logger.warning("会话文件加载失败")
