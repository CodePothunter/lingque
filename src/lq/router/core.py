"""消息路由核心 — MessageRouter 类定义与事件分发"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any

from lq.buffer import MessageBuffer
from lq.executor.api import DirectAPIExecutor
from lq.memory import MemoryManager
from lq.platform import (
    PlatformAdapter, IncomingMessage, OutgoingMessage,
    Reaction, CardAction, ChatType,
)
from lq.prompts import PREAMBLE_STARTS, BOT_POLL_AT_REASON

from .defs import TOOLS
from .private import PrivateChatMixin
from .group import GroupChatMixin
from .tool_loop import ToolLoopMixin
from .tool_exec import ToolExecMixin
from .vision_mcp import VisionMCPMixin
from .web_tools import WebToolsMixin
from .runtime_tools import RuntimeToolsMixin

logger = logging.getLogger(__name__)


class MessageRouter(
    PrivateChatMixin,
    GroupChatMixin,
    ToolLoopMixin,
    ToolExecMixin,
    VisionMCPMixin,
    WebToolsMixin,
    RuntimeToolsMixin,
):
    """消息路由核心：事件分发、回复锁与冷却、thinking 信号。

    具体处理逻辑由各 Mixin 提供：
    - PrivateChatMixin   — 私聊处理与自我反思
    - GroupChatMixin      — 群聊三层介入与协作
    - ToolLoopMixin       — LLM 工具调用循环与审批
    - ToolExecMixin       — 工具执行分发与多模态
    - WebToolsMixin       — 联网搜索、网页抓取
    - RuntimeToolsMixin   — 代码执行、文件读写、统计查询
    """

    REPLY_COOLDOWN: float = 8.0  # 回复后的冷却秒数

    def __init__(
        self,
        executor: DirectAPIExecutor,
        memory: MemoryManager,
        adapter: PlatformAdapter,
        bot_id: str,
        bot_name: str = "",
    ) -> None:
        self.executor = executor
        self.memory = memory
        self.adapter = adapter
        self.bot_open_id = bot_id  # 保留旧名以减少内部改动量
        self.bot_name = bot_name
        # 启动时间戳（毫秒），用于区分历史消息和新消息
        self._startup_ts: int = int(time.time() * 1000)

        # 群聊缓冲区
        self.group_buffers: dict[str, MessageBuffer] = {}
        # 私聊防抖：chat_id → {texts, message_id, timer, event}
        self._private_pending: dict[str, dict] = {}
        self._private_debounce_seconds: float = 1.5
        # 群聊 bot 消息轮询计数（防止 bot 间无限对话，用户消息重置）
        self._bot_poll_count: dict[str, int] = {}
        self._bot_seen_ids: dict[str, set[str]] = {}  # chat_id → 已处理的 message_id
        # 延迟评估定时器：冷却中收到 @提及时，安排冷却结束后重试
        self._deferred_eval_timers: dict[str, asyncio.TimerHandle] = {}
        # 回复去重：chat_id → 上次发送的文本，防止 bot 轮询循环导致重复回复
        self._last_reply_per_chat: dict[str, str] = {}
        # WS 消息去重：飞书偶尔用不同 event_id 重复推送同一 message_id
        self._seen_ws_msg_ids: OrderedDict[str, None] = OrderedDict()
        # Reaction 意图信号：chat_id → {bot_open_id: timestamp}
        self._thinking_signals: dict[str, dict[str, float]] = {}
        # 本实例添加的 reaction_id 用于清理：message_id → reaction_id
        self._my_reaction_ids: dict[str, str] = {}
        # 意图信号使用的 emoji 类型
        self._thinking_emoji: str = "OnIt"
        # ReplyGate: per-chat 回复锁 + 冷却期，防止多路径并发回复同一群
        self._reply_locks: dict[str, asyncio.Lock] = {}
        self._reply_cooldown_ts: dict[str, float] = {}  # chat_id → 上次回复完成的时间戳
        # 工具调用统计（per-tool success/fail）
        self._tool_stats: dict[str, dict[str, int]] = {}
        # 注入依赖
        self.session_mgr: Any = None
        self.calendar: Any = None
        self.stats: Any = None
        self.cc_executor: Any = None
        self.bash_executor: Any = None
        self.tool_registry: Any = None
        self.post_processor: Any = None
        self.config: Any = None  # LQConfig, 由 gateway 注入

    # ── 事件入口 ──

    async def handle(self, data: dict) -> None:
        """处理标准化消息事件"""
        event_type = data.get("event_type")

        if event_type == "message":
            msg: IncomingMessage = data["message"]
            await self._dispatch_message(msg)
        elif event_type == "interaction":
            action: CardAction = data["action"]
            await self._handle_card_action(action)
        elif event_type == "eval_timeout":
            chat_id = data.get("chat_id")
            if chat_id:
                await self._evaluate_buffer(chat_id)
        elif event_type == "reaction":
            reaction: Reaction = data["reaction"]
            self._handle_reaction_event(reaction)
        elif event_type == "member_change":
            await self._handle_member_change(data)
        else:
            logger.debug("忽略事件类型: %s", event_type)

    # ── 回复锁与冷却 ──

    def _reply_is_busy(self, chat_id: str) -> bool:
        """判断该群是否正在回复或在冷却期内"""
        lock = self._reply_locks.get(chat_id)
        if lock and lock.locked():
            return True
        return time.time() - self._reply_cooldown_ts.get(chat_id, 0) < self.REPLY_COOLDOWN

    def _get_reply_lock(self, chat_id: str) -> asyncio.Lock:
        """获取 per-chat 回复锁（懒创建）"""
        if chat_id not in self._reply_locks:
            self._reply_locks[chat_id] = asyncio.Lock()
        return self._reply_locks[chat_id]

    def _remaining_cooldown(self, chat_id: str) -> float:
        """估算冷却剩余秒数（锁持有中按 2 倍冷却估算）"""
        lock = self._reply_locks.get(chat_id)
        if lock and lock.locked():
            return self.REPLY_COOLDOWN * 2
        elapsed = time.time() - self._reply_cooldown_ts.get(chat_id, 0)
        remaining = self.REPLY_COOLDOWN - elapsed
        return max(remaining, 0) + 0.5

    # ── 延迟评估 ──

    def _schedule_deferred_eval(
        self, chat_id: str, msg_id: str = "", at_mention: bool = False,
    ) -> None:
        """冷却中收到 bot 消息时，安排冷却结束后重新触发评估/介入。"""
        old = self._deferred_eval_timers.pop(chat_id, None)
        if old:
            old.cancel()
        delay = self._remaining_cooldown(chat_id)
        try:
            loop = asyncio.get_running_loop()
            handle = loop.call_later(
                delay,
                lambda: asyncio.ensure_future(
                    self._deferred_eval_callback(chat_id, msg_id, at_mention)
                ),
            )
            self._deferred_eval_timers[chat_id] = handle
            logger.info(
                "安排延迟评估: 群 %s, %.1fs 后%s",
                chat_id[-8:], delay, "(@提及)" if at_mention else "",
            )
        except Exception:
            logger.exception("安排延迟评估失败")

    async def _deferred_eval_callback(
        self, chat_id: str, msg_id: str, at_mention: bool,
    ) -> None:
        """延迟评估回调：冷却结束后重新触发。"""
        self._deferred_eval_timers.pop(chat_id, None)

        if self._reply_is_busy(chat_id):
            logger.info("延迟评估仍受阻: 群 %s，放弃", chat_id[-8:])
            return

        if at_mention and msg_id:
            buf = self.group_buffers.get(chat_id)
            if buf:
                recent = buf.get_recent(20)
                judgment = {
                    "intervene": True,
                    "reason": BOT_POLL_AT_REASON.format(bot_name=self.bot_name),
                    "reply_to_message_id": msg_id,
                }
                logger.info("延迟触发 @提及介入: 群 %s", chat_id[-8:])
                await self._intervene(chat_id, recent, judgment)
        else:
            logger.info("延迟触发评估: 群 %s", chat_id[-8:])
            await self._evaluate_buffer(chat_id)

    # ── Reaction 意图信号 ──

    def _handle_reaction_event(self, reaction: Reaction) -> None:
        """处理 reaction 事件，更新 thinking_signals"""
        if not reaction.is_thinking_signal:
            return
        operator_id = reaction.operator_id
        if not operator_id or operator_id == self.bot_open_id:
            return

        chat_id = reaction.chat_id
        if not chat_id:
            # 适配器未能关联 chat_id，尝试从 buffer 查找
            for cid, buf in self.group_buffers.items():
                for m in buf.get_recent(20):
                    if m.get("message_id") == reaction.message_id:
                        chat_id = cid
                        break
                if chat_id:
                    break
            if not chat_id:
                logger.debug("收到 reaction 但无法关联群聊，丢弃: msg=%s", reaction.message_id[-8:])
                return

        signals = self._thinking_signals.setdefault(chat_id, {})
        signals[operator_id] = time.time()
        logger.info("收到意图信号: %s 正在思考 [%s]", operator_id[-6:], chat_id[-8:])

    def _get_thinking_bots(self, chat_id: str) -> list[str]:
        """返回正在思考的其他 bot 名字列表（15 秒内有 thinking 信号的）"""
        signals = self._thinking_signals.get(chat_id, {})
        if not signals:
            return []
        now = time.time()
        active: list[str] = []
        expired: list[str] = []
        for bot_id, ts in signals.items():
            if now - ts > 15:
                expired.append(bot_id)
            elif bot_id != self.bot_open_id:
                active.append(bot_id[-6:])
        for bot_id in expired:
            signals.pop(bot_id, None)
        return active

    # ── 消息分发 ──

    async def _dispatch_message(self, msg: IncomingMessage) -> None:
        """根据消息类型分发"""
        # 忽略自己发的消息
        if msg.sender_id == self.bot_open_id:
            return

        # 去重
        if msg.message_id in self._seen_ws_msg_ids:
            logger.debug("跳过重复消息 %s", msg.message_id)
            return
        self._seen_ws_msg_ids[msg.message_id] = None
        while len(self._seen_ws_msg_ids) > 200:
            self._seen_ws_msg_ids.popitem(last=False)

        if msg.chat_type == ChatType.PRIVATE:
            await self._handle_private(msg)
        elif msg.chat_type == ChatType.GROUP:
            self._bot_poll_count.pop(msg.chat_id, None)
            self._bot_seen_ids.pop(msg.chat_id, None)
            self._last_reply_per_chat.pop(msg.chat_id, None)
            await self._handle_group(msg)

    # ── 工具管理 ──

    def _track_tool_result(self, tool_name: str, success: bool, error: str = "") -> None:
        """记录工具调用成功/失败统计"""
        entry = self._tool_stats.setdefault(tool_name, {"success": 0, "fail": 0, "last_error": ""})
        if success:
            entry["success"] += 1
        else:
            entry["fail"] += 1
            if error:
                entry["last_error"] = error[:200]

    def _build_all_tools(self) -> list[dict]:
        """合并内置工具和自定义工具的定义列表。"""
        all_tools = list(TOOLS)
        if self.tool_registry:
            all_tools.extend(self.tool_registry.get_definitions())
        return all_tools

    @staticmethod
    def _is_action_preamble(text: str) -> bool:
        """判断 LLM 回复是否是未完成的行动前奏。

        只有极短的（≤50字）、以行动短语开头的回复才被视为前奏。
        正常长度的对话回复不会触发，避免误催促。
        """
        text = text.strip()
        if len(text) > 50:
            return False
        return any(text.startswith(p) for p in PREAMBLE_STARTS)

    # 清理 LLM 模仿的元数据格式
    _CLEAN_RE = __import__("re").compile(
        r"\[\d{1,2}:\d{2}(?:\s+[^\]]+)?\]\s*"  # [17:32] 或 [17:32 你]
        r"|<msg\s[^>]*>"                          # <msg time=17:32 from=你>
        r"|</msg>",                                # </msg>
    )

    # ── 发送回复 ──

    async def _send_reply(self, text: str, chat_id: str, reply_to_message_id: str | None) -> None:
        """发送回复"""
        text = self._CLEAN_RE.sub("", text).strip()
        if not text:
            return
        # 去重
        if chat_id and self._last_reply_per_chat.get(chat_id) == text:
            logger.info("跳过重复回复 chat=%s text=%s", chat_id[-8:], text[:60])
            return
        if chat_id:
            self._last_reply_per_chat[chat_id] = text
        reply_to = ""
        if reply_to_message_id and not reply_to_message_id.startswith("inbox_"):
            reply_to = reply_to_message_id
        if chat_id and chat_id != "local_cli":
            await self.adapter.send(OutgoingMessage(chat_id, text, reply_to=reply_to))
        else:
            logger.info("本地回复（未发送）: %s", text[:200])

    # ── 卡片回调 ──

    async def _handle_card_action(self, action: CardAction) -> None:
        """处理卡片交互回调"""
        op_id = action.operator_id[:12] if action.operator_id else ""
        logger.info(
            "卡片回调: action=%s operator=%s value=%s",
            action.action_type, op_id, action.value,
        )

        # 审批卡片回调
        card_type = action.value.get("type", "")
        if card_type == "approval":
            approval_id = action.value.get("id", "")
            status = "approved" if action.action_type == "confirm" else "rejected"
            self._update_approval_status(approval_id, status)
            logger.info("审批 %s: %s (操作者: %s)", approval_id, status, op_id)
            return

        if action.action_type == "confirm":
            logger.info("用户 %s 确认了操作", op_id)
        elif action.action_type == "cancel":
            logger.info("用户 %s 取消了操作", op_id)
        else:
            logger.info("卡片动作: %s", action.action_type)
