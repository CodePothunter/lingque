"""会话管理 — token 感知的上下文窗口管理"""

from __future__ import annotations

import json
import logging
import re
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


def _estimate_content_tokens(content: str | list) -> int:
    """估算 content 的 token 数，支持纯文本和 content blocks 列表。

    图片按 Anthropic 的计费模型估算：大约 1600 tokens / 图片（中等尺寸）。
    """
    if isinstance(content, str):
        return estimate_tokens(content)
    total = 0
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                total += estimate_tokens(block.get("text", ""))
            elif block.get("type") == "image":
                total += 1600  # 图片 token 估算
    return total


def _content_to_text(content: str | list) -> str:
    """将 content（可能是 str 或 blocks 列表）转为纯文本用于摘要/格式化。"""
    if isinstance(content, str):
        return content
    parts = []
    for block in content:
        if isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "image":
                parts.append("[图片]")
    return " ".join(parts)


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

    def add_message(self, role: str, content: str | list, sender_name: str = "") -> None:
        """添加一条消息到历史。

        content 可以是纯文本字符串，也可以是 Anthropic content blocks 列表
        （如包含 image 和 text 的多模态内容）。
        """
        msg: dict = {
            "role": role,
            "content": content,
            "timestamp": time.time(),
        }
        if sender_name:
            msg["sender_name"] = sender_name
        tokens = _estimate_content_tokens(content)
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
            msg_tokens = msg.get("_tokens", _estimate_content_tokens(msg.get("content", "")))
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
        # 建立 tool_use_id -> tool_name 映射（从完整历史中查找）
        tool_name_map: dict[str, str] = {
            m["tool_use_id"]: m.get("tool_name", "?")
            for m in self.messages
            if m.get("is_tool_use") and m.get("tool_use_id")
        }
        # 收集已处理的 tool_use_id（tool_result 随 tool_use 一起合并输出）
        merged_tool_ids: set[str] = set()
        for m in selected:
            if m.get("is_tool_result"):
                # 仅当对应 tool_use 已被合并输出时才跳过
                if m.get("tool_use_id", "") in merged_tool_ids:
                    continue
                # 孤立 tool_result（对应 tool_use 被截断），作为独立消息输出
                content = _content_to_text(m.get("content", ""))
                content = re.sub(r"</?tool_result[^>]*>", "", content).strip()
                if len(content) > 150:
                    content = content[:147] + "..."
                tid = m.get("tool_use_id", "")
                tool_name = tool_name_map.get(tid, "工具")
                summary = f"<tool_record tool=\"{tool_name}\" result=\"{content}\" />"
                result_msgs.append({"role": "user", "content": summary})
                continue

            if m.get("is_tool_use"):
                # 将 tool_use + tool_result 合并为一行摘要
                tool_name = m.get("tool_name", "?")
                tool_use_id = m.get("tool_use_id", "")
                result_preview = self._find_tool_result_preview(tool_use_id, selected)
                summary = f"<tool_record tool=\"{tool_name}\" result=\"{result_preview}\" />"
                result_msgs.append({"role": "user", "content": summary})
                merged_tool_ids.add(tool_use_id)
                continue

            content = self._format_message(m)
            # 多模态消息中图片的 base64 数据不存入历史，
            # 只在当次请求中通过 content blocks 传递。
            # 历史中的图片会在序列化时降级为 [图片] 占位符。
            result_msgs.append({"role": m["role"], "content": content})

        return result_msgs

    def _format_message(self, m: dict) -> str | list:
        """格式化单条消息，注入时间和发送者元数据。

        对纯文本消息返回 str；对多模态消息返回 content blocks 列表，
        其中 text block 会被包裹元数据标签。
        """
        ts = m.get("timestamp")
        name = m.get("sender_name", "")
        content = m.get("content", "")

        meta_parts = []
        if ts:
            t = datetime.fromtimestamp(ts, tz=CST).strftime("%H:%M")
            meta_parts.append(f"time={t}")
        if name:
            meta_parts.append(f"from={name}")

        # 多模态内容：保留 image blocks，仅对 text blocks 包裹元数据
        if isinstance(content, list):
            if not meta_parts:
                return content
            meta = " ".join(meta_parts)
            wrapped = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    text = block.get("text", "")
                    wrapped.append({
                        "type": "text",
                        "text": f"<{TAG_MSG} {meta}>{text}</{TAG_MSG}>",
                    })
                else:
                    wrapped.append(block)
            return wrapped

        # 纯文本
        if meta_parts:
            meta = " ".join(meta_parts)
            return f"<{TAG_MSG} {meta}>{content}</{TAG_MSG}>"
        return content

    def _find_tool_result_preview(
        self, tool_use_id: str, selected: list[dict],
    ) -> str:
        """在 selected 中查找匹配 tool_use_id 的 tool_result，返回结果预览。"""
        for m in selected:
            if m.get("is_tool_result") and m.get("tool_use_id") == tool_use_id:
                content = _content_to_text(m.get("content", ""))
                # 去掉 XML 标签包装，提取纯文本
                content = re.sub(r"</?tool_result[^>]*>", "", content).strip()
                if len(content) > 150:
                    content = content[:147] + "..."
                return content
        return "（无结果）"

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
            msg_tokens = msg.get("_tokens", _estimate_content_tokens(msg.get("content", "")))
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
            msg_tokens = msg.get("_tokens", _estimate_content_tokens(msg.get("content", "")))
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
            m.get("_tokens", _estimate_content_tokens(m.get("content", "")))
            for m in self.messages
        )

    # ── 序列化 ──

    def to_dict(self) -> dict:
        # 序列化时，将多模态 content 降级为纯文本（不持久化 base64 图片数据）
        clean_msgs = []
        for m in self.messages:
            content = m.get("content", "")
            if isinstance(content, list):
                m = dict(m)
                m["content"] = _content_to_text(content)
                m["_tokens"] = estimate_tokens(m["content"])
            clean_msgs.append(m)
        return {
            "chat_id": self.chat_id,
            "messages": clean_msgs,
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

    def get_recent_index(self, max_preview_total: int = 20) -> str:
        """返回有近期对话的会话索引，供心跳 prompt 使用。

        max_preview_total 控制所有 session 合计最多展示多少条预览消息，
        保证 prompt 体积可控。预览条数按各 session 的对话量比例分配：
        聊得多的 session 多给几条，聊得少的少给，最少 2 条最多 6 条。

        格式示例:
        - [奶油] 最后活跃 14:32 · 18 条对话 | sessions/oc_xxx.json
          奶油: "帮我看看这个图片生成的效果"
          你: "图片已经生成好了，不过色调偏暗"
          奶油: "markdown previewer 渲染有问题"
          你: "我看看……是 CSS 的问题"
        """
        # 收集所有有消息的 session，按最后活跃时间倒序
        candidates: list[tuple[float, str, Session]] = []
        for cid, session in self._sessions.items():
            if not session.messages:
                continue
            last_ts = session.messages[-1].get("timestamp", 0)
            candidates.append((last_ts, cid, session))

        if not candidates:
            return ""

        candidates.sort(key=lambda x: x[0], reverse=True)

        # 按对话量比例分配预览条数
        total_msgs = sum(len(s.messages) for _, _, s in candidates)

        lines: list[str] = []
        preview_budget = max_preview_total
        for last_ts, cid, session in candidates:
            if preview_budget <= 0:
                break
            msg_count = len(session.messages)
            ratio = msg_count / total_msgs if total_msgs else 0
            # 按比例分配，但保证每个 session 至少 2 条、最多 6 条
            alloc = max(2, min(round(ratio * max_preview_total), 6))
            alloc = min(alloc, preview_budget)  # 不超过剩余预算

            # 提取 sender_name
            sender_name = ""
            for msg in reversed(session.messages):
                if msg.get("role") == "user" and msg.get("sender_name"):
                    sender_name = msg["sender_name"]
                    break

            # 取最近的 user/assistant 消息保留对话节奏
            trail: list[tuple[str, str]] = []
            for msg in reversed(session.messages):
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue
                if msg.get("is_tool_use") or msg.get("is_tool_result"):
                    continue
                text = _content_to_text(msg.get("content", ""))
                if not text:
                    continue
                label = sender_name or "用户" if role == "user" else "你"
                trail.append((label, text[:60]))
                if len(trail) >= alloc:
                    break
            trail.reverse()

            rel_path = f"sessions/{cid}.json"
            last_time_str = datetime.fromtimestamp(last_ts, tz=CST).strftime("%H:%M")
            lines.append(f"- [{sender_name or cid}] 最后活跃 {last_time_str} · {msg_count} 条对话 | {rel_path}")
            for label, text in trail:
                lines.append(f"  {label}: \"{text}\"")

            preview_budget -= len(trail)

        return "\n".join(lines)

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
