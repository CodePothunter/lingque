"""消息路由 + 介入判断 + 工具调用"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict
from typing import Any

from lq.buffer import MessageBuffer, rule_check
from lq.executor.api import DirectAPIExecutor
from lq.memory import MemoryManager
from lq.platform import PlatformAdapter, IncomingMessage, OutgoingMessage, Reaction, CardAction, ChatType, SenderType, MessageType
from lq.prompts import (
    TAG_CONSTRAINTS, TAG_MEMORY_GUIDANCE, TAG_GROUP_CONTEXT,
    wrap_tag,
    CONSTRAINTS_PRIVATE, CONSTRAINTS_GROUP,
    MEMORY_GUIDANCE_PRIVATE, MEMORY_GUIDANCE_GROUP,
    PRIVATE_SYSTEM_SUFFIX, GROUP_AT_SYSTEM_SUFFIX, GROUP_INTERVENE_SYSTEM_SUFFIX,
    GROUP_EVAL_PROMPT,
    PREAMBLE_STARTS, ACTION_NUDGE,
    NON_TEXT_REPLY_PRIVATE, NON_TEXT_REPLY_GROUP, EMPTY_AT_FALLBACK,
    RESULT_GLOBAL_MEMORY_WRITTEN, RESULT_CHAT_MEMORY_WRITTEN,
    RESULT_CARD_SENT, RESULT_FILE_EMPTY, RESULT_FILE_UPDATED,
    RESULT_SEND_FAILED, RESULT_SCHEDULE_OK,
    ERR_CALENDAR_NOT_LOADED, ERR_TOOL_REGISTRY_NOT_LOADED,
    ERR_CC_NOT_LOADED, ERR_BASH_NOT_LOADED, ERR_UNKNOWN_TOOL,
    ERR_TIME_FORMAT_INVALID, ERR_TIME_PAST, ERR_CODE_VALIDATION_OK,
    ERR_FILE_NOT_FOUND, ERR_FILE_READ_FAILED, ERR_FILE_WRITE_FAILED,
    RESULT_FILE_WRITTEN,
    DAILY_LOG_PRIVATE, DAILY_LOG_STATUS_OK, DAILY_LOG_STATUS_FAIL,
    GROUP_MSG_SELF, GROUP_MSG_OTHER,
    GROUP_MSG_WITH_ID_SELF, GROUP_MSG_WITH_ID_OTHER,
    GROUP_CONTEXT_HEADER,
    COMPACTION_DAILY_HEADER, COMPACTION_MEMORY_HEADER, COMPACTION_SUMMARY_PROMPT,
    FLUSH_NO_RESULT,
    SENDER_SELF, SENDER_UNKNOWN, SENDER_GROUP,
    BOT_POLL_AT_REASON,
    BOT_SELF_INTRO_SYSTEM, BOT_SELF_INTRO_USER,
    USER_WELCOME_SYSTEM, USER_WELCOME_USER,
    TOOL_DESC_WRITE_MEMORY, TOOL_DESC_WRITE_CHAT_MEMORY,
    TOOL_DESC_CALENDAR_CREATE, TOOL_DESC_CALENDAR_LIST,
    TOOL_DESC_SEND_CARD, TOOL_DESC_READ_SELF_FILE, TOOL_DESC_WRITE_SELF_FILE,
    TOOL_DESC_CREATE_CUSTOM_TOOL, TOOL_DESC_LIST_CUSTOM_TOOLS,
    TOOL_DESC_TEST_CUSTOM_TOOL, TOOL_DESC_DELETE_CUSTOM_TOOL,
    TOOL_DESC_TOGGLE_CUSTOM_TOOL, TOOL_DESC_SEND_MESSAGE,
    TOOL_DESC_SCHEDULE_MESSAGE, TOOL_DESC_RUN_CLAUDE_CODE, TOOL_DESC_RUN_BASH,
    TOOL_DESC_WEB_SEARCH, TOOL_DESC_WEB_FETCH,
    TOOL_DESC_RUN_PYTHON, TOOL_DESC_READ_FILE, TOOL_DESC_WRITE_FILE,
    TOOL_DESC_GET_MY_STATS, TOOL_FIELD_STATS_CATEGORY,
    REFLECTION_PROMPT, REFLECTION_WITH_CURIOSITY_PROMPT,
    TOOL_FIELD_SECTION, TOOL_FIELD_CONTENT_MEMORY,
    TOOL_FIELD_CHAT_SECTION, TOOL_FIELD_CHAT_CONTENT,
    TOOL_FIELD_SUMMARY, TOOL_FIELD_START_TIME, TOOL_FIELD_END_TIME,
    TOOL_FIELD_EVENT_DESC, TOOL_FIELD_QUERY_START, TOOL_FIELD_QUERY_END,
    TOOL_FIELD_CARD_TITLE, TOOL_FIELD_CARD_CONTENT, TOOL_FIELD_CARD_COLOR,
    TOOL_FIELD_FILENAME_READ, TOOL_FIELD_FILENAME_WRITE, TOOL_FIELD_FILE_CONTENT,
    TOOL_FIELD_TOOL_NAME, TOOL_FIELD_TOOL_CODE,
    TOOL_FIELD_VALIDATE_CODE, TOOL_FIELD_DELETE_NAME,
    TOOL_FIELD_TOGGLE_NAME, TOOL_FIELD_TOGGLE_ENABLED,
    TOOL_FIELD_CHAT_ID, TOOL_FIELD_TEXT, TOOL_FIELD_SCHEDULE_TEXT, TOOL_FIELD_SEND_AT,
    SCHEDULED_ACTION_PROMPT,
    TOOL_FIELD_CC_PROMPT, TOOL_FIELD_WORKING_DIR, TOOL_FIELD_CC_TIMEOUT,
    TOOL_FIELD_BASH_COMMAND, TOOL_FIELD_BASH_TIMEOUT,
    TOOL_FIELD_SEARCH_QUERY, TOOL_FIELD_SEARCH_MAX_RESULTS,
    TOOL_FIELD_FETCH_URL, TOOL_FIELD_FETCH_MAX_LENGTH,
    TOOL_FIELD_PYTHON_CODE, TOOL_FIELD_PYTHON_TIMEOUT,
    TOOL_FIELD_FILE_PATH, TOOL_FIELD_FILE_MAX_LINES,
    TOOL_FIELD_WRITE_PATH, TOOL_FIELD_WRITE_CONTENT,
)

logger = logging.getLogger(__name__)

# LLM 可调用的工具定义
TOOLS = [
    {
        "name": "write_memory",
        "description": TOOL_DESC_WRITE_MEMORY,
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": TOOL_FIELD_SECTION,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_CONTENT_MEMORY,
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "write_chat_memory",
        "description": TOOL_DESC_WRITE_CHAT_MEMORY,
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_SECTION,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_CONTENT,
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": TOOL_DESC_CALENDAR_CREATE,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": TOOL_FIELD_SUMMARY},
                "start_time": {
                    "type": "string",
                    "description": TOOL_FIELD_START_TIME,
                },
                "end_time": {
                    "type": "string",
                    "description": TOOL_FIELD_END_TIME,
                },
                "description": {
                    "type": "string",
                    "description": TOOL_FIELD_EVENT_DESC,
                    "default": "",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": TOOL_DESC_CALENDAR_LIST,
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": TOOL_FIELD_QUERY_START,
                },
                "end_time": {
                    "type": "string",
                    "description": TOOL_FIELD_QUERY_END,
                },
            },
            "required": ["start_time", "end_time"],
        },
    },
    {
        "name": "send_card",
        "description": TOOL_DESC_SEND_CARD,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": TOOL_FIELD_CARD_TITLE},
                "content": {"type": "string", "description": TOOL_FIELD_CARD_CONTENT},
                "color": {
                    "type": "string",
                    "description": TOOL_FIELD_CARD_COLOR,
                    "enum": ["blue", "green", "orange", "red", "purple"],
                    "default": "blue",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "read_self_file",
        "description": TOOL_DESC_READ_SELF_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": TOOL_FIELD_FILENAME_READ,
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"],
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": "write_self_file",
        "description": TOOL_DESC_WRITE_SELF_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": TOOL_FIELD_FILENAME_WRITE,
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"],
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_FILE_CONTENT,
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_custom_tool",
        "description": TOOL_DESC_CREATE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_TOOL_NAME},
                "code": {
                    "type": "string",
                    "description": TOOL_FIELD_TOOL_CODE,
                },
            },
            "required": ["name", "code"],
        },
    },
    {
        "name": "list_custom_tools",
        "description": TOOL_DESC_LIST_CUSTOM_TOOLS,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "test_custom_tool",
        "description": TOOL_DESC_TEST_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": TOOL_FIELD_VALIDATE_CODE},
            },
            "required": ["code"],
        },
    },
    {
        "name": "delete_custom_tool",
        "description": TOOL_DESC_DELETE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_DELETE_NAME},
            },
            "required": ["name"],
        },
    },
    {
        "name": "toggle_custom_tool",
        "description": TOOL_DESC_TOGGLE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_TOGGLE_NAME},
                "enabled": {"type": "boolean", "description": TOOL_FIELD_TOGGLE_ENABLED},
            },
            "required": ["name", "enabled"],
        },
    },
    {
        "name": "send_message",
        "description": TOOL_DESC_SEND_MESSAGE,
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_ID,
                },
                "text": {
                    "type": "string",
                    "description": TOOL_FIELD_TEXT,
                },
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "schedule_message",
        "description": TOOL_DESC_SCHEDULE_MESSAGE,
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_ID,
                },
                "text": {
                    "type": "string",
                    "description": TOOL_FIELD_SCHEDULE_TEXT,
                },
                "send_at": {
                    "type": "string",
                    "description": TOOL_FIELD_SEND_AT,
                },
            },
            "required": ["chat_id", "text", "send_at"],
        },
    },
    {
        "name": "run_claude_code",
        "description": TOOL_DESC_RUN_CLAUDE_CODE,
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": TOOL_FIELD_CC_PROMPT,
                },
                "working_dir": {
                    "type": "string",
                    "description": TOOL_FIELD_WORKING_DIR,
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_CC_TIMEOUT,
                    "default": 300,
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "run_bash",
        "description": TOOL_DESC_RUN_BASH,
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": TOOL_FIELD_BASH_COMMAND,
                },
                "working_dir": {
                    "type": "string",
                    "description": TOOL_FIELD_WORKING_DIR,
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_BASH_TIMEOUT,
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": TOOL_DESC_WEB_SEARCH,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": TOOL_FIELD_SEARCH_QUERY,
                },
                "max_results": {
                    "type": "integer",
                    "description": TOOL_FIELD_SEARCH_MAX_RESULTS,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": TOOL_DESC_WEB_FETCH,
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": TOOL_FIELD_FETCH_URL,
                },
                "max_length": {
                    "type": "integer",
                    "description": TOOL_FIELD_FETCH_MAX_LENGTH,
                    "default": 8000,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_python",
        "description": TOOL_DESC_RUN_PYTHON,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": TOOL_FIELD_PYTHON_CODE,
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_PYTHON_TIMEOUT,
                    "default": 30,
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": TOOL_DESC_READ_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": TOOL_FIELD_FILE_PATH,
                },
                "max_lines": {
                    "type": "integer",
                    "description": TOOL_FIELD_FILE_MAX_LINES,
                    "default": 500,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": TOOL_DESC_WRITE_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": TOOL_FIELD_WRITE_PATH,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_WRITE_CONTENT,
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_my_stats",
        "description": TOOL_DESC_GET_MY_STATS,
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": TOOL_FIELD_STATS_CATEGORY,
                    "enum": ["today", "month", "capability"],
                    "default": "today",
                },
            },
        },
    },
]


class MessageRouter:
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



    # ── Reaction 意图信号处理 ──

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

    async def _handle_private(self, msg: IncomingMessage) -> None:
        """处理私聊消息（带防抖：短时间连发多条会合并后统一处理）"""
        text = msg.text
        has_images = bool(msg.image_keys)

        if not text and not has_images:
            if msg.message_type not in (MessageType.TEXT, MessageType.RICH_TEXT, MessageType.IMAGE):
                await self.adapter.send(OutgoingMessage(
                    msg.chat_id, NON_TEXT_REPLY_PRIVATE, reply_to=msg.message_id,
                ))
            return

        chat_id = msg.chat_id
        sender_name = msg.sender_name
        log_preview = text[:80] if text else "[图片]"
        logger.info("收到私聊 [%s]: %s", sender_name, log_preview)

        # 防抖：收集连续消息，延迟后统一处理
        pending = self._private_pending.get(chat_id)
        if pending:
            if text:
                pending["texts"].append(text)
            if has_images:
                pending.setdefault("image_msgs", []).append(msg)
            if pending.get("timer"):
                pending["timer"].cancel()
            # 通知适配器：消息正在排队
            count = len(pending["texts"])
            if count > 1:
                await self.adapter.notify_queued(chat_id, count)
            loop = asyncio.get_running_loop()
            pending["timer"] = loop.call_later(
                self._private_debounce_seconds,
                lambda cid=chat_id: asyncio.ensure_future(self._flush_private(cid)),
            )
        else:
            entry: dict[str, Any] = {
                "texts": [text] if text else [],
                "message_id": msg.message_id,
                "sender_name": sender_name,
                "timer": None,
            }
            if has_images:
                entry["image_msgs"] = [msg]
            self._private_pending[chat_id] = entry
            loop = asyncio.get_running_loop()
            self._private_pending[chat_id]["timer"] = loop.call_later(
                self._private_debounce_seconds,
                lambda cid=chat_id: asyncio.ensure_future(self._flush_private(cid)),
            )

    async def _flush_private(self, chat_id: str) -> None:
        """防抖到期，合并消息并执行 LLM 回复"""
        pending = self._private_pending.pop(chat_id, None)
        if not pending:
            return

        # 合并多条消息为一条
        combined_text = "\n".join(pending["texts"]) if pending["texts"] else ""
        message_id = pending["message_id"]
        sender_name = pending["sender_name"]

        # 构建多模态内容：下载图片并组装 content blocks
        image_msgs: list[IncomingMessage] = pending.get("image_msgs", [])
        if image_msgs:
            content = await self._build_image_content(image_msgs, combined_text)
        else:
            content = combined_text

        if not content:
            return

        # 主人身份自动发现
        self._try_discover_owner(chat_id, sender_name)

        system = self.memory.build_context(chat_id=chat_id)
        system += (
            f"\n\n你正在和用户私聊。当前会话 chat_id={chat_id}。请直接、简洁地回复。"
            "如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
            "需要联网查询时（搜索、天气、新闻等），使用 web_search / web_fetch 工具。"
            "需要计算或处理数据时，使用 run_python 工具。"
            "需要读写文件时，使用 read_file / write_file 工具。"
            "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE)
        )

        # 使用会话管理器维护上下文
        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", content, sender_name=sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": content}]

        # 添加 thinking 信号
        thinking_handle = await self.adapter.start_thinking(message_id) or ""

        # 尝试带工具回复
        try:
            reply_text = await self._reply_with_tool_loop(
                system, messages, chat_id, message_id
            )
        except Exception:
            logger.exception("私聊回复失败 (chat=%s)", chat_id)
            reply_text = ""
        finally:
            if thinking_handle:
                await self.adapter.stop_thinking(message_id, thinking_handle)

        if self.session_mgr and reply_text:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name="你")
            if session.should_compact():
                await self._compact_session(session)

        # 记录日志
        log_preview = combined_text[:50] if combined_text else "[图片]"
        self.memory.append_daily(f"- 私聊 [{sender_name}]: {log_preview}... → {'已回复' if reply_text else '回复失败'}\n", chat_id=chat_id)

        # 异步自我反思（fire-and-forget，不阻塞回复）
        if reply_text:
            asyncio.create_task(self._reflect_on_reply(chat_id, reply_text))

    async def _reflect_on_reply(self, chat_id: str, reply_text: str) -> None:
        """轻量级 LLM 调用，对刚发出的回复做质量自评 + 好奇心信号检测"""
        try:
            prompt = REFLECTION_WITH_CURIOSITY_PROMPT.format(reply=reply_text[:500])
            reflection = await self.executor.reply_with_history(
                "", [{"role": "user", "content": prompt}], max_tokens=150,
            )
            reflection = reflection.strip()
            if reflection:
                logger.info("自我评估 [%s]: %s", chat_id[-8:], reflection)
                self._append_reflection(chat_id, reflection)
                # 提取好奇心信号
                self._extract_curiosity_from_reflection(reflection, "私聊反思", chat_id)
        except Exception:
            logger.debug("自我反思失败", exc_info=True)

    def _append_reflection(self, chat_id: str, reflection: str) -> None:
        """将反思结果追加到当日反思日志"""
        import json as _json
        from datetime import date as _date, datetime as _dt, timedelta as _td, timezone as _tz

        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"reflections-{today}.jsonl"
        entry = {
            "ts": time.time(),
            "chat_id": chat_id,
            "reflection": reflection,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception:
            logger.debug("反思日志写入失败", exc_info=True)

    def _extract_curiosity_from_reflection(
        self, reflection: str, source: str, chat_id: str,
    ) -> None:
        """从反思结果中提取 [好奇:...] 标记并写入好奇心信号日志"""
        import re as _re
        match = _re.search(r"\[好奇[:：]\s*(.+?)\]", reflection)
        if match:
            topic = match.group(1).strip()
            if topic and topic != "无":
                self._append_curiosity_signal(topic, source, chat_id)

    def _append_curiosity_signal(
        self, topic: str, source: str, chat_id: str,
    ) -> None:
        """将好奇心信号追加到当日信号日志（自动去重）"""
        import json as _json
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz

        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"curiosity-signals-{today}.jsonl"

        # 去重：检查今日是否已有相似话题（前 20 字匹配）
        topic_prefix = topic[:20]
        if log_path.exists():
            try:
                for line in log_path.read_text(encoding="utf-8").strip().splitlines():
                    existing = _json.loads(line)
                    if existing.get("topic", "")[:20] == topic_prefix:
                        logger.debug("跳过重复好奇心信号: %s", topic[:40])
                        return
            except Exception:
                pass

        entry = {
            "ts": time.time(),
            "topic": topic,
            "source": source,
            "chat_id": chat_id,
        }
        try:
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info("好奇心信号: %s (来源: %s)", topic, source)
        except Exception:
            logger.warning("好奇心信号写入失败", exc_info=True)

    def _extract_group_curiosity(
        self, chat_id: str, recent: list[dict], reason: str,
    ) -> None:
        """从群聊对话中被动提取好奇心信号（不调用 LLM）。

        仅当消息足够长且包含技术性关键词组合时才触发，避免误报。
        """
        texts = [m.get("text", "") for m in recent[-5:]]
        # 需要同时包含「动作词」+「对象词」才算有意义的信号
        action_words = ["怎么做", "怎么实现", "有没有办法", "能不能", "如何"]
        for text in texts:
            if len(text) < 15:
                continue
            for aw in action_words:
                if aw in text:
                    topic = text[:60].strip()
                    self._append_curiosity_signal(topic, "群聊旁听", chat_id)
                    return  # 每次评估最多一个信号

    # ── 审批机制 ──

    async def _request_owner_approval(
        self, action_desc: str, callback_id: str,
    ) -> None:
        """向主人发送审批卡片"""
        owner_chat_id = ""
        if self.config:
            owner_chat_id = self.config.feishu.owner_chat_id
        if not owner_chat_id:
            logger.warning("无法发送审批: 未配置 owner_chat_id")
            return

        card = {
            "type": "confirm",
            "title": "操作审批",
            "content": action_desc,
            "confirm_text": "批准",
            "cancel_text": "拒绝",
            "callback_data": {"type": "approval", "id": callback_id},
        }
        await self.adapter.send(OutgoingMessage(owner_chat_id, card=card))

        # 记录待审批
        import json as _json
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "pending-approvals.jsonl"
        entry = {
            "id": callback_id,
            "ts": time.time(),
            "action": action_desc,
            "status": "pending",
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("审批请求已发送: %s", callback_id)

    def _update_approval_status(self, callback_id: str, status: str) -> None:
        """更新审批记录状态"""
        import json as _json

        log_dir = self.memory.workspace / "logs"
        log_path = log_dir / "pending-approvals.jsonl"
        if not log_path.exists():
            return
        try:
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            updated = []
            for line in lines:
                entry = _json.loads(line)
                if entry.get("id") == callback_id:
                    entry["status"] = status
                    entry["resolved_ts"] = time.time()
                updated.append(_json.dumps(entry, ensure_ascii=False))
            log_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("更新审批状态失败", exc_info=True)

    def _check_approval(self, callback_id: str) -> str | None:
        """检查审批状态，返回 'approved'/'rejected'/None(pending)"""
        import json as _json

        log_dir = self.memory.workspace / "logs"
        log_path = log_dir / "pending-approvals.jsonl"
        if not log_path.exists():
            return None
        try:
            for line in log_path.read_text(encoding="utf-8").strip().splitlines():
                entry = _json.loads(line)
                if entry.get("id") == callback_id:
                    status = entry.get("status", "pending")
                    return status if status != "pending" else None
        except Exception:
            pass
        return None

    # ── 主人身份自动发现 ──

    def _try_discover_owner(self, chat_id: str, sender_name: str) -> None:
        """尝试自动发现主人身份（首个私聊用户或名字匹配的用户）"""
        if not self.config:
            return
        # 已有 owner_chat_id，不需要发现
        if self.config.feishu.owner_chat_id:
            return
        # 如果配置了 owner_name，只匹配该名字
        if self.config.owner_name:
            if sender_name != self.config.owner_name:
                return
        # 设置 owner_chat_id（首个私聊用户或名字匹配的用户）
        self.config.feishu.owner_chat_id = chat_id
        if not self.config.owner_name:
            self.config.owner_name = sender_name
        # 持久化到 config.json
        try:
            from lq.config import save_config
            save_config(self.memory.workspace, self.config)
            logger.info("主人身份已发现并保存: %s (chat_id: %s)", sender_name, chat_id[-8:])
        except Exception:
            logger.warning("主人身份保存失败", exc_info=True)
        # 刷新自我认知缓存
        self.memory.invalidate_awareness_cache()

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

    async def _reply_with_tool_loop(
        self,
        system: str,
        messages: list[dict],
        chat_id: str,
        reply_to_message_id: str,
        text_transform: Any = None,
        allow_nudge: bool = True,
    ) -> str:
        """执行带工具调用的完整对话循环。

        支持更长的工具调用链（最多 20 轮），适应 Claude Code 和 Bash
        等需要多步骤执行的复杂任务。工具调用记录会写入会话历史。
        Per-chat 互斥锁确保同一群聊不会并发回复。
        """
        lock = self._get_reply_lock(chat_id)
        if lock.locked():
            logger.info("跳过回复: 群 %s 已有回复进行中", chat_id[-8:])
            return ""
        async with lock:
            return await self._reply_with_tool_loop_inner(
                system, messages, chat_id, reply_to_message_id,
                text_transform, allow_nudge,
            )

    async def _reply_with_tool_loop_inner(
        self,
        system: str,
        messages: list[dict],
        chat_id: str,
        reply_to_message_id: str,
        text_transform: Any = None,
        allow_nudge: bool = True,
    ) -> str:
        """_reply_with_tool_loop 的实际实现（已持锁）。"""
        all_tools = self._build_all_tools()
        tool_names = [t["name"] for t in all_tools]
        logger.debug("工具循环开始: chat=%s 共 %d 个工具 %s", chat_id[-8:], len(all_tools), tool_names)
        resp = await self.executor.reply_with_tools(system, messages, all_tools)

        # 复杂任务（如 Claude Code 执行）可能需要更多轮次
        max_iterations = 20
        iteration = 0
        nudge_count = 0
        tools_called: list[str] = []
        sent_to_current_chat = False  # 是否已通过 send_message 向当前 chat 发送过

        while iteration < max_iterations:
            iteration += 1

            if resp.pending and resp.tool_calls:
                # LLM 调用了工具 → 执行并继续
                tool_results = []
                for tc in resp.tool_calls:
                    tools_called.append(tc["name"])
                    # 记录工具调用到会话历史
                    if self.session_mgr:
                        session = self.session_mgr.get_or_create(chat_id)
                        session.add_tool_use(tc["name"], tc["input"], tc["id"])
                    result = await self._execute_tool(tc["name"], tc["input"], chat_id)
                    # 记录工具调用统计
                    self._track_tool_result(
                        tc["name"],
                        result.get("success", True),
                        result.get("error", ""),
                    )
                    # 标记是否已通过 send_message 向当前 chat 发送过
                    if tc["name"] == "send_message" and result.get("success"):
                        target = tc["input"].get("chat_id", "")
                        if not target or target == chat_id:
                            sent_to_current_chat = True
                    result_str = json.dumps(result, ensure_ascii=False)
                    tool_results.append({
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })
                    # 记录工具结果到会话历史
                    if self.session_mgr:
                        session = self.session_mgr.get_or_create(chat_id)
                        session.add_tool_result(tc["id"], result_str)

                # 工具执行后刷新工具列表（可能有新工具被创建）
                all_tools = self._build_all_tools()
                # 创建自定义工具后失效自我认知缓存
                if "create_custom_tool" in tools_called or "delete_custom_tool" in tools_called:
                    self.memory.invalidate_awareness_cache()
                resp = await self.executor.continue_after_tools(
                    system, resp.messages, all_tools, tool_results, resp.raw_response
                )
            elif (
                allow_nudge
                and resp.text
                and nudge_count < 1
                and self._is_action_preamble(resp.text)
            ):
                nudge_count += 1
                logger.info(
                    "检测到行动前奏，催促执行 (%d/1) 原文: %s",
                    nudge_count, resp.text[:100],
                )
                continued_messages = resp.messages + [
                    {"role": "user", "content": ACTION_NUDGE}
                ]
                resp = await self.executor.reply_with_tools(
                    system, continued_messages, all_tools
                )
            else:
                break

        # 发送最终文本回复（如果已通过 send_message 发到当前 chat 则跳过，避免重复）
        if resp.text and not sent_to_current_chat:
            # 先清理 LLM 模仿的元数据标签，再做 transform
            cleaned = self._CLEAN_RE.sub("", resp.text).strip()
            final = text_transform(cleaned) if text_transform else cleaned
            logger.info("回复: %s", final[:80])
            await self._send_reply(final, chat_id, reply_to_message_id)
            resp.text = final
        elif sent_to_current_chat:
            logger.info("跳过最终回复: 已通过 send_message 发送到当前 chat")

        # 后处理：检测未执行的意图并补救
        if self.post_processor and resp.text:
            original_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    original_user_msg = m["content"]
                    break
            if original_user_msg:
                try:
                    await self.post_processor.process(
                        original_user_msg, resp.text, tools_called,
                        chat_id, reply_to_message_id,
                    )
                except Exception:
                    logger.exception("PostProcessor failed")

        self._reply_cooldown_ts[chat_id] = time.time()
        return resp.text

    # 清理 LLM 模仿的元数据格式
    _CLEAN_RE = __import__("re").compile(
        r"\[\d{1,2}:\d{2}(?:\s+[^\]]+)?\]\s*"  # [17:32] 或 [17:32 你]
        r"|<msg\s[^>]*>"                          # <msg time=17:32 from=你>
        r"|</msg>",                                # </msg>
    )

    async def _send_tool_notification(
        self, text: str, chat_id: str, reply_to_message_id: str | None,
    ) -> None:
        """发送工具执行通知（卡片消息）。"""
        card = {"type": "info", "title": "", "content": text}
        try:
            reply_to = ""
            if reply_to_message_id and not reply_to_message_id.startswith("inbox_"):
                reply_to = reply_to_message_id
            if chat_id and chat_id != "local_cli":
                await self.adapter.send(OutgoingMessage(chat_id, text, reply_to=reply_to, card=card))
        except Exception:
            logger.exception("工具通知发送失败")

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

    async def _execute_tool(self, name: str, input_data: dict, chat_id: str) -> dict:
        """执行单个工具调用"""
        logger.info("执行工具: %s(%s)", name, json.dumps(input_data, ensure_ascii=False)[:100])

        try:
            if name == "write_memory":
                self.memory.update_memory(
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_GLOBAL_MEMORY_WRITTEN}

            elif name == "write_chat_memory":
                self.memory.update_chat_memory(
                    chat_id,
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_CHAT_MEMORY_WRITTEN}

            elif name == "calendar_create_event":
                if not self.calendar:
                    return {"success": False, "error": ERR_CALENDAR_NOT_LOADED}
                result = await self.calendar.create_event(
                    summary=input_data["summary"],
                    start_time=input_data["start_time"],
                    end_time=input_data["end_time"],
                    description=input_data.get("description", ""),
                )
                return result

            elif name == "calendar_list_events":
                if not self.calendar:
                    return {"success": False, "error": ERR_CALENDAR_NOT_LOADED}
                events = await self.calendar.list_events(
                    input_data["start_time"],
                    input_data["end_time"],
                )
                return {"success": True, "events": events}

            elif name == "send_card":
                card = {
                    "type": "info",
                    "title": input_data["title"],
                    "content": input_data["content"],
                    "color": input_data.get("color", "blue"),
                }
                await self.adapter.send(OutgoingMessage(chat_id, card=card))
                return {"success": True, "message": RESULT_CARD_SENT}

            elif name == "read_self_file":
                content = self.memory.read_self_file(input_data["filename"])
                if not content:
                    return {"success": True, "content": RESULT_FILE_EMPTY}
                return {"success": True, "content": content}

            elif name == "write_self_file":
                self.memory.write_self_file(
                    input_data["filename"],
                    input_data["content"],
                )
                return {"success": True, "message": RESULT_FILE_UPDATED.format(filename=input_data['filename'])}

            elif name == "create_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.create_tool(
                    input_data["name"],
                    input_data["code"],
                )

            elif name == "list_custom_tools":
                if not self.tool_registry:
                    return {"success": True, "tools": []}
                return {"success": True, "tools": self.tool_registry.list_tools()}

            elif name == "test_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                errors = self.tool_registry.validate_code(input_data["code"])
                if errors:
                    return {"success": False, "errors": errors}
                return {"success": True, "message": ERR_CODE_VALIDATION_OK}

            elif name == "delete_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.delete_tool(input_data["name"])

            elif name == "toggle_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": ERR_TOOL_REGISTRY_NOT_LOADED}
                return self.tool_registry.toggle_tool(
                    input_data["name"],
                    input_data["enabled"],
                )

            elif name == "send_message":
                target = input_data.get("chat_id", "")
                if not target.startswith(("oc_", "ou_", "on_")) or len(target) < 20:
                    target = chat_id  # LLM 给了无效或截断的 ID，回退到当前会话
                text_to_send = input_data["text"]
                msg_id = await self.adapter.send(
                    OutgoingMessage(target, text_to_send)
                )
                if msg_id:
                    return {"success": True, "message_id": msg_id}
                return {"success": False, "error": RESULT_SEND_FAILED}

            elif name == "schedule_message":
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td

                send_at_str = input_data["send_at"]
                try:
                    send_at = _dt.fromisoformat(send_at_str)
                except ValueError:
                    return {"success": False, "error": ERR_TIME_FORMAT_INVALID.format(value=send_at_str)}

                cst = _tz(_td(hours=8))
                now = _dt.now(cst)
                if send_at.tzinfo is None:
                    send_at = send_at.replace(tzinfo=cst)
                delay = (send_at - now).total_seconds()
                if delay < 0:
                    return {"success": False, "error": ERR_TIME_PAST}

                target_chat_id = input_data.get("chat_id", "")
                if not target_chat_id.startswith(("oc_", "ou_", "on_")) or len(target_chat_id) < 20:
                    target_chat_id = chat_id  # LLM 给了无效或截断的 ID，回退到当前会话
                instruction = input_data["text"]
                router_ref = self

                async def _delayed_action():
                    await asyncio.sleep(delay)
                    try:
                        system = router_ref.memory.build_context(
                            chat_id=target_chat_id,
                        )
                        system += SCHEDULED_ACTION_PROMPT.format(
                            instruction=instruction, chat_id=target_chat_id,
                        )
                        messages = [{"role": "user", "content": instruction}]
                        result = await router_ref._reply_with_tool_loop(
                            system, messages, target_chat_id, None,
                        )
                        logger.info(
                            "定时任务已执行: chat=%s result=%s",
                            target_chat_id, (result or "")[:80],
                        )
                    except Exception:
                        logger.exception("定时任务执行失败: chat=%s", target_chat_id)

                asyncio.ensure_future(_delayed_action())
                return {"success": True, "message": RESULT_SCHEDULE_OK.format(send_at=send_at_str)}

            elif name == "run_claude_code":
                if not self.cc_executor:
                    return {"success": False, "error": ERR_CC_NOT_LOADED}
                result = await self.cc_executor.execute_with_context(
                    prompt=input_data["prompt"],
                    working_dir=input_data.get("working_dir", ""),
                    timeout=input_data.get("timeout", 300),
                )
                return result

            elif name == "run_bash":
                if not self.bash_executor:
                    return {"success": False, "error": ERR_BASH_NOT_LOADED}
                result = await self.bash_executor.execute(
                    command=input_data["command"],
                    working_dir=input_data.get("working_dir", ""),
                    timeout=input_data.get("timeout", 60),
                )
                return result

            elif name == "web_search":
                return await self._tool_web_search(
                    input_data["query"],
                    input_data.get("max_results", 5),
                )

            elif name == "web_fetch":
                return await self._tool_web_fetch(
                    input_data["url"],
                    input_data.get("max_length", 8000),
                )

            elif name == "run_python":
                return await self._tool_run_python(
                    input_data["code"],
                    input_data.get("timeout", 30),
                )

            elif name == "read_file":
                return self._tool_read_file(
                    input_data["path"],
                    input_data.get("max_lines", 500),
                )

            elif name == "write_file":
                return self._tool_write_file(
                    input_data["path"],
                    input_data["content"],
                )

            elif name == "get_my_stats":
                return self._tool_get_my_stats(
                    input_data.get("category", "today"),
                )

            else:
                # 尝试自定义工具注册表
                if self.tool_registry and self.tool_registry.has_tool(name):
                    import httpx
                    async with httpx.AsyncClient() as http_client:
                        context = {
                            "adapter": self.adapter,
                            "memory": self.memory,
                            "calendar": self.calendar,
                            "http": http_client,
                        }
                        return await self.tool_registry.execute(name, input_data, context)
                return {"success": False, "error": ERR_UNKNOWN_TOOL.format(name=name)}

        except Exception as e:
            logger.exception("工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    # ── 泛化工具实现 ──

    @staticmethod
    def _build_http_client(**kwargs: Any) -> Any:
        """构建代理感知的 httpx.AsyncClient。

        自动从环境变量读取代理配置（HTTPS_PROXY / HTTP_PROXY / ALL_PROXY），
        使用通用 User-Agent 避免被目标站点拦截。
        """
        import os
        import httpx

        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or os.environ.get("all_proxy")
        )

        defaults: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": 20.0,
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        }
        if proxy:
            defaults["proxy"] = proxy
        defaults.update(kwargs)
        return httpx.AsyncClient(**defaults)

    # ── MCP 联网搜索（智谱 web-search-prime）──

    _mcp_session_id: str | None = None

    async def _mcp_request(
        self,
        method: str,
        params: dict | None = None,
        *,
        is_notification: bool = False,
    ) -> dict | None:
        """向智谱 MCP 服务器发送 JSON-RPC 请求。

        支持 Streamable HTTP 传输：自动处理 application/json 和 text/event-stream 两种响应。
        """
        import httpx

        mcp_url = "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"
        mcp_key = getattr(self.executor, "mcp_key", "")
        if not mcp_key:
            raise ValueError("未配置 MCP API Key（ZHIPU_API_KEY）")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {mcp_key}",
        }
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            payload["id"] = hash((method, time.time())) & 0x7FFFFFFF
        if params:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(mcp_url, json=payload, headers=headers)
            resp.raise_for_status()

            # 缓存 session ID
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._mcp_session_id = sid

            if is_notification:
                return None

            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                # 从 SSE 流中提取最后一个 JSON-RPC 响应
                last_data: dict | None = None
                for line in resp.text.splitlines():
                    if line.startswith("data:"):
                        raw = line[5:].lstrip()
                        if not raw:
                            continue
                        try:
                            last_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                return last_data or {}
            return resp.json()

    async def _ensure_mcp_session(self) -> None:
        """确保 MCP 会话已初始化（带缓存，避免每次搜索都握手）。"""
        if self._mcp_session_id:
            return
        await self._mcp_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lingque", "version": "1.0.0"},
        })
        await self._mcp_request("notifications/initialized", is_notification=True)

    async def _tool_web_search(self, query: str, max_results: int = 5) -> dict:
        """通过智谱 MCP web-search-prime 搜索互联网"""
        try:
            # 首次调用需初始化 MCP 会话
            try:
                await self._ensure_mcp_session()
            except Exception:
                # 会话可能已过期，重置后重试
                self._mcp_session_id = None
                await self._ensure_mcp_session()

            resp = await self._mcp_request("tools/call", {
                "name": "webSearchPrime",
                "arguments": {"search_query": query},
            })

            if not resp or "result" not in resp:
                # 会话过期时服务器可能返回错误，重置重试一次
                if resp and resp.get("error"):
                    logger.warning("MCP 搜索返回错误，重置会话重试: %s", resp["error"])
                    self._mcp_session_id = None
                    await self._ensure_mcp_session()
                    resp = await self._mcp_request("tools/call", {
                        "name": "webSearchPrime",
                        "arguments": {"search_query": query},
                    })

            if not resp or "result" not in resp:
                error_msg = (resp or {}).get("error", {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", "未知错误")
                return {"success": False, "error": f"MCP 搜索失败: {error_msg}"}

            # 解析 MCP 工具返回的 content 列表
            content_blocks = resp["result"].get("content", [])
            raw_text = "\n".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )

            if not raw_text.strip():
                return {
                    "success": True,
                    "query": query,
                    "results": [],
                    "count": 0,
                    "engine": "zhipu_mcp",
                }

            # 尝试从文本中解析结构化搜索结果
            results = self._parse_mcp_search_results(raw_text, max_results)
            return {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
                "engine": "zhipu_mcp",
            }

        except Exception as e:
            logger.exception("MCP 联网搜索失败: %s", query)
            self._mcp_session_id = None  # 重置会话以便下次重新初始化
            return {"success": False, "error": f"搜索失败: {e}"}

    @staticmethod
    def _parse_mcp_search_results(raw_text: str, max_results: int) -> list[dict]:
        """解析 MCP webSearchPrime 返回的搜索结果。

        兼容多种格式：JSON 数组、JSON 对象（含 results 字段）、纯文本。
        """
        # 1) 尝试整体解析为 JSON
        try:
            data = json.loads(raw_text)
            if isinstance(data, list):
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url") or item.get("link", ""),
                        "snippet": item.get("snippet") or item.get("content") or item.get("description", ""),
                    }
                    for item in data[:max_results]
                    if isinstance(item, dict)
                ]
            if isinstance(data, dict):
                items = data.get("results") or data.get("items") or data.get("data", [])
                if isinstance(items, list):
                    return [
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url") or item.get("link", ""),
                            "snippet": item.get("snippet") or item.get("content") or item.get("description", ""),
                        }
                        for item in items[:max_results]
                        if isinstance(item, dict)
                    ]
        except (json.JSONDecodeError, TypeError):
            pass

        # 2) 纯文本：将原始内容作为单条结果返回，由 LLM 自行理解
        return [{"title": "搜索结果", "url": "", "snippet": raw_text[:3000]}]

    async def _tool_web_fetch(self, url: str, max_length: int = 8000) -> dict:
        """抓取网页并提取纯文本内容"""
        import re as _re

        if not url.startswith(("http://", "https://")):
            return {"success": False, "error": "URL 必须以 http:// 或 https:// 开头"}

        try:
            async with self._build_http_client(timeout=20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                raw = resp.text

            # 非 HTML 内容直接返回
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                text = raw[:max_length]
                if len(raw) > max_length:
                    text += f"\n... (内容已截断，原始长度 {len(raw)} 字符)"
                return {"success": True, "url": url, "content": text, "type": content_type}

            # HTML → 纯文本
            # 移除 script/style 标签及其内容
            text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
            # 将 <br>, <p>, <div>, <li>, <tr> 等块级标签转为换行
            text = _re.sub(r"<(?:br|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=_re.IGNORECASE)
            # 移除所有 HTML 标签
            text = _re.sub(r"<[^>]+>", "", text)
            # 解码常见 HTML 实体
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            text = text.replace("&quot;", '"').replace("&apos;", "'").replace("&nbsp;", " ")
            # 合并多个空行
            text = _re.sub(r"\n{3,}", "\n\n", text)
            # 移除行首尾空白
            text = "\n".join(line.strip() for line in text.splitlines())
            text = text.strip()

            # 截断
            if len(text) > max_length:
                text = text[:max_length] + f"\n... (内容已截断，原始长度 {len(text)} 字符)"

            return {"success": True, "url": url, "content": text, "length": len(text)}
        except Exception as e:
            logger.exception("web_fetch 失败: %s", url)
            return {"success": False, "error": f"网页抓取失败: {e}"}

    async def _tool_run_python(self, code: str, timeout: int = 30) -> dict:
        """在子进程中执行 Python 代码"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.memory.workspace),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            # 截断过长输出
            if len(output) > 10000:
                output = output[:10000] + f"\n... (输出已截断，共 {len(stdout)} 字节)"
            if len(error) > 5000:
                error = error[:5000] + f"\n... (错误输出已截断)"

            exit_code = proc.returncode or 0
            return {
                "success": exit_code == 0,
                "output": output,
                "error": error,
                "exit_code": exit_code,
            }
        except asyncio.TimeoutError:
            logger.error("run_python 超时 (%ds)", timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "", "error": f"执行超时 ({timeout}s)", "exit_code": -1}
        except Exception as e:
            logger.exception("run_python 失败")
            return {"success": False, "output": "", "error": str(e), "exit_code": -1}

    def _tool_get_my_stats(self, category: str = "today") -> dict:
        """返回自身运行统计信息"""
        result: dict[str, Any] = {"success": True}
        if category == "today" and self.stats:
            daily = self.stats.get_daily_summary()
            result["today"] = daily
            result["uptime"] = self._format_uptime()
            result["model"] = getattr(self.executor, "model", "unknown")
        elif category == "month" and self.stats:
            monthly = self.stats.get_monthly_summary()
            result["month"] = monthly
        elif category == "capability":
            result["tool_stats"] = {
                name: {
                    "total": s["success"] + s["fail"],
                    "success_rate": round(s["success"] / max(s["success"] + s["fail"], 1) * 100),
                    "last_error": s.get("last_error", ""),
                }
                for name, s in self._tool_stats.items()
                if s["success"] + s["fail"] > 0
            }
        else:
            result["message"] = "统计模块未加载或类别无效"
        return result

    def _format_uptime(self) -> str:
        """格式化运行时间"""
        elapsed = int(time.time()) - self._startup_ts // 1000
        if elapsed < 60:
            return f"{elapsed}秒"
        if elapsed < 3600:
            return f"{elapsed // 60}分钟"
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        if hours < 24:
            return f"{hours}小时{minutes}分钟"
        days = hours // 24
        hours = hours % 24
        return f"{days}天{hours}小时"

    def _tool_read_file(self, path: str, max_lines: int = 500) -> dict:
        """读取文件系统中的文件"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        if not file_path.exists():
            return {"success": False, "error": ERR_FILE_NOT_FOUND.format(path=str(file_path))}

        if not file_path.is_file():
            return {"success": False, "error": f"路径不是文件: {file_path}"}

        try:
            # 检查文件大小，避免读取超大文件
            size = file_path.stat().st_size
            if size > 5_000_000:  # 5MB
                return {
                    "success": False,
                    "error": f"文件过大 ({size} 字节，上限 5MB)，请使用 run_bash 的 head/tail 命令读取部分内容",
                }

            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total_lines = len(lines)

            if total_lines > max_lines:
                text = "\n".join(lines[:max_lines])
                text += f"\n... (已显示前 {max_lines} 行，共 {total_lines} 行)"

            return {
                "success": True,
                "path": str(file_path),
                "content": text,
                "lines": min(total_lines, max_lines),
                "total_lines": total_lines,
                "size": size,
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_READ_FAILED.format(error=str(e))}

    def _tool_write_file(self, path: str, content: str) -> dict:
        """写入文件到文件系统"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        try:
            # 自动创建父目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            size = file_path.stat().st_size
            logger.info("write_file: %s (%d 字节)", file_path, size)
            return {
                "success": True,
                "message": RESULT_FILE_WRITTEN.format(path=str(file_path), size=size),
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_WRITE_FAILED.format(error=str(e))}

    async def _handle_group(self, msg: IncomingMessage) -> None:
        """处理群聊消息"""
        chat_id = msg.chat_id

        if msg.is_mention_bot:
            # 先把 @at 消息也写入缓冲区，保留完整上下文
            if msg.text:
                if chat_id not in self.group_buffers:
                    self.group_buffers[chat_id] = MessageBuffer()
                self.group_buffers[chat_id].add({
                    "text": msg.text,
                    "sender_id": msg.sender_id,
                    "sender_name": msg.sender_name,
                    "message_id": msg.message_id,
                    "chat_id": chat_id,
                })
            await self._handle_group_at(msg)
            return

        # 第一层规则：极短无实质消息直接忽略
        text = msg.text
        if not text:
            logger.debug("群聊旁听: 非文本消息，跳过")
            return
        if rule_check(text) == "IGNORE":
            logger.debug("群聊旁听: 无实质消息，跳过: %s", text[:20])
            return

        logger.info("群聊旁听 [%s] %s: %s", chat_id[-8:], msg.sender_name, text[:50])

        # 第二层：缓冲区
        if chat_id not in self.group_buffers:
            self.group_buffers[chat_id] = MessageBuffer()

        buf = self.group_buffers[chat_id]
        buf.add({
            "text": text,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "message_id": msg.message_id,
            "chat_id": chat_id,
        })

        # bot 消息限流：防止 bot 间无限对话循环
        if msg.sender_type == SenderType.BOT:
            count = self._bot_poll_count.get(chat_id, 0)
            if count >= 5:
                logger.debug("群 %s bot 消息已达上限，跳过评估", chat_id[-8:])
                return
            self._bot_poll_count[chat_id] = count + 1

        if buf.should_evaluate():
            logger.info("群聊缓冲区已满 (%d条)，触发评估", buf._new_count)
            await self._evaluate_buffer(chat_id)
        else:
            # 消息数未达阈值，设置超时定时器确保安静群聊也能触发评估
            logger.info("群聊缓冲 %d/%d，%ds 后超时评估", buf._new_count, buf.eval_threshold, int(buf.max_age_seconds))
            loop = asyncio.get_running_loop()
            buf.schedule_timeout(loop, lambda cid=chat_id: asyncio.ensure_future(self._evaluate_buffer(cid)))

    async def _handle_group_at(self, msg: IncomingMessage) -> None:
        """处理群聊 @at 消息 — 必须回复"""
        text = msg.text
        has_images = bool(msg.image_keys)

        if not text and not has_images:
            if msg.message_type not in (MessageType.TEXT, MessageType.RICH_TEXT, MessageType.IMAGE):
                await self.adapter.send(OutgoingMessage(
                    msg.chat_id, NON_TEXT_REPLY_GROUP, reply_to=msg.message_id,
                ))
            return

        if not text and not has_images:
            # 空 @：从缓冲区取该用户最近的消息作为上下文
            buf = self.group_buffers.get(msg.chat_id)
            if buf:
                recent = buf.get_recent(20)
                sender_msgs = [m["text"] for m in recent if m["sender_id"] == msg.sender_id]
                if sender_msgs:
                    text = sender_msgs[-1]
                    logger.info("群聊 @at 空消息，取缓冲区上文: %s", text[:50])
            if not text:
                text = EMPTY_AT_FALLBACK

        log_preview = text[:50] if text else "[图片]"
        logger.info("群聊 @at [%s]: %s", msg.sender_name, log_preview)

        # 构建群聊上下文
        group_context = ""
        buf = self.group_buffers.get(msg.chat_id)
        if buf:
            recent = buf.get_recent(10)
            if recent:
                lines = []
                for m in recent:
                    name = m.get("sender_name", "未知")
                    if m.get("sender_id") == self.bot_open_id:
                        lines.append(f"{name}（你自己）：{m['text']}")
                    else:
                        lines.append(f"{name}：{m['text']}")
                group_context = "\n群聊近期消息：\n" + "\n".join(lines)

        system = self.memory.build_context(chat_id=msg.chat_id)
        system += (
            f"\n\n你在群聊中被 {msg.sender_name} @at 了。当前会话 chat_id={msg.chat_id}。请针对对方的问题简洁回复。"
            f"{group_context}"
            "\n如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "需要联网查询时（搜索、天气、新闻等），使用 web_search / web_fetch 工具。"
            "需要计算或处理数据时，使用 run_python 工具。"
            "如果用户明确要求你执行某个任务且以上工具不够，可以用 create_custom_tool 创建工具来完成。"
            "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)
        )

        # 构建消息内容（可能包含图片）
        if has_images:
            content = await self._build_multimodal_content(msg, text or "")
        else:
            content = text

        if self.session_mgr:
            session = self.session_mgr.get_or_create(msg.chat_id)
            session.add_message("user", content, sender_name=msg.sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": content}]

        # 添加 thinking reaction
        thinking_handle = await self.adapter.start_thinking(msg.message_id) or ""

        try:
            reply_text = await self._reply_with_tool_loop(
                system, messages, msg.chat_id, msg.message_id,
            )
        finally:
            if thinking_handle:
                await self.adapter.stop_thinking(msg.message_id, thinking_handle)

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(msg.chat_id)
            session.add_message("assistant", reply_text, sender_name="你")


    async def _evaluate_buffer(self, chat_id: str) -> None:
        """第三层：LLM 判断是否介入群聊"""
        if self._reply_is_busy(chat_id):
            logger.info("跳过评估: 群 %s 正在回复或冷却中", chat_id[-8:])
            return

        buf = self.group_buffers.get(chat_id)
        if not buf:
            return

        recent = buf.get_recent(10)
        if not recent:
            return

        buf.mark_evaluated()

        # ── 协作信号预处理（独立 try-except，不阻塞主判断流程）──
        last_msg_id = ""
        reaction_id = ""
        collab_context = ""
        try:
            # 意图信号检查：如果其他 bot 正在思考，延迟让步
            thinking_bots = self._get_thinking_bots(chat_id)
            if thinking_bots:
                names = "、".join(thinking_bots)
                logger.info("%s 正在思考 %s，延迟评估", names, chat_id[-8:])
                self._record_collab_event(chat_id, "deferred", self.bot_name, f"让步给{names}")
                await asyncio.sleep(random.uniform(3, 5))
                recent = buf.get_recent(10)

            # 添加 thinking reaction 到最近一条非自己的消息
            last_msg_id = ""
            for m in reversed(recent):
                if m.get("sender_id") != self.bot_open_id and m.get("message_id"):
                    last_msg_id = m["message_id"]
                    break
            if last_msg_id:
                reaction_id = await self.adapter.start_thinking(last_msg_id) or ""
                if reaction_id:
                    self._my_reaction_ids[last_msg_id] = reaction_id

            # 随机 jitter 降低碰撞概率
            await asyncio.sleep(random.uniform(0, 1.5))

            # 注入协作记忆（具名 bot 协作历史）
            if self.memory:
                chat_mem = self.memory.read_chat_memory(chat_id)
                if chat_mem and "## 协作模式" in chat_mem:
                    section = chat_mem.split("## 协作模式", 1)[1]
                    next_section = section.find("\n## ")
                    if next_section != -1:
                        section = section[:next_section]
                    section = section.strip()
                    if section:
                        collab_context = f"\n\n近期协作记录：\n{section}\n根据历史模式和各助理的表现决定是否介入。"
        except Exception:
            logger.warning("协作信号预处理失败 chat=%s", chat_id[-8:], exc_info=True)

        soul = self.memory.read_soul()
        # 标注自己的消息，其他人（含 bot）正常记录名字
        my_name = self.bot_name
        lines: list[str] = []
        has_my_reply = False
        for m in recent:
            name = m.get("sender_name", SENDER_UNKNOWN)
            if m.get("sender_id") == self.bot_open_id:
                lines.append(GROUP_MSG_WITH_ID_SELF.format(message_id=m['message_id'], name=name, text=m['text']))
                has_my_reply = True
            else:
                lines.append(GROUP_MSG_WITH_ID_OTHER.format(message_id=m['message_id'], name=name, text=m['text']))
        conversation = "\n".join(lines)

        # 如果最后一条消息就是自己发的，不需要再次介入
        if recent and recent[-1].get("sender_id") == self.bot_open_id:
            logger.info("最后一条消息是自己发的，跳过评估 %s", chat_id[-8:])
            return

        # 如果已经发言过，且之后没有任何新消息，不再介入
        if has_my_reply:
            my_last_idx = max(
                i for i, m in enumerate(recent)
                if m.get("sender_id") == self.bot_open_id
            )
            new_msgs_after = recent[my_last_idx + 1:]
            if not new_msgs_after:
                logger.info("已发言且无新消息，跳过评估 %s", chat_id[-8:])
                return

        prompt = GROUP_EVAL_PROMPT.format(
            bot_name=my_name or SENDER_UNKNOWN, soul=soul,
            conversation=conversation, collab_context=collab_context
        )

        try:
            result = await self.executor.quick_judge(prompt)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0]
            # GLM-5 可能在 JSON 后附带额外文本，用 raw_decode 只取第一个对象
            try:
                judgment = json.loads(result)
            except json.JSONDecodeError:
                judgment, _ = json.JSONDecoder().raw_decode(result.lstrip())

            if judgment.get("should_intervene"):
                logger.info("决定介入群聊 %s: %s", chat_id, judgment.get("reason"))
                await self._intervene(chat_id, recent, judgment, last_msg_id)
            else:
                logger.info("不介入群聊 %s: %s", chat_id[-8:], judgment.get("reason"))
                # 不介入 → 清除 thinking reaction
                if last_msg_id and reaction_id:
                    await self.adapter.stop_thinking(last_msg_id, reaction_id)
                    self._my_reaction_ids.pop(last_msg_id, None)
                # 被动好奇心信号：从群聊对话中提取感兴趣的话题
                reason = judgment.get("reason", "")
                if reason and len(reason) > 10:
                    # 从评估 reason 中检测好奇心线索
                    self._extract_group_curiosity(chat_id, recent, reason)
        except Exception:
            logger.exception("介入判断失败")
            # 异常时也清除 reaction
            if last_msg_id and reaction_id:
                await self.adapter.stop_thinking(last_msg_id, reaction_id)
                self._my_reaction_ids.pop(last_msg_id, None)

    async def _intervene(
        self, chat_id: str, recent: list[dict], judgment: dict,
        thinking_msg_id: str = "",
    ) -> None:
        """执行群聊介入"""
        if self._reply_is_busy(chat_id):
            logger.info("跳过介入: 群 %s 正在回复或冷却中", chat_id[-8:])
            return

        system = self.memory.build_context(chat_id=chat_id)
        # 用真实名字构建对话上下文，标注 bot 消息
        lines: list[str] = []
        for m in recent:
            name = m.get("sender_name", SENDER_UNKNOWN)
            if m.get("sender_id") == self.bot_open_id:
                lines.append(GROUP_MSG_SELF.format(name=name, text=m['text']))
            else:
                lines.append(GROUP_MSG_OTHER.format(name=name, text=m['text']))
        conversation = "\n".join(lines)

        system += GROUP_INTERVENE_SYSTEM_SUFFIX.format(conversation=conversation)
        system += "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)

        # 校验 reply_to_message_id
        reply_to = judgment.get("reply_to_message_id")
        valid_msg_ids = {m["message_id"] for m in recent}
        if not (reply_to and isinstance(reply_to, str) and reply_to.startswith("om_") and reply_to in valid_msg_ids):
            reply_to = None

        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", f"[群聊旁听]\n{conversation}", sender_name="群聊")
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": f"[群聊旁听]\n{conversation}"}]

        reply_text = await self._reply_with_tool_loop(
            system, messages, chat_id, reply_to,
            allow_nudge=False,
        )

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name="你")

        if reply_text:
            self._record_collab_event(
                chat_id, "responded", self.bot_name,
                judgment.get("reason", "")[:50],
            )

        # 清除 thinking reaction（无论是否成功回复）
        if thinking_msg_id:
            rid = self._my_reaction_ids.pop(thinking_msg_id, "")
            if rid:
                await self.adapter.stop_thinking(thinking_msg_id, rid)

    def _record_collab_event(
        self, chat_id: str, event_type: str, actor_name: str, detail: str = "",
    ) -> None:
        """记录协作事件到 chat_memory 的 ## 协作模式 section"""
        if not self.memory:
            return
        try:
            from datetime import datetime, timedelta, timezone
            cst = timezone(timedelta(hours=8))
            now = datetime.now(cst).strftime("%m-%d %H:%M")
            entry = f"- {now} {actor_name} {event_type}"
            if detail:
                entry += f": {detail}"

            path = self.memory.chat_memories_dir / f"{chat_id}.md"
            content = path.read_text(encoding="utf-8") if path.exists() else ""

            section_header = "## 协作模式"
            if section_header in content:
                # 提取现有 section 内容
                parts = content.split(section_header, 1)
                before = parts[0]
                after = parts[1]
                # 找到下一个 ## 或文件结尾
                next_section = after.find("\n## ")
                if next_section != -1:
                    section_body = after[:next_section]
                    rest = after[next_section:]
                else:
                    section_body = after
                    rest = ""
                # 解析已有条目，保留最近 19 条 + 新条目 = 20 条
                lines = [l for l in section_body.strip().split("\n") if l.startswith("- ")]
                lines = lines[-19:]  # 保留最近 19 条
                lines.append(entry)
                content = before + section_header + "\n" + "\n".join(lines) + "\n" + rest
            else:
                content = content.rstrip() + f"\n\n{section_header}\n{entry}\n"

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.debug("协作事件: %s", entry)
        except Exception:
            logger.warning("记录协作事件失败", exc_info=True)



    async def _handle_member_change(self, data: dict) -> None:
        """处理群成员变更事件"""
        chat_id = data.get("chat_id", "")
        change_type = data.get("change_type", "")
        if not chat_id:
            return

        if change_type == "bot_joined":
            # Bot 被加入群聊 → 自我介绍
            try:
                context = self.memory.build_context(chat_id=chat_id)
                system = BOT_SELF_INTRO_SYSTEM.format(soul=context)
                intro = await self.executor.reply(system, BOT_SELF_INTRO_USER)
                intro = intro.strip()
                if intro:
                    await self.adapter.send(OutgoingMessage(chat_id, intro))
                    logger.info("Bot 入群自我介绍已发送: %s -> %s", chat_id[-8:], intro[:50])
            except Exception:
                logger.exception("Bot 入群自我介绍失败: %s", chat_id[-8:])

        elif change_type == "user_joined":
            # 新用户入群 → 欢迎
            users = data.get("users", [])
            if not users:
                return
            names = [u.get("name") or u.get("user_id", "")[-6:] for u in users]
            if not names:
                return
            try:
                user_names = "、".join(names)
                context = self.memory.build_context(chat_id=chat_id)
                system = USER_WELCOME_SYSTEM.format(soul=context, user_names=user_names)
                welcome = await self.executor.reply(system, USER_WELCOME_USER)
                welcome = welcome.strip()
                if welcome:
                    await self.adapter.send(OutgoingMessage(chat_id, welcome))
                    logger.info("用户入群欢迎已发送: %s -> %s", chat_id[-8:], welcome[:50])
            except Exception:
                logger.exception("用户入群欢迎失败: %s", chat_id[-8:])

        elif change_type == "bot_left":
            # Bot 被移出群聊 → 清理内部状态
            self._thinking_signals.pop(chat_id, None)
            self.group_buffers.pop(chat_id, None)
            logger.info("Bot 已退出群 %s，清理完成", chat_id[-8:])

    async def _compact_session(self, session: Any) -> None:
        """压缩会话：提取长期记忆到 chat_memory，生成结构化摘要后裁剪消息"""
        # 仅对将被压缩的旧消息做记忆提取
        old_messages = session.get_compaction_context()
        if old_messages:
            flush_prompt = self.memory.flush_before_compaction(old_messages)
            extracted = await self.executor.reply("", flush_prompt)
            if extracted.strip() and extracted.strip() != FLUSH_NO_RESULT:
                self.memory.append_daily(
                    COMPACTION_DAILY_HEADER.format(extracted=extracted),
                    chat_id=session.chat_id,
                )
                # 同时写入 per-chat 长期记忆，避免 daily log 过期后丢失
                from datetime import date as _date
                self.memory.append_chat_memory(
                    session.chat_id,
                    COMPACTION_MEMORY_HEADER.format(date=_date.today().isoformat(), extracted=extracted),
                )

        # 生成结构化摘要
        summary_prompt = COMPACTION_SUMMARY_PROMPT.format(
            old_messages_formatted="\n".join(
                f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
                for m in old_messages
            )
        )
        summary = await self.executor.reply("", summary_prompt)
        session.compact(summary)

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


    async def _build_multimodal_content(
        self, msg: IncomingMessage, text: str,
    ) -> str | list[dict]:
        """构建多模态内容：如果消息含图片则返回 content blocks 列表，否则返回纯文本。

        返回格式兼容 Anthropic Messages API：
        - 纯文本: "hello"
        - 多模态: [{"type": "image", "source": {...}}, {"type": "text", "text": "hello"}]

        图片下载失败时会在文本中附带提示，让 LLM 知道有图片未能加载。
        """
        if not msg.image_keys:
            return text

        blocks: list[dict] = []
        failed_count = 0

        for key in msg.image_keys:
            result = await self.adapter.fetch_media(msg.message_id, key)
            if result:
                b64_data, media_type = result
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": b64_data,
                    },
                })
            else:
                failed_count += 1

        # 构建文本部分，附带下载失败提示
        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            # 无图片也无文本
            return ""
        elif not any(b["type"] == "text" for b in blocks):
            # 有图片但没文本，加个默认提示
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks

    async def _build_image_content(
        self, image_messages: list[IncomingMessage], text: str,
    ) -> str | list[dict]:
        """从多条图片消息中下载图片，与文本合并为 content blocks。

        用于防抖合并场景：多条消息（可能混合文本和图片）合并后统一处理。
        图片下载失败时会在文本中附带提示。
        """
        blocks: list[dict] = []
        failed_count = 0
        for msg in image_messages:
            for key in msg.image_keys:
                result = await self.adapter.fetch_media(msg.message_id, key)
                if result:
                    b64_data, media_type = result
                    blocks.append({
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": b64_data,
                        },
                    })
                else:
                    failed_count += 1

        text_parts = []
        if text:
            text_parts.append(text)
        if failed_count:
            text_parts.append(f"（有 {failed_count} 张图片加载失败，无法查看）")

        text_combined = "\n".join(text_parts) if text_parts else ""

        if text_combined:
            blocks.append({"type": "text", "text": text_combined})
        elif not blocks:
            return ""
        elif not any(b.get("type") == "text" for b in blocks):
            blocks.append({"type": "text", "text": "（用户发送了图片）"})

        return blocks

