"""消息路由 + 介入判断 + 工具调用"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from typing import Any

from lq.buffer import MessageBuffer, rule_check
from lq.executor.api import DirectAPIExecutor
from lq.feishu.sender import FeishuSender
from lq.memory import MemoryManager
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
    DAILY_LOG_PRIVATE, DAILY_LOG_STATUS_OK, DAILY_LOG_STATUS_FAIL,
    GROUP_MSG_SELF, GROUP_MSG_OTHER,
    GROUP_MSG_WITH_ID_SELF, GROUP_MSG_WITH_ID_OTHER,
    GROUP_CONTEXT_HEADER,
    COMPACTION_DAILY_HEADER, COMPACTION_MEMORY_HEADER, COMPACTION_SUMMARY_PROMPT,
    FLUSH_NO_RESULT,
    SENDER_SELF, SENDER_UNKNOWN, SENDER_GROUP,
    BOT_POLL_AT_REASON,
    TOOL_DESC_WRITE_MEMORY, TOOL_DESC_WRITE_CHAT_MEMORY,
    TOOL_DESC_CALENDAR_CREATE, TOOL_DESC_CALENDAR_LIST,
    TOOL_DESC_SEND_CARD, TOOL_DESC_READ_SELF_FILE, TOOL_DESC_WRITE_SELF_FILE,
    TOOL_DESC_CREATE_CUSTOM_TOOL, TOOL_DESC_LIST_CUSTOM_TOOLS,
    TOOL_DESC_TEST_CUSTOM_TOOL, TOOL_DESC_DELETE_CUSTOM_TOOL,
    TOOL_DESC_TOGGLE_CUSTOM_TOOL, TOOL_DESC_SEND_MESSAGE,
    TOOL_DESC_SCHEDULE_MESSAGE, TOOL_DESC_RUN_CLAUDE_CODE, TOOL_DESC_RUN_BASH,
    TOOL_FIELD_SECTION, TOOL_FIELD_CONTENT_MEMORY,
    TOOL_FIELD_CHAT_SECTION, TOOL_FIELD_CHAT_CONTENT,
    TOOL_FIELD_SUMMARY, TOOL_FIELD_START_TIME, TOOL_FIELD_END_TIME,
    TOOL_FIELD_EVENT_DESC, TOOL_FIELD_QUERY_START, TOOL_FIELD_QUERY_END,
    TOOL_FIELD_CARD_TITLE, TOOL_FIELD_CARD_CONTENT, TOOL_FIELD_CARD_COLOR,
    TOOL_FIELD_FILENAME_READ, TOOL_FIELD_FILENAME_WRITE, TOOL_FIELD_FILE_CONTENT,
    TOOL_FIELD_TOOL_NAME, TOOL_FIELD_TOOL_CODE,
    TOOL_FIELD_VALIDATE_CODE, TOOL_FIELD_DELETE_NAME,
    TOOL_FIELD_TOGGLE_NAME, TOOL_FIELD_TOGGLE_ENABLED,
    TOOL_FIELD_CHAT_ID, TOOL_FIELD_TEXT, TOOL_FIELD_SEND_AT,
    TOOL_FIELD_CC_PROMPT, TOOL_FIELD_WORKING_DIR, TOOL_FIELD_CC_TIMEOUT,
    TOOL_FIELD_BASH_COMMAND, TOOL_FIELD_BASH_TIMEOUT,
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
                    "description": TOOL_FIELD_TEXT,
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
]


class MessageRouter:
    def __init__(
        self,
        executor: DirectAPIExecutor,
        memory: MemoryManager,
        sender: FeishuSender,
        bot_open_id: str,
        bot_name: str = "",
    ) -> None:
        self.executor = executor
        self.memory = memory
        self.sender = sender
        self.bot_open_id = bot_open_id
        self.bot_name = bot_name

        # 群聊缓冲区
        self.group_buffers: dict[str, MessageBuffer] = {}
        # 私聊防抖：chat_id → {texts, message_id, timer, event}
        self._private_pending: dict[str, dict] = {}
        self._private_debounce_seconds: float = 1.5
        # 群聊 bot 消息轮询计数（防止 bot 间无限对话，用户消息重置）
        self._bot_poll_count: dict[str, int] = {}
        self._bot_poll_timers: dict[str, asyncio.TimerHandle] = {}
        self._bot_seen_ids: dict[str, set[str]] = {}  # chat_id → 已处理的 message_id
        # 回复去重：chat_id → 上次发送的文本，防止 bot 轮询循环导致重复回复
        self._last_reply_per_chat: dict[str, str] = {}
        # 活跃群聊跟踪：chat_id → 最近一次消息的 time.time()
        self._active_groups: dict[str, float] = {}
        # 主动轮询已知消息 ID（去重用）
        self._polled_msg_ids: dict[str, set[str]] = {}
        # Reaction 意图信号：chat_id → {bot_open_id: timestamp}
        self._thinking_signals: dict[str, dict[str, float]] = {}
        # 本实例添加的 reaction_id 用于清理：message_id → reaction_id
        self._my_reaction_ids: dict[str, str] = {}
        # 意图信号使用的 emoji 类型
        self._thinking_emoji: str = "OnIt"
        # 注入依赖
        self.session_mgr: Any = None
        self.calendar: Any = None
        self.stats: Any = None
        self.cc_executor: Any = None
        self.bash_executor: Any = None
        self.tool_registry: Any = None
        self.post_processor: Any = None

    async def handle(self, data: dict) -> None:
        """处理消息事件"""
        event_type = data.get("event_type")

        if event_type == "im.message.receive_v1":
            await self._dispatch_message(data["event"])
        elif event_type == "card.action.trigger":
            await self._handle_card_action(data["event"])
        elif event_type == "eval_timeout":
            chat_id = data.get("chat_id")
            if chat_id:
                await self._evaluate_buffer(chat_id)
        elif event_type == "reaction.created":
            self._handle_reaction_event(data)
        else:
            logger.debug("忽略事件类型: %s", event_type)

    # ── 活跃群聊跟踪（供 gateway 主动轮询使用）──

    def register_active_group(self, chat_id: str) -> None:
        """标记群聊为活跃（收到消息时调用）"""
        self._active_groups[chat_id] = time.time()

    def get_active_groups(self, ttl: float = 600.0) -> list[str]:
        """返回近 10 分钟内有消息的群聊 ID 列表"""
        now = time.time()
        expired = [cid for cid, ts in self._active_groups.items() if now - ts > ttl]
        for cid in expired:
            self._active_groups.pop(cid, None)
            self._polled_msg_ids.pop(cid, None)
            self._thinking_signals.pop(cid, None)
        # 清理过期的 reaction ID 缓存（超过 5 分钟的条目）
        stale_rids = [mid for mid, _ in self._my_reaction_ids.items()
                      if not any(mid in {m.get("message_id", "") for m in buf.get_recent(20)}
                                 for buf in self.group_buffers.values())]
        for mid in stale_rids:
            self._my_reaction_ids.pop(mid, None)
        return list(self._active_groups)

    async def inject_polled_message(self, chat_id: str, msg: dict) -> None:
        """将主动轮询发现的 bot 消息注入群聊缓冲区（去重）"""
        msg_id = msg.get("message_id", "")
        if not msg_id:
            return

        known = self._polled_msg_ids.setdefault(chat_id, set())
        if msg_id in known:
            return

        # 也检查 buffer 中是否已有此消息
        buf = self.group_buffers.get(chat_id)
        if buf:
            buf_ids = {m.get("message_id", "") for m in buf.get_recent(20)}
            if msg_id in buf_ids:
                known.add(msg_id)
                return

        known.add(msg_id)

        # 注入 buffer
        if chat_id not in self.group_buffers:
            self.group_buffers[chat_id] = MessageBuffer()
        buf = self.group_buffers[chat_id]
        buf.add(msg)

        sender_name = msg.get("sender_name", msg.get("sender_id", "")[-6:])
        logger.info(
            "主动轮询注入 bot 消息 [%s] %s: %s",
            chat_id[-8:], sender_name, msg.get("text", "")[:50],
        )

    # ── Reaction 意图信号处理 ──

    def _handle_reaction_event(self, data: dict) -> None:
        """处理 reaction WS 事件，更新 thinking_signals"""
        emoji = data.get("emoji_type", "")
        if emoji != self._thinking_emoji:
            return  # 不是约定的意图信号 emoji
        operator_id = data.get("operator_id", "")
        if not operator_id or operator_id == self.bot_open_id:
            return  # 忽略自己的 reaction
        message_id = data.get("message_id", "")

        # 找到此消息所属的群聊
        chat_id = ""
        for cid, buf in self.group_buffers.items():
            for m in buf.get_recent(20):
                if m.get("message_id") == message_id:
                    chat_id = cid
                    break
            if chat_id:
                break

        if not chat_id:
            # 消息不在已知 buffer 中，丢弃（无法关联到群聊则无意义）
            logger.debug("收到 reaction 但无法关联群聊，丢弃: msg=%s", message_id[-8:])
            return

        signals = self._thinking_signals.setdefault(chat_id, {})
        signals[operator_id] = time.time()
        bot_name = self.sender._user_name_cache.get(operator_id, operator_id[-6:])
        logger.info("收到意图信号: %s 正在思考 [%s]", bot_name, chat_id[-8:])

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
                name = self.sender._user_name_cache.get(bot_id, bot_id[-6:])
                active.append(name)
        for bot_id in expired:
            signals.pop(bot_id, None)
        return active

    async def _dispatch_message(self, event: Any) -> None:
        """根据消息类型分发"""
        message = event.message
        chat_type = message.chat_type
        sender_id = event.sender.sender_id.open_id

        # 忽略自己发的消息
        if sender_id == self.bot_open_id:
            return

        if chat_type == "p2p":
            await self._handle_private(event)
        elif chat_type == "group":
            if self.sender.is_chat_left(message.chat_id):
                logger.debug("忽略已退出群 %s 的消息", message.chat_id[-8:])
                return
            self._bot_poll_count.pop(message.chat_id, None)
            self._bot_seen_ids.pop(message.chat_id, None)
            self._last_reply_per_chat.pop(message.chat_id, None)
            await self._handle_group(event)

    async def _handle_private(self, event: Any) -> None:
        """处理私聊消息（带防抖：短时间连发多条会合并后统一处理）"""
        message = event.message
        text = self._extract_text(message)
        if not text:
            if self._is_non_text_message(message):
                await self.sender.reply_text(
                    message.message_id,
                    NON_TEXT_REPLY_PRIVATE,
                )
            return

        chat_id = message.chat_id
        sender_name = await self.sender.get_user_name(event.sender.sender_id.open_id, chat_id=chat_id)
        logger.info("收到私聊 [%s]: %s", sender_name, text[:80])

        # 防抖：收集连续消息，延迟后统一处理
        pending = self._private_pending.get(chat_id)
        if pending:
            # 已有待处理消息，追加文本（保留首条 message_id 用于回复线程）
            pending["texts"].append(text)
            # message_id 保持首条不变，确保回复线程指向正确的消息
            # 重置定时器
            if pending.get("timer"):
                pending["timer"].cancel()
            loop = asyncio.get_running_loop()
            pending["timer"] = loop.call_later(
                self._private_debounce_seconds,
                lambda cid=chat_id: asyncio.ensure_future(self._flush_private(cid)),
            )
        else:
            # 首条消息，启动防抖定时器
            self._private_pending[chat_id] = {
                "texts": [text],
                "message_id": message.message_id,
                "sender_name": sender_name,
                "timer": None,
            }
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
        combined_text = "\n".join(pending["texts"])
        message_id = pending["message_id"]
        sender_name = pending["sender_name"]

        system = self.memory.build_context(chat_id=chat_id)
        system += PRIVATE_SYSTEM_SUFFIX.format(chat_id=chat_id)
        system += "\n\n" + wrap_tag(TAG_MEMORY_GUIDANCE, MEMORY_GUIDANCE_PRIVATE)
        system += "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE)

        # 使用会话管理器维护上下文
        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", combined_text, sender_name=sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": combined_text}]

        # 尝试带工具回复
        try:
            reply_text = await self._reply_with_tool_loop(
                system, messages, chat_id, message_id
            )
        except Exception:
            logger.exception("私聊回复失败 (chat=%s)", chat_id)
            reply_text = ""

        if self.session_mgr and reply_text:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name=SENDER_SELF)
            if session.should_compact():
                await self._compact_session(session)

        # 记录日志
        self.memory.append_daily(DAILY_LOG_PRIVATE.format(sender_name=sender_name, text_preview=combined_text[:50], status=DAILY_LOG_STATUS_OK if reply_text else DAILY_LOG_STATUS_FAIL), chat_id=chat_id)

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
        """
        all_tools = self._build_all_tools()
        resp = await self.executor.reply_with_tools(system, messages, all_tools)

        # 复杂任务（如 Claude Code 执行）可能需要更多轮次
        max_iterations = 20
        iteration = 0
        nudge_count = 0
        tools_called: list[str] = []

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

        # 发送最终文本回复
        if resp.text:
            final = text_transform(resp.text) if text_transform else resp.text
            logger.info("回复: %s", final[:80])
            await self._send_reply(final, chat_id, reply_to_message_id)
            resp.text = final

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

        return resp.text

    # 清理 LLM 模仿的元数据格式
    _CLEAN_RE = __import__("re").compile(
        r"\[\d{1,2}:\d{2}(?:\s+[^\]]+)?\]\s*"  # [17:32] 或 [17:32 你]
        r"|<msg\s[^>]*>"                          # <msg time=17:32 from=你>
        r"|</msg>",                                # </msg>
    )

    async def _send_reply(self, text: str, chat_id: str, reply_to_message_id: str | None) -> None:
        """发送回复：优先 reply_text，fallback 到 send_text"""
        text = self._CLEAN_RE.sub("", text).strip()
        if not text:
            return
        # 去重：跳过与上次完全相同的回复（防止 bot 轮询循环导致复读）
        if chat_id and self._last_reply_per_chat.get(chat_id) == text:
            logger.info("跳过重复回复 chat=%s text=%s", chat_id[-8:], text[:60])
            return
        if chat_id:
            self._last_reply_per_chat[chat_id] = text
        if reply_to_message_id and not reply_to_message_id.startswith("inbox_"):
            await self.sender.reply_text(reply_to_message_id, text)
        elif chat_id and chat_id != "local_cli":
            await self.sender.send_text(chat_id, text)
        else:
            logger.info("本地回复（未发送飞书）: %s", text[:200])

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
                from lq.feishu.cards import build_info_card
                card = build_info_card(
                    input_data["title"],
                    input_data["content"],
                    color=input_data.get("color", "blue"),
                )
                await self.sender.send_card(chat_id, card)
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
                # 自动将 @名字 转换为飞书 <at> 标签
                text_to_send = input_data["text"]
                cache_map = {n: oid for oid, n in self.sender._user_name_cache.items() if n and oid.startswith("ou_")}
                if cache_map:
                    text_to_send = self._replace_at_mentions(text_to_send, cache_map)
                msg_id = await self.sender.send_text(
                    target,
                    text_to_send,
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
                target_text = input_data["text"]
                sender_ref = self.sender

                async def _delayed_send():
                    await asyncio.sleep(delay)
                    msg_id = await sender_ref.send_text(target_chat_id, target_text)
                    if msg_id:
                        logger.info("定时消息已发送: chat=%s", target_chat_id)
                    else:
                        logger.error("定时消息发送失败: chat=%s", target_chat_id)

                asyncio.ensure_future(_delayed_send())
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

            else:
                # 尝试自定义工具注册表
                if self.tool_registry and self.tool_registry.has_tool(name):
                    import httpx
                    async with httpx.AsyncClient() as http_client:
                        context = {
                            "sender": self.sender,
                            "memory": self.memory,
                            "calendar": self.calendar,
                            "http": http_client,
                        }
                        return await self.tool_registry.execute(name, input_data, context)
                return {"success": False, "error": ERR_UNKNOWN_TOOL.format(name=name)}

        except Exception as e:
            logger.exception("工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    async def _handle_group(self, event: Any) -> None:
        """处理群聊消息"""
        message = event.message
        chat_id = message.chat_id
        self.register_active_group(chat_id)

        # 第一层：检查是否被 @at
        mentions = getattr(message, "mentions", None)
        is_at_me = False
        if mentions:
            for m in mentions:
                if hasattr(m, "id") and hasattr(m.id, "open_id") and m.id.open_id == self.bot_open_id:
                    is_at_me = True
                    break

        # 文本层兜底：飞书有时不解析 @，以纯文本 "@bot名" 发出
        if not is_at_me and self.bot_name:
            raw_text = self._extract_text(message)
            if raw_text and f"@{self.bot_name}" in raw_text:
                is_at_me = True

        if is_at_me:
            await self._handle_group_at(event)
            return

        # 第一层规则：极短无实质消息直接忽略
        text = self._extract_text(message)
        # 解析 @占位符为真名
        text = self._resolve_at_mentions(text, mentions)
        if not text:
            logger.debug("群聊旁听: 非文本消息，跳过")
            return
        if rule_check(text) == "IGNORE":
            logger.debug("群聊旁听: 无实质消息，跳过: %s", text[:20])
            return

        sender_id = event.sender.sender_id.open_id
        sender_name = await self.sender.get_user_name(sender_id, chat_id=chat_id)
        if self.sender.is_chat_left(chat_id):
            logger.info("Bot 已退出群 %s，忽略旁听消息", chat_id[-8:])
            return
        logger.info("群聊旁听 [%s] %s: %s", chat_id[-8:], sender_name, text[:50])

        # 第二层：缓冲区
        if chat_id not in self.group_buffers:
            self.group_buffers[chat_id] = MessageBuffer()

        buf = self.group_buffers[chat_id]
        buf.add({
            "text": text,
            "sender_id": sender_id,
            "sender_name": sender_name,
            "message_id": message.message_id,
            "chat_id": chat_id,
        })

        if buf.should_evaluate():
            logger.info("群聊缓冲区已满 (%d条)，触发评估", buf._new_count)
            await self._evaluate_buffer(chat_id)
        else:
            # 消息数未达阈值，设置超时定时器确保安静群聊也能触发评估
            logger.info("群聊缓冲 %d/%d，%ds 后超时评估", buf._new_count, buf.eval_threshold, int(buf.max_age_seconds))
            loop = asyncio.get_running_loop()
            buf.schedule_timeout(loop, lambda cid=chat_id: asyncio.ensure_future(self._evaluate_buffer(cid)))

    async def _handle_group_at(self, event: Any) -> None:
        """处理群聊 @at 消息 — 必须回复"""
        message = event.message
        self.register_active_group(message.chat_id)
        text = self._extract_text(message)
        if not text:
            if self._is_non_text_message(message):
                await self.sender.reply_text(
                    message.message_id,
                    NON_TEXT_REPLY_GROUP,
                )
            return

        sender_id = event.sender.sender_id.open_id
        text = self._resolve_at_mentions(text, getattr(message, "mentions", None)).strip()
        if not text:
            # 空 @：从缓冲区取该用户最近的消息作为上下文
            buf = self.group_buffers.get(message.chat_id)
            if buf:
                recent = buf.get_recent(5)
                sender_msgs = [m["text"] for m in recent if m["sender_id"] == sender_id]
                if sender_msgs:
                    text = sender_msgs[-1]
                    logger.info("群聊 @at 空消息，取缓冲区上文: %s", text[:50])
            if not text:
                text = EMPTY_AT_FALLBACK

        sender_name = await self.sender.get_user_name(sender_id, chat_id=message.chat_id)
        if self.sender.is_chat_left(message.chat_id):
            logger.info("Bot 已退出群 %s，忽略 @at 消息", message.chat_id[-8:])
            return
        logger.info("群聊 @at [%s]: %s", sender_name, text[:50])

        # 构建群聊上下文（含 API 补充的机器人消息）
        group_context = ""
        buf = self.group_buffers.get(message.chat_id)
        if buf:
            recent = buf.get_recent(10)
            if recent:
                recent = await self._enrich_with_api_messages(message.chat_id, recent)
                lines = []
                for m in recent:
                    name = m.get("sender_name", SENDER_UNKNOWN)
                    if m.get("sender_id") == self.bot_open_id:
                        lines.append(GROUP_MSG_SELF.format(name=name, text=m['text']))
                    else:
                        lines.append(GROUP_MSG_OTHER.format(name=name, text=m['text']))
                group_context = GROUP_CONTEXT_HEADER.format(messages="\n".join(lines))

        system = self.memory.build_context(chat_id=message.chat_id, sender=self.sender)
        system += GROUP_AT_SYSTEM_SUFFIX.format(
            sender_name=sender_name, chat_id=message.chat_id, group_context=group_context
        )
        system += "\n\n" + wrap_tag(TAG_MEMORY_GUIDANCE, MEMORY_GUIDANCE_GROUP)
        system += "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)

        # 构建 name_to_id 映射，用于 @名字 → <at> 标签转换
        name_to_id: dict[str, str] = {}
        name_to_id[sender_name] = sender_id
        buf = self.group_buffers.get(message.chat_id)
        if buf:
            for m in buf.get_recent(15):
                n = m.get("sender_name", "")
                if n:
                    name_to_id[n] = m["sender_id"]
        # 补充群成员缓存中的 bot 名字
        for oid, n in self.sender._user_name_cache.items():
            if oid.startswith("ou_") and n:
                name_to_id[n] = oid
        transform = lambda t: self._replace_at_mentions(t, name_to_id)

        if self.session_mgr:
            session = self.session_mgr.get_or_create(message.chat_id)
            session.add_message("user", text, sender_name=sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": text}]

        reply_text = await self._reply_with_tool_loop(
            system, messages, message.chat_id, message.message_id,
            text_transform=transform,
        )

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(message.chat_id)
            session.add_message("assistant", reply_text, sender_name=SENDER_SELF)
            if session.should_compact():
                await self._compact_session(session)

        if reply_text:
            self._schedule_bot_poll(message.chat_id)

    async def _enrich_with_api_messages(self, chat_id: str, buffer_msgs: list[dict]) -> list[dict]:
        """用 API 拉取的消息补充缓冲区，填入事件机制收不到的机器人消息。"""
        try:
            api_msgs = await self.sender.fetch_chat_messages(chat_id, 15)
        except Exception:
            return buffer_msgs
        if not api_msgs:
            return buffer_msgs

        known_ids = {m["message_id"] for m in buffer_msgs}
        merged = list(buffer_msgs)

        for am in api_msgs:
            if am["message_id"] in known_ids:
                continue
            # 解析发送者名字：app 类型先查缓存，不走通讯录 API（会 400）
            sid = am.get("sender_id", "")
            if not sid:
                sender_name = SENDER_UNKNOWN
            elif am.get("sender_type") == "app":
                sender_name = self.sender._user_name_cache.get(sid, sid[-6:])
            else:
                sender_name = await self.sender.get_user_name(sid, chat_id=chat_id)
            merged.append({
                "text": am["text"],
                "sender_id": am["sender_id"],
                "sender_name": sender_name,
                "sender_type": am.get("sender_type", "user"),
                "message_id": am["message_id"],
                "chat_id": chat_id,
            })
            known_ids.add(am["message_id"])

        # 按 message_id 排序（飞书 message_id 含时间序，字典序即时间序）
        merged.sort(key=lambda m: m["message_id"])
        return merged[-15:]  # 最多保留 15 条

    async def _evaluate_buffer(self, chat_id: str) -> None:
        """第三层：LLM 判断是否介入群聊"""
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

            # 添加自己的 "thinking" reaction 到最新消息
            last_msg_id = recent[-1].get("message_id", "") if recent else ""
            if last_msg_id:
                reaction_id = await self.sender.add_reaction(last_msg_id, self._thinking_emoji) or ""
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

        # 从 API 拉取近期消息，补充事件机制收不到的机器人消息
        recent = await self._enrich_with_api_messages(chat_id, recent)

        soul = self.memory.read_soul()
        # 标注自己的消息，其他人（含 bot）正常记录名字
        my_name = self.bot_name or self.sender._user_name_cache.get(self.bot_open_id, "")
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
            logger.debug("最后一条消息是自己发的，跳过评估")
            return

        # 如果已经发言过，且之后没有任何新消息，不再介入
        if has_my_reply:
            my_last_idx = max(
                i for i, m in enumerate(recent)
                if m.get("sender_id") == self.bot_open_id
            )
            new_msgs_after = recent[my_last_idx + 1:]
            if not new_msgs_after:
                logger.debug("已发言且无新消息，跳过评估")
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
            judgment = json.loads(result)

            if judgment.get("should_intervene"):
                logger.info("决定介入群聊 %s: %s", chat_id, judgment.get("reason"))
                await self._intervene(chat_id, recent, judgment, last_msg_id)
            else:
                logger.debug("不介入群聊 %s: %s", chat_id, judgment.get("reason"))
                # 不介入 → 清除 thinking reaction
                if last_msg_id and reaction_id:
                    await self.sender.remove_reaction(last_msg_id, reaction_id)
                    self._my_reaction_ids.pop(last_msg_id, None)
        except Exception:
            logger.exception("介入判断失败")
            # 异常时也清除 reaction
            if last_msg_id and reaction_id:
                await self.sender.remove_reaction(last_msg_id, reaction_id)
                self._my_reaction_ids.pop(last_msg_id, None)

    async def _intervene(
        self, chat_id: str, recent: list[dict], judgment: dict,
        thinking_msg_id: str = "",
    ) -> None:
        """执行群聊介入"""
        system = self.memory.build_context(chat_id=chat_id, sender=self.sender)
        # 用真实名字构建对话上下文，标注 bot 消息
        name_to_id: dict[str, str] = {}
        lines: list[str] = []
        for m in recent:
            name = m.get("sender_name", SENDER_UNKNOWN)
            name_to_id[name] = m["sender_id"]
            if m.get("sender_id") == self.bot_open_id:
                lines.append(GROUP_MSG_SELF.format(name=name, text=m['text']))
            else:
                lines.append(GROUP_MSG_OTHER.format(name=name, text=m['text']))
        conversation = "\n".join(lines)

        system += GROUP_INTERVENE_SYSTEM_SUFFIX.format(reason=judgment.get('reason', ''), conversation=conversation)
        system += "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)

        # 校验 reply_to_message_id
        reply_to = judgment.get("reply_to_message_id")
        valid_msg_ids = {m["message_id"] for m in recent}
        if not (reply_to and isinstance(reply_to, str) and reply_to.startswith("om_") and reply_to in valid_msg_ids):
            reply_to = None

        # 走工具循环，支持创建工具 + 联网查询
        # text_transform 在发送前将 @名字 替换为飞书 <at> 标记
        transform = lambda t: self._replace_at_mentions(t, name_to_id)

        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", wrap_tag(TAG_GROUP_CONTEXT, conversation), sender_name=SENDER_GROUP)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": wrap_tag(TAG_GROUP_CONTEXT, conversation)}]

        reply_text = await self._reply_with_tool_loop(
            system, messages, chat_id, reply_to,
            text_transform=transform, allow_nudge=False,
        )

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name=SENDER_SELF)
            if session.should_compact():
                await self._compact_session(session)

        if reply_text:
            self._schedule_bot_poll(chat_id)
            self._record_collab_event(
                chat_id, "responded", self.bot_name,
                judgment.get("reason", "")[:50],
            )

            # ── 碰撞检测：仅在群内有其他 bot 时执行 ──
            if self.sender.get_bot_members(chat_id):
                await asyncio.sleep(2)
                try:
                    api_msgs = await self.sender.fetch_chat_messages(chat_id, 5)
                    known_ids = {m["message_id"] for m in recent}
                    other_bot_replies = [
                        m for m in api_msgs
                        if m.get("sender_type") == "app"
                        and m.get("sender_id") != self.bot_open_id
                        and m["message_id"] not in known_ids
                    ]
                    if other_bot_replies:
                        other_id = other_bot_replies[0]["sender_id"]
                        other_name = self.sender._user_name_cache.get(other_id, other_id[-6:])
                        logger.warning("回复碰撞！%s 也在 %s 中回复了", other_name, chat_id[-8:])
                        self._record_collab_event(chat_id, "collision", self.bot_name, f"与{other_name}碰撞")
                        await self._social_repair(chat_id, other_name)
                except Exception:
                    logger.warning("碰撞检测失败", exc_info=True)

        # 清除 thinking reaction（无论是否成功回复）
        if thinking_msg_id:
            rid = self._my_reaction_ids.pop(thinking_msg_id, "")
            if rid:
                await self.sender.remove_reaction(thinking_msg_id, rid)

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

    async def _social_repair(self, chat_id: str, other_name: str) -> None:
        """碰撞修复：生成简短自然的化解语"""
        repair_prompt = (
            f"你和{other_name}不小心同时回复了群聊里的同一个话题（撞车了）。"
            "请用一句简短（<15字）、自然、轻松的话化解这个小尴尬。"
            "不需要道歉，可以调侃。只输出这句话本身，不要加引号。"
        )
        try:
            text = await self.executor.quick_judge(repair_prompt)
            text = text.strip().strip('"').strip("'")
            # 基础内容验证：拒绝 JSON/XML 残片和多行输出
            if text and len(text) < 50 and "\n" not in text and "{" not in text and "<" not in text:
                await self.sender.send_text(chat_id, text)
        except Exception:
            logger.exception("social repair failed")

    @staticmethod
    def _replace_at_mentions(text: str, name_to_id: dict[str, str]) -> str:
        """将 @名字 替换为飞书 <at> 标记，按名字长度降序匹配避免子串冲突"""
        for name in sorted(name_to_id, key=len, reverse=True):
            tag = f'<at user_id="{name_to_id[name]}">{name}</at>'
            text = text.replace(f"@{name}", tag)
        return text

    def _schedule_bot_poll(self, chat_id: str, delay: float = 5.0) -> None:
        """群聊回复后，延迟拉取其他 bot 的消息（WS 收不到 bot 消息）。
        带防抖：同一群聊连续回复只保留最后一次定时器。
        """
        old = self._bot_poll_timers.pop(chat_id, None)
        if old:
            old.cancel()
        try:
            loop = asyncio.get_running_loop()
            handle = loop.call_later(
                delay,
                lambda cid=chat_id: asyncio.ensure_future(self._poll_bot_messages(cid)),
            )
            self._bot_poll_timers[chat_id] = handle
            logger.debug("已安排 bot 消息轮询: 群 %s, %ds 后", chat_id[-8:], delay)
        except Exception:
            logger.exception("安排 bot 轮询失败")

    async def _poll_bot_messages(self, chat_id: str) -> None:
        """延迟拉取群聊 API 消息，补充 WS 收不到的 bot 消息并触发评估。"""
        self._bot_poll_timers.pop(chat_id, None)

        count = self._bot_poll_count.get(chat_id, 0)
        if count >= 5:
            logger.debug("群 %s bot 轮询已达上限，跳过", chat_id[-8:])
            return

        try:
            api_msgs = await self.sender.fetch_chat_messages(chat_id, 10)
        except Exception:
            return
        if not api_msgs:
            return

        buf = self.group_buffers.get(chat_id)
        if not buf:
            buf = MessageBuffer()
            self.group_buffers[chat_id] = buf

        # 双重去重：缓冲区已有 + 之前轮询已见
        known_ids = {m["message_id"] for m in buf.get_recent(20)}
        seen = self._bot_seen_ids.setdefault(chat_id, set())
        known_ids |= seen

        new_count = 0
        newly_added: set[str] = set()  # 本轮新增的 message_id
        for am in api_msgs:
            mid = am["message_id"]
            if mid in known_ids:
                continue
            if am.get("sender_type") != "app":
                continue
            # 跳过自己发的消息（只关心其他 bot 的消息）
            if am.get("sender_id") == self.bot_open_id:
                seen.add(mid)  # 标记已见，避免下次重复检查
                continue
            sender_name = self.sender._user_name_cache.get(
                am["sender_id"], am["sender_id"][-6:]
            )
            buf.add({
                "text": am["text"],
                "sender_id": am["sender_id"],
                "sender_name": sender_name,
                "sender_type": "app",
                "message_id": mid,
                "chat_id": chat_id,
            })
            seen.add(mid)
            newly_added.add(mid)
            new_count += 1

        if new_count:
            self._bot_poll_count[chat_id] = count + 1
            # 仅检查本轮新增消息是否以文本方式 @了自己
            at_me = False
            if self.bot_name:
                at_tag = f"@{self.bot_name}"
                for am in api_msgs:
                    if am["message_id"] not in newly_added:
                        continue
                    if am.get("text") and at_tag in am["text"]:
                        at_me = True
                        break
            if at_me:
                logger.info(
                    "补充 %d 条 bot 消息到群 %s，检测到文本 @%s，直接介入",
                    new_count, chat_id[-8:], self.bot_name,
                )
                recent = buf.get_recent(20)
                judgment = {"intervene": True, "reason": BOT_POLL_AT_REASON.format(bot_name=self.bot_name)}
                await self._intervene(chat_id, recent, judgment)
                # 取消 _intervene 内部触发的 bot_poll，避免 bot 互@循环
                timer = self._bot_poll_timers.pop(chat_id, None)
                if timer:
                    timer.cancel()
            else:
                logger.info(
                    "补充 %d 条 bot 消息到群 %s，触发评估 (%d/%d)",
                    new_count, chat_id[-8:], count + 1, 5,
                )
                await self._evaluate_buffer(chat_id)
                # 取消 _intervene 内部触发的 bot_poll，避免 evaluate→intervene→poll 循环
                timer = self._bot_poll_timers.pop(chat_id, None)
                if timer:
                    timer.cancel()

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

    async def _handle_card_action(self, event: Any) -> None:
        """处理卡片交互回调"""
        try:
            action = getattr(event, "action", None) or {}
            if isinstance(action, dict):
                value = action.get("value", {})
                tag = action.get("tag", "")
            else:
                value = getattr(action, "value", {}) or {}
                tag = getattr(action, "tag", "")

            action_type = value.get("action", "unknown") if isinstance(value, dict) else "unknown"

            # 提取操作者和卡片上下文
            operator = getattr(event, "operator", None)
            operator_id = ""
            if operator:
                open_id_obj = getattr(operator, "open_id", None)
                operator_id = open_id_obj if isinstance(open_id_obj, str) else str(open_id_obj or "")

            logger.info(
                "卡片回调: action=%s tag=%s operator=%s value=%s",
                action_type, tag, operator_id[:12], value,
            )

            if action_type == "confirm":
                logger.info("用户 %s 确认了操作", operator_id[:12])
            elif action_type == "cancel":
                logger.info("用户 %s 取消了操作", operator_id[:12])
            else:
                logger.info("卡片动作: %s (tag=%s)", action_type, tag)

        except Exception:
            logger.exception("解析卡片回调失败: %s", event)

    # 非文本消息类型映射
    _NON_TEXT_TYPES = {"image", "file", "audio", "media", "sticker", "post", "share_chat", "share_user"}

    def _extract_text(self, message: Any) -> str:
        """从消息中提取纯文本，非文本消息返回空字符串"""
        try:
            content = json.loads(message.content)
            return content.get("text", "")
        except (json.JSONDecodeError, TypeError):
            return ""

    def _is_non_text_message(self, message: Any) -> bool:
        """检查是否为非文本消息（图片、文件、语音等）"""
        msg_type = getattr(message, "message_type", None) or ""
        return msg_type in self._NON_TEXT_TYPES

    def _resolve_at_mentions(self, text: str, mentions: Any) -> str:
        """将 @占位符替换为真名，仅移除 bot 自己的 @。

        飞书文本中 @mention 表现为 @_user_1 等占位符，
        mentions 数组包含 key(@_user_1) → id(open_id) + name 映射。
        """
        if not mentions:
            # 无 mentions 信息，回退：只删 @_user_ 占位符
            import re
            return re.sub(r"@_user_\d+\s*", "", text).strip()

        for m in mentions:
            key = getattr(m, "key", "")
            if not key:
                continue
            open_id = ""
            if hasattr(m, "id") and hasattr(m.id, "open_id"):
                open_id = m.id.open_id
            name = getattr(m, "name", "") or ""

            if open_id == self.bot_open_id:
                # 移除 bot 自己的 @
                text = text.replace(key, "")
            elif name:
                # 其他用户的 @ 替换为真名
                text = text.replace(key, f"@{name}")
        return text.strip()
