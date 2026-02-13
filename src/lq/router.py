"""消息路由 + 介入判断 + 工具调用"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from lq.buffer import MessageBuffer, rule_check
from lq.executor.api import DirectAPIExecutor
from lq.feishu.sender import FeishuSender
from lq.memory import MemoryManager

logger = logging.getLogger(__name__)

# LLM 可调用的工具定义（Phase 4）
TOOLS = [
    {
        "name": "write_memory",
        "description": "将重要信息写入 MEMORY.md 的指定分区实现长期记忆持久化。用于记住用户偏好、重要事实、待办事项等。内容按 section 分区组织，相同 section 会覆盖更新。",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "记忆分区名（如：重要信息、用户偏好、备忘、待办事项）",
                },
                "content": {
                    "type": "string",
                    "description": "要记住的内容，支持 Markdown 格式，建议用列表组织多条信息",
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "write_chat_memory",
        "description": "将信息写入当前聊天窗口的专属记忆（chat_memory）。与 write_memory（全局记忆）不同，chat_memory 只在当前聊天窗口中可见。用于记住与当前对话者相关的信息，如对方的偏好、聊天中的要点和约定等。全局通用的信息请用 write_memory，仅与当前对话相关的信息请用 write_chat_memory。",
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": "记忆分区名（如：关于对方、聊天要点、约定事项）",
                },
                "content": {
                    "type": "string",
                    "description": "要记录的内容，支持 Markdown 格式",
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": "在飞书日历中创建一个新事件/日程。时间必须使用 ISO 8601 格式并包含时区偏移，例如 2026-02-13T15:00:00+08:00。请根据当前时间计算用户说的相对时间（如「5分钟后」「明天下午3点」）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "事件标题"},
                "start_time": {
                    "type": "string",
                    "description": "开始时间，必须为 ISO 8601 格式且包含时区，如 2026-02-13T15:00:00+08:00",
                },
                "end_time": {
                    "type": "string",
                    "description": "结束时间，必须为 ISO 8601 格式且包含时区，如 2026-02-13T16:00:00+08:00。若用户未指定结束时间，默认为开始时间后1小时",
                },
                "description": {
                    "type": "string",
                    "description": "事件描述（可选）",
                    "default": "",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": "查询指定时间范围内的日历事件。用于查看日程安排、检查时间冲突等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": "查询开始时间，ISO 8601 格式且包含时区，如 2026-02-13T00:00:00+08:00",
                },
                "end_time": {
                    "type": "string",
                    "description": "查询结束时间，ISO 8601 格式且包含时区，如 2026-02-13T23:59:59+08:00",
                },
            },
            "required": ["start_time", "end_time"],
        },
    },
    {
        "name": "send_card",
        "description": "发送一张信息卡片给用户。用于展示结构化信息如日程、任务列表等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "卡片标题"},
                "content": {"type": "string", "description": "卡片内容（支持 Markdown）"},
                "color": {
                    "type": "string",
                    "description": "卡片颜色主题",
                    "enum": ["blue", "green", "orange", "red", "purple"],
                    "default": "blue",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "read_self_file",
        "description": "读取自己的配置文件。可读文件: SOUL.md（人格定义）、MEMORY.md（长期记忆）、HEARTBEAT.md（心跳任务模板）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要读取的文件名",
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"],
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": "write_self_file",
        "description": "修改自己的配置文件。可写文件: SOUL.md（人格定义）、MEMORY.md（长期记忆）、HEARTBEAT.md（心跳任务模板）。修改 SOUL.md 会改变核心人格，请谨慎。建议先用 read_self_file 读取当前内容再修改。",
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": "要写入的文件名",
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"],
                },
                "content": {
                    "type": "string",
                    "description": "文件的完整新内容",
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_custom_tool",
        "description": "创建一个新的自定义工具。code 参数必须是完整的 Python 源代码，包含 TOOL_DEFINITION 字典（必须有 name, description, input_schema 三个 key）和 async def execute(input_data, context) 函数。context 是 dict，包含 sender、memory、calendar、http(httpx.AsyncClient) 四个 key。注意：TOOL_DEFINITION 中描述参数的 key 必须是 input_schema（不是 parameters）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称（字母、数字、下划线）"},
                "code": {
                    "type": "string",
                    "description": "完整 Python 源代码。TOOL_DEFINITION 必须包含 input_schema（非 parameters）描述工具参数。execute(input_data, context) 中 context['http'] 是 httpx.AsyncClient",
                },
            },
            "required": ["name", "code"],
        },
    },
    {
        "name": "list_custom_tools",
        "description": "列出所有已安装的自定义工具及其状态。",
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "test_custom_tool",
        "description": "校验工具代码（语法、安全性），不实际创建。用于在创建前检查代码是否合规。",
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要校验的 Python 源代码"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "delete_custom_tool",
        "description": "删除一个自定义工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "要删除的工具名称"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "toggle_custom_tool",
        "description": "启用或禁用一个自定义工具。",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称"},
                "enabled": {"type": "boolean", "description": "true=启用, false=禁用"},
            },
            "required": ["name", "enabled"],
        },
    },
    {
        "name": "send_message",
        "description": "主动发送一条纯文本消息到指定会话（chat_id）。用于主动联系用户、发送通知等。",
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "目标会话 ID（用户私聊或群聊的 chat_id）",
                },
                "text": {
                    "type": "string",
                    "description": "要发送的文本内容",
                },
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "schedule_message",
        "description": "定时发送一条消息。在指定的时间（ISO 8601 格式，含时区）到达后，自动发送消息到目标会话。用于实现「5分钟后提醒我」等场景。",
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": "目标会话 ID",
                },
                "text": {
                    "type": "string",
                    "description": "要发送的文本内容",
                },
                "send_at": {
                    "type": "string",
                    "description": "计划发送时间，ISO 8601 格式且包含时区，如 2026-02-13T15:05:00+08:00",
                },
            },
            "required": ["chat_id", "text", "send_at"],
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
        # Phase 3+: 注入依赖
        self.session_mgr: Any = None
        self.calendar: Any = None
        self.stats: Any = None
        self.cc_executor: Any = None
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
        else:
            logger.debug("忽略事件类型: %s", event_type)

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
                    "目前只能处理文字消息，图片、文件什么的还看不懂。有事直接打字跟我说就好。",
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
        system += (
            f"\n\n你正在和用户私聊。当前会话 chat_id={chat_id}。请直接、简洁地回复。"
            "如果涉及日程，使用 calendar 工具。"
            "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
            "\n\n<memory_guidance>"
            "\n你有两种记忆工具，请根据信息的性质选用："
            "\n- write_memory：写入全局记忆（MEMORY.md），用于跨聊天通用的信息（如用户生日、公司信息、通用偏好）"
            "\n- write_chat_memory：写入当前聊天的专属记忆，用于仅与当前对话相关的信息（如与这个人的约定、聊天中的要点、对方的个人偏好）"
            "\n当用户说「记住」什么时，判断这个信息是通用的还是专属于当前对话的，选择对应工具。"
            "\n</memory_guidance>"
            "\n\n<constraints>"
            "\n- 严格遵守 SOUL.md 定义的性格，这是你的核心身份，不可偏离"
            "\n- 回复务必简短精炼，不要长篇大论"
            "\n- 禁止使用 emoji"
            "\n- 不要自我否定能力——如果系统提供了某个工具或功能，就直接使用，不要说自己做不到"
            "\n- 当用户的需求超出现有工具能力时（如联网搜索、查汇率、翻译、查天气等），"
            "直接调用 create_custom_tool 创建新工具，然后立即调用它完成任务。"
            "不要先说「我需要创建工具」再行动——直接做，做完再说结果。"
            "不要说「我做不到」或「我没有这个功能」——你可以给自己创造能力。"
            "\n</constraints>"
        )

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
            session.add_message("assistant", reply_text, sender_name="你")
            if session.should_compact():
                await self._compact_session(session)

        # 记录日志
        self.memory.append_daily(f"- 私聊 [{sender_name}]: {combined_text[:50]}... → {'已回复' if reply_text else '回复失败'}\n", chat_id=chat_id)

    def _build_all_tools(self) -> list[dict]:
        """合并内置工具和自定义工具的定义列表。"""
        all_tools = list(TOOLS)
        if self.tool_registry:
            all_tools.extend(self.tool_registry.get_definitions())
        return all_tools

    # 行动前奏短语：只有极短回复以这些开头时才认为 LLM 想做事但没调工具
    _PREAMBLE_STARTS = (
        "好的，我", "好，我", "我来", "稍等", "马上",
        "让我", "我去", "好的，让我", "好，让我",
    )

    @staticmethod
    def _is_action_preamble(text: str) -> bool:
        """判断 LLM 回复是否是未完成的行动前奏。

        只有极短的（≤50字）、以行动短语开头的回复才被视为前奏。
        正常长度的对话回复不会触发，避免误催促。
        """
        text = text.strip()
        if len(text) > 50:
            return False
        return any(text.startswith(p) for p in MessageRouter._PREAMBLE_STARTS)

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

        当 LLM 返回纯文本但暗示要做更多事时，追加一轮 prompt 催促它行动，
        避免只说不做。支持创建工具 → 调用工具的多轮链路。
        """
        all_tools = self._build_all_tools()
        resp = await self.executor.reply_with_tools(system, messages, all_tools)

        max_iterations = 10
        iteration = 0
        nudge_count = 0  # 催促次数，防止无限催促
        tools_called: list[str] = []  # 跟踪已调用的工具名

        while iteration < max_iterations:
            iteration += 1

            if resp.pending and resp.tool_calls:
                # LLM 调用了工具 → 执行并继续
                tool_results = []
                for tc in resp.tool_calls:
                    tools_called.append(tc["name"])
                    result = await self._execute_tool(tc["name"], tc["input"], chat_id)
                    tool_results.append({
                        "tool_use_id": tc["id"],
                        "content": json.dumps(result, ensure_ascii=False),
                    })
                # 工具执行后刷新工具列表（可能有新工具被创建）
                all_tools = self._build_all_tools()
                resp = await self.executor.continue_after_tools(
                    system, resp.messages, all_tools, tool_results, resp.raw_response
                )
            elif (
                allow_nudge
                and resp.text
                and nudge_count < 1
                and self._is_action_preamble(resp.text)
            ):
                # LLM 输出了极短的行动前奏（"好的，我来..."）但没调工具 → 催促一次
                nudge_count += 1
                logger.info(
                    "检测到行动前奏，催促执行 (%d/1) 原文: %s",
                    nudge_count, resp.text[:100],
                )
                # 不发送前奏文本给用户（"好的，我来..."不是有效回复）
                continued_messages = resp.messages + [
                    {"role": "user", "content": "继续，直接调用工具即可。"}
                ]
                resp = await self.executor.reply_with_tools(
                    system, continued_messages, all_tools
                )
            else:
                # 正常结束
                break

        # 发送最终文本回复
        if resp.text:
            final = text_transform(resp.text) if text_transform else resp.text
            logger.info("回复: %s", final[:80])
            await self._send_reply(final, chat_id, reply_to_message_id)
            resp.text = final  # 确保返回值也是处理后的文本

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
                return {"success": True, "message": "已写入全局记忆"}

            elif name == "write_chat_memory":
                self.memory.update_chat_memory(
                    chat_id,
                    input_data["section"],
                    input_data["content"],
                )
                return {"success": True, "message": "已写入当前聊天记忆"}

            elif name == "calendar_create_event":
                if not self.calendar:
                    return {"success": False, "error": "日历模块未加载"}
                result = await self.calendar.create_event(
                    summary=input_data["summary"],
                    start_time=input_data["start_time"],
                    end_time=input_data["end_time"],
                    description=input_data.get("description", ""),
                )
                return result

            elif name == "calendar_list_events":
                if not self.calendar:
                    return {"success": False, "error": "日历模块未加载"}
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
                return {"success": True, "message": "卡片已发送"}

            elif name == "read_self_file":
                content = self.memory.read_self_file(input_data["filename"])
                if not content:
                    return {"success": True, "content": "(文件为空或不存在)"}
                return {"success": True, "content": content}

            elif name == "write_self_file":
                self.memory.write_self_file(
                    input_data["filename"],
                    input_data["content"],
                )
                return {"success": True, "message": f"{input_data['filename']} 已更新"}

            elif name == "create_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": "工具注册表未加载"}
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
                    return {"success": False, "error": "工具注册表未加载"}
                errors = self.tool_registry.validate_code(input_data["code"])
                if errors:
                    return {"success": False, "errors": errors}
                return {"success": True, "message": "代码校验通过"}

            elif name == "delete_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": "工具注册表未加载"}
                return self.tool_registry.delete_tool(input_data["name"])

            elif name == "toggle_custom_tool":
                if not self.tool_registry:
                    return {"success": False, "error": "工具注册表未加载"}
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
                return {"success": False, "error": "消息发送失败"}

            elif name == "schedule_message":
                from datetime import datetime as _dt, timezone as _tz, timedelta as _td

                send_at_str = input_data["send_at"]
                try:
                    send_at = _dt.fromisoformat(send_at_str)
                except ValueError:
                    return {"success": False, "error": f"时间格式无效: {send_at_str}，请使用 ISO 8601 格式"}

                cst = _tz(_td(hours=8))
                now = _dt.now(cst)
                if send_at.tzinfo is None:
                    send_at = send_at.replace(tzinfo=cst)
                delay = (send_at - now).total_seconds()
                if delay < 0:
                    return {"success": False, "error": "计划时间已过去"}

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
                return {"success": True, "message": f"已计划在 {send_at_str} 发送消息"}

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
                return {"success": False, "error": f"未知工具: {name}"}

        except Exception as e:
            logger.exception("工具执行失败: %s", name)
            return {"success": False, "error": str(e)}

    async def _handle_group(self, event: Any) -> None:
        """处理群聊消息"""
        message = event.message
        chat_id = message.chat_id

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
        text = self._extract_text(message)
        if not text:
            if self._is_non_text_message(message):
                await self.sender.reply_text(
                    message.message_id,
                    "目前只能处理文字消息，图片什么的还看不懂。有事打字说就好。",
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
                text = "（@了我但没说具体内容）"

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
                    name = m.get("sender_name", "未知")
                    if m.get("sender_id") == self.bot_open_id:
                        lines.append(f"{name}（你自己）：{m['text']}")
                    else:
                        lines.append(f"{name}：{m['text']}")
                group_context = "\n群聊近期消息：\n" + "\n".join(lines)

        system = self.memory.build_context(chat_id=message.chat_id)
        system += (
            f"\n\n你在群聊中被 {sender_name} @at 了。当前会话 chat_id={message.chat_id}。请针对对方的问题简洁回复。"
            f"{group_context}"
            "\n如果涉及日程，使用 calendar 工具。"
            "如果用户明确要求你执行某个任务且现有工具不够，可以用 create_custom_tool 创建工具来完成。"
            "\n\n<memory_guidance>"
            "\n- write_memory：写入全局记忆，用于跨聊天通用的信息"
            "\n- write_chat_memory：写入当前群聊的专属记忆，用于仅与本群相关的信息（如群聊话题、群友特点）"
            "\n</memory_guidance>"
            "\n\n<constraints>"
            "\n- 严格遵守 SOUL.md 定义的性格，这是你的核心身份，不可偏离"
            "\n- 回复务必简短精炼，不要长篇大论"
            "\n- 禁止使用 emoji"
            "\n- 不要在回复中暴露用户的内部 ID（如 ou_、oc_ 开头的标识符）"
            "\n- 群聊中禁止修改配置文件（SOUL.md, MEMORY.md, HEARTBEAT.md），这只允许在私聊中操作"
            "\n- 没有被明确要求执行任务时，不要主动调用工具——正常聊天就好"
            "\n</constraints>"
        )

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
            session.add_message("assistant", reply_text, sender_name="你")
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
                sender_name = "未知"
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

        # 从 API 拉取近期消息，补充事件机制收不到的机器人消息
        recent = await self._enrich_with_api_messages(chat_id, recent)

        soul = self.memory.read_soul()
        # 标注自己的消息，其他人（含 bot）正常记录名字
        my_name = self.bot_name or self.sender._user_name_cache.get(self.bot_open_id, "")
        lines: list[str] = []
        has_my_reply = False
        for m in recent:
            name = m.get("sender_name", "未知")
            if m.get("sender_id") == self.bot_open_id:
                lines.append(f"[{m['message_id']}] {name}（你自己）：{m['text']}")
                has_my_reply = True
            else:
                lines.append(f"[{m['message_id']}] {name}：{m['text']}")
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

        prompt = (
            f"你是一个 AI 助理（名字：{my_name or '未知'}）。以下是你的人格定义：\n{soul}\n\n"
            f"以下是群聊中的最近消息（方括号内是消息ID）：\n{conversation}\n\n"
            "请判断你是否应该主动参与这个对话。考虑：\n"
            "1. 对话是否与你相关或涉及你能帮助的话题？\n"
            "2. 你的介入是否会增加价值？\n"
            "3. 如果群里有另一个 bot 已经在处理这个话题，你就不要重复介入\n"
            "4. 如果对方是在和另一个 bot 对话，且没有涉及你，不要介入\n"
            "5. 这是否只是闲聊/情绪表达（通常不应介入）？\n"
            "6. 如果有人直接叫你的名字或提到你，应该介入\n"
            "7. 如果你已经在对话中发过言（标记为「你自己」），除非有人直接问你新问题或 @你，否则不要再发言\n"
            "8. 例外：如果用户明确要求你与其他人/bot 互动（如「你俩聊一下」「跟xx说说话」），即使对方是 bot 也应该积极配合\n\n"
            '仅输出 JSON: {"should_intervene": true/false, "reason": "简短原因", "reply_to_message_id": "要回复的消息ID或null"}'
        )

        try:
            result = await self.executor.quick_judge(prompt)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0]
            judgment = json.loads(result)

            if judgment.get("should_intervene"):
                logger.info("决定介入群聊 %s: %s", chat_id, judgment.get("reason"))
                await self._intervene(chat_id, recent, judgment)
            else:
                logger.debug("不介入群聊 %s: %s", chat_id, judgment.get("reason"))
        except (json.JSONDecodeError, Exception):
            logger.exception("介入判断失败")

    async def _intervene(self, chat_id: str, recent: list[dict], judgment: dict) -> None:
        """执行群聊介入"""
        system = self.memory.build_context(chat_id=chat_id)
        # 用真实名字构建对话上下文，标注 bot 消息
        name_to_id: dict[str, str] = {}
        lines: list[str] = []
        for m in recent:
            name = m.get("sender_name", "未知")
            name_to_id[name] = m["sender_id"]
            if m.get("sender_id") == self.bot_open_id:
                lines.append(f"{name}（你自己）：{m['text']}")
            else:
                lines.append(f"{name}：{m['text']}")
        conversation = "\n".join(lines)

        system += (
            f"\n\n你决定主动参与群聊对话。原因：{judgment.get('reason', '')}\n"
            f"最近的群聊消息：\n{conversation}\n\n"
            "如果要提及某人，使用 @名字 格式。回复保持简洁自然。"
            "\n\n<constraints>"
            "\n- 严格遵守 SOUL.md 定义的性格，这是你的核心身份，不可偏离"
            "\n- 回复务必简短精炼，不要长篇大论"
            "\n- 禁止使用 emoji"
            "\n- 不要在回复中暴露用户的内部 ID（如 ou_、oc_ 开头的标识符）"
            "\n- 群聊中禁止修改配置文件（SOUL.md, MEMORY.md, HEARTBEAT.md），这只允许在私聊中操作"
            "\n- 没有被明确要求执行任务时，不要主动调用工具——正常聊天就好"
            "\n- 如果之前给了错误信息被指出，大方承认并纠正，不要嘴硬"
            "\n- 不要编造实时数据（天气、汇率等），需要时先用 create_custom_tool 获取"
            "\n- 如果群里有另一个 bot 已经回复了相同话题，不要重复相同内容；可以补充或接话"
            "\n</constraints>"
        )

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
            session.add_message("user", f"[群聊旁听]\n{conversation}", sender_name="群聊")
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": f"[群聊旁听]\n{conversation}"}]

        reply_text = await self._reply_with_tool_loop(
            system, messages, chat_id, reply_to,
            text_transform=transform, allow_nudge=False,
        )

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name="你")
            if session.should_compact():
                await self._compact_session(session)

        if reply_text:
            self._schedule_bot_poll(chat_id)

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
                judgment = {"intervene": True, "reason": f"被其他 bot 以文本方式 @{self.bot_name}"}
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
        """压缩会话：提取长期记忆到 chat_memory，生成摘要后裁剪消息"""
        flush_prompt = self.memory.flush_before_compaction(session.messages)
        extracted = await self.executor.reply("", flush_prompt)
        if extracted.strip() and extracted.strip() != "无":
            self.memory.append_daily(f"### 会话记忆提取\n{extracted}\n", chat_id=session.chat_id)
            # 同时写入 per-chat 长期记忆，避免 daily log 过期后丢失
            from datetime import date as _date
            self.memory.append_chat_memory(
                session.chat_id,
                f"\n## 记忆提取 ({_date.today().isoformat()})\n{extracted}\n",
            )

        summary_prompt = (
            "请用 2-3 句话总结以下对话的关键内容：\n"
            + "\n".join(f"[{m['role']}] {m['content']}" for m in session.messages)
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
