"""会话管理 — token 感知的上下文窗口管理"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from lq.prompts import (
    TAG_MSG, TAG_TOOL_CALL, TAG_TOOL_RESULT, TAG_CONTEXT_SUMMARY,
    wrap_tag,
    CONTEXT_SUMMARY_USER, CONTEXT_SUMMARY_ACK,
)

CST = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

# ── Token 估算 ──

def estimate_tokens(text: str) -> int:
    """粗略估算文本的 token 数。

    中文约 1.5 token/字符，英文约 0.75 token/单词(~4字符)。
    混合内容取加权平均。实际误差约 ±20%，用于预算控制足够。
    """
    if not text:
        return 0
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3000' <= c <= '\u303f'
                    or '\uff00' <= c <= '\uffef')
    ascii_count = len(text) - cjk_count
    return int(cjk_count * 1.5 + ascii_count * 0.3)


# ── 常量 ──

# token 预算：为对话历史保留的最大 token 数
# Claude 的上下文窗口为 200k，预留 system prompt + 输出空间
MAX_CONTEXT_TOKENS = 40_000
# 压缩后目标 token 数（保留多少近期消息）
COMPACT_TARGET_TOKENS = 15_000
# 触发压缩的 token 阈值
COMPACT_THRESHOLD_TOKENS = 30_000
# 兼容旧逻辑：最大消息条数（作为备用触发器）
MAX_MESSAGES = 80
# 压缩后最多保留的消息条数（防止短消息场景 token 限额失效）
COMPACT_MAX_KEEP = 30


class Session:
    """单个会话的消息历史，支持 token 感知的上下文管理"""

    def __init__(self, chat_id: str) -> None:
        self.chat_id = chat_id
        self.messages: list[dict] = []
        self._summary: str = ""
        self._total_tokens: int = 0  # 缓存的 token 计数
        self._dirty: bool = False  # 标记自上次保存后是否有变动

    # ── 消息管理 ──

    def add_message(self, role: str, content: str, sender_name: str = "") -> None:
        """添加一条消息到历史"""
        msg: dict = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if sender_name:
            msg["sender_name"] = sender_name
        tokens = estimate_tokens(content)
        msg["_tokens"] = tokens
        self.messages.append(msg)
        self._total_tokens += tokens
        self._dirty = True

    def add_tool_use(self, tool_name: str, tool_input: dict, tool_use_id: str) -> None:
        """记录工具调用（assistant 角色的 tool_use）"""
        # 用简洁格式存储，避免占用过多 token
        input_str = json.dumps(tool_input, ensure_ascii=False)
        if len(input_str) > 500:
            input_str = input_str[:497] + "..."
        content = wrap_tag(TAG_TOOL_CALL, input_str, name=tool_name)
        tokens = estimate_tokens(content)
        self.messages.append({
            "role": "assistant",
            "content": content,
            "timestamp": time.time(),
            "is_tool_use": True,
            "tool_name": tool_name,
            "tool_use_id": tool_use_id,
            "_tokens": tokens,
        })
        self._total_tokens += tokens
        self._dirty = True

    def add_tool_result(self, tool_use_id: str, result: str) -> None:
        """记录工具执行结果"""
        # 截断过长的结果
        if len(result) > 1000:
            result = result[:997] + "..."
        content = wrap_tag(TAG_TOOL_RESULT, result)
        tokens = estimate_tokens(content)
        self.messages.append({
            "role": "user",
            "content": content,
            "timestamp": time.time(),
            "is_tool_result": True,
            "tool_use_id": tool_use_id,
            "_tokens": tokens,
        })
        self._total_tokens += tokens
        self._dirty = True

    # ── 上下文构建 ──

    def get_messages(self, token_budget: int = MAX_CONTEXT_TOKENS) -> list[dict[str, str]]:
        """返回用于 API 调用的消息列表，遵守 token 预算。

        策略：从最新消息往前取，直到用完预算。
        如果有摘要，在开头注入摘要上下文。
        """
        result_msgs: list[dict[str, str]] = []
        budget = token_budget

        # 预留摘要空间
        summary_tokens = 0
        if self._summary:
            summary_tokens = estimate_tokens(self._summary) + 50  # 包装文本的开销
            budget -= summary_tokens

        # 从后往前收集消息，直到预算用完
        selected: list[dict] = []
        for msg in reversed(self.messages):
            msg_tokens = msg.get("_tokens", estimate_tokens(msg.get("content", "")))
            if budget - msg_tokens < 0 and selected:
                # 预算不够且已有消息，停止
                break
            selected.append(msg)
            budget -= msg_tokens

        selected.reverse()

        # 注入摘要
        if self._summary:
            result_msgs.append({
                "role": "user",
                "content": (
                    f"{wrap_tag(TAG_CONTEXT_SUMMARY, self._summary)}\n\n"
                    f"{CONTEXT_SUMMARY_USER}"
                ),
            })
            result_msgs.append({
                "role": "assistant",
                "content": CONTEXT_SUMMARY_ACK,
            })

        # 格式化消息
        for m in selected:
            # 跳过工具记录（它们只用于摘要提取，不直接发给 API）
            if m.get("is_tool_use") or m.get("is_tool_result"):
                continue

            content = self._format_message(m)
            result_msgs.append({"role": m["role"], "content": content})

        return result_msgs

    def _format_message(self, m: dict) -> str:
        """格式化单条消息，注入时间和发送者元数据"""
        ts = m.get("timestamp")
        name = m.get("sender_name", "")
        content = m.get("content", "")

        parts = []
        if ts:
            t = datetime.fromtimestamp(ts, tz=CST).strftime("%H:%M")
            parts.append(f"time={t}")
        if name:
            parts.append(f"from={name}")

        if parts:
            meta = " ".join(parts)
            return f"<{TAG_MSG} {meta}>{content}</{TAG_MSG}>"
        return content

    # ── 压缩策略 ──

    def should_compact(self) -> bool:
        """判断是否需要压缩。使用 token 计数和消息条数双重触发。"""
        if self._total_tokens >= COMPACT_THRESHOLD_TOKENS:
            return True
        if len(self.messages) >= MAX_MESSAGES:
            return True
        return False

    def compact(self, summary: str) -> None:
        """压缩旧消息为摘要，保留近期消息直到目标 token 数。

        双重限制：token 预算 + 最大条数，取较严格的那个。
        防止短消息（群聊常见）场景下 token 限额无法有效缩减条数。
        """
        # 从后往前保留消息，直到达到目标 token 数或条数上限
        kept: list[dict] = []
        budget = COMPACT_TARGET_TOKENS
        for msg in reversed(self.messages):
            if len(kept) >= COMPACT_MAX_KEEP:
                break
            msg_tokens = msg.get("_tokens", estimate_tokens(msg.get("content", "")))
            if budget - msg_tokens < 0 and kept:
                break
            kept.append(msg)
            budget -= msg_tokens
        kept.reverse()

        old_count = len(self.messages)
        self._summary = summary
        self.messages = kept
        self._recalc_tokens()
        self._dirty = True
        logger.info(
            "会话 %s 已压缩: %d → %d 条, ~%d tokens",
            self.chat_id, old_count, len(kept), self._total_tokens,
        )

    def get_compaction_context(self) -> list[dict]:
        """返回将被压缩的旧消息（用于生成摘要）"""
        # 计算要保留的消息数（与 compact() 逻辑一致：token + 条数双重限制）
        budget = COMPACT_TARGET_TOKENS
        keep_count = 0
        keep_from = len(self.messages)
        for i in range(len(self.messages) - 1, -1, -1):
            if keep_count >= COMPACT_MAX_KEEP:
                break
            msg = self.messages[i]
            msg_tokens = msg.get("_tokens", estimate_tokens(msg.get("content", "")))
            if budget - msg_tokens < 0:
                break
            keep_from = i
            keep_count += 1
            budget -= msg_tokens
        # 返回将被压缩掉的消息
        return self.messages[:keep_from]

    def _recalc_tokens(self) -> None:
        """重新计算总 token 数"""
        self._total_tokens = sum(
            m.get("_tokens", estimate_tokens(m.get("content", "")))
            for m in self.messages
        )

    # ── 序列化 ──

    def to_dict(self) -> dict:
        return {
            "chat_id": self.chat_id,
            "messages": self.messages,
            "summary": self._summary,
            "total_tokens": self._total_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        s = cls(data["chat_id"])
        s.messages = data.get("messages", [])
        s._summary = data.get("summary", "")
        # 重新计算 token（兼容旧数据没有 _tokens 字段的情况）
        s._recalc_tokens()
        return s


class SessionManager:
    """管理所有活跃会话，每个 chat_id 独立存储一个 JSON 文件"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.sessions_dir = workspace / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self._sessions: dict[str, Session] = {}
        self._load()

    def _session_path(self, chat_id: str) -> Path:
        """返回指定 chat_id 的 session 文件路径"""
        return self.sessions_dir / f"{chat_id}.json"

    def get_or_create(self, chat_id: str) -> Session:
        if chat_id not in self._sessions:
            self._sessions[chat_id] = Session(chat_id)
        return self._sessions[chat_id]

    def save(self) -> None:
        """保存有变动的会话（每个 chat_id 独立文件，原子写入）"""
        for cid, session in self._sessions.items():
            if not session._dirty:
                continue
            path = self._session_path(cid)
            tmp = path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
            tmp.replace(path)
            session._dirty = False
        logger.debug("会话保存完成（%d 个活跃会话）", len(self._sessions))

    def save_one(self, chat_id: str) -> None:
        """立即保存单个会话（用于关键操作后确保持久化）"""
        session = self._sessions.get(chat_id)
        if not session:
            return
        path = self._session_path(chat_id)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(session.to_dict(), f, ensure_ascii=False, indent=2)
        tmp.replace(path)
        session._dirty = False

    def archive(self, chat_id: str, slug: str = "") -> None:
        """归档会话：移动到 archive/ 目录并从活跃列表中移除"""
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
        # 删除活跃 session 文件
        active_path = self._session_path(chat_id)
        if active_path.exists():
            active_path.unlink()
        del self._sessions[chat_id]

    def get_stats(self) -> dict:
        """返回所有会话的统计信息"""
        stats = {}
        for cid, session in self._sessions.items():
            stats[cid] = {
                "messages": len(session.messages),
                "tokens": session._total_tokens,
                "has_summary": bool(session._summary),
            }
        return stats

    def _load(self) -> None:
        """加载所有活跃会话：优先读 per-chat 文件，兼容旧版 current.json"""
        loaded = 0

        # 1. 读取 per-chat 独立文件（新格式）
        #    oc_* = 飞书会话, local_* = 本地 CLI 会话
        for pattern in ("oc_*.json", "local_*.json"):
            for f in self.sessions_dir.glob(pattern):
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    cid = data["chat_id"]
                    self._sessions[cid] = Session.from_dict(data)
                    loaded += 1
                except (json.JSONDecodeError, KeyError):
                    logger.warning("会话文件加载失败: %s", f.name)

        # 2. 兼容旧版：如果存在 current.json，迁移其中的 session
        legacy_path = self.sessions_dir / "current.json"
        if legacy_path.exists():
            try:
                with open(legacy_path, encoding="utf-8") as f:
                    data = json.load(f)
                migrated = 0
                for cid, sdata in data.items():
                    if cid not in self._sessions:  # 新文件优先，不覆盖
                        self._sessions[cid] = Session.from_dict(sdata)
                        self._sessions[cid]._dirty = True  # 标记需要写入新文件
                        migrated += 1
                if migrated:
                    # 保存为独立文件
                    self.save()
                    logger.info("从 current.json 迁移了 %d 个会话到独立文件", migrated)
                # 迁移完成后删除旧文件
                legacy_path.unlink()
                logger.info("已删除旧版 current.json")
            except (json.JSONDecodeError, KeyError):
                logger.warning("旧版会话文件加载失败: current.json")

        if loaded:
            logger.info("加载了 %d 个活跃会话", len(self._sessions))
