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
    ) -> None:
        self.executor = executor
        self.memory = memory
        self.sender = sender
        self.bot_open_id = bot_open_id

        # 群聊缓冲区
        self.group_buffers: dict[str, MessageBuffer] = {}
        # 私聊防抖：chat_id → {texts, message_id, timer, event}
        self._private_pending: dict[str, dict] = {}
        self._private_debounce_seconds: float = 1.5
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
        sender_name = await self.sender.get_user_name(event.sender.sender_id.open_id)
        logger.info("收到私聊 [%s]: %s", sender_name, text[:80])

        # 防抖：收集连续消息，延迟后统一处理
        pending = self._private_pending.get(chat_id)
        if pending:
            # 已有待处理消息，追加文本并更新 message_id（回复最后一条）
            pending["texts"].append(text)
            pending["message_id"] = message.message_id
            pending["sender_name"] = sender_name
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
            "\n\n你正在和用户私聊。请直接、简洁地回复。"
            "如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
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
            session.add_message("user", combined_text)
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
            session.add_message("assistant", reply_text)
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

    # 检测 LLM 回复中是否暗示要继续行动但没有调用工具
    _INTENT_KEYWORDS = ("创建", "搞定", "马上", "稍等", "先", "正在", "开始")

    async def _reply_with_tool_loop(
        self,
        system: str,
        messages: list[dict],
        chat_id: str,
        reply_to_message_id: str,
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
            elif resp.text and nudge_count < 2 and any(
                kw in resp.text for kw in self._INTENT_KEYWORDS
            ):
                # LLM 表示要行动但只输出了文本没调用工具 → 催促一轮
                nudge_count += 1
                logger.info("检测到未完成意图，催促 LLM 行动 (%d/2)", nudge_count)
                # 先把当前文本发给用户作为中间状态
                if resp.text.strip():
                    await self._send_reply(resp.text, chat_id, reply_to_message_id)
                    reply_to_message_id = None  # 后续用 send_text 而非 reply
                # 追加 user 消息催促继续
                continued_messages = resp.messages + [
                    {"role": "user", "content": "请直接使用工具执行，不要只说不做。"}
                ]
                resp = await self.executor.reply_with_tools(
                    system, continued_messages, all_tools
                )
            else:
                # 正常结束
                break

        # 发送最终文本回复
        if resp.text:
            logger.info("回复: %s", resp.text[:80])
            await self._send_reply(resp.text, chat_id, reply_to_message_id)

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

    async def _send_reply(self, text: str, chat_id: str, reply_to_message_id: str | None) -> None:
        """发送回复：优先 reply_text，fallback 到 send_text"""
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
                return {"success": True, "message": "已写入记忆"}

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
                msg_id = await self.sender.send_text(
                    input_data["chat_id"],
                    input_data["text"],
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

                target_chat_id = input_data["chat_id"]
                target_text = input_data["text"]
                sender_ref = self.sender

                async def _delayed_send():
                    await asyncio.sleep(delay)
                    await sender_ref.send_text(target_chat_id, target_text)
                    logger.info("定时消息已发送: chat=%s", target_chat_id)

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

        if is_at_me:
            await self._handle_group_at(event)
            return

        # 第一层规则：极短无实质消息直接忽略
        text = self._extract_text(message)
        if not text:
            logger.debug("群聊旁听: 非文本消息，跳过")
            return
        if rule_check(text) == "IGNORE":
            logger.debug("群聊旁听: 无实质消息，跳过: %s", text[:20])
            return

        sender_id = event.sender.sender_id.open_id
        sender_name = await self.sender.get_user_name(sender_id)
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
        text = self._strip_at_mentions(text).strip()
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

        sender_name = await self.sender.get_user_name(sender_id)
        logger.info("群聊 @at [%s]: %s", sender_name, text[:50])

        # 构建群聊上下文
        group_context = ""
        buf = self.group_buffers.get(message.chat_id)
        if buf:
            recent = buf.get_recent(10)
            if recent:
                lines = []
                for m in recent:
                    name = m.get("sender_name", "未知")
                    lines.append(f"{name}：{m['text']}")
                group_context = "\n群聊近期消息：\n" + "\n".join(lines)

        system = self.memory.build_context(chat_id=message.chat_id)
        system += (
            f"\n\n你在群聊中被 {sender_name} @at 了，请针对对方的问题简洁回复。"
            f"{group_context}"
            "\n如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
            "\n\n<constraints>"
            "\n- 严格遵守 SOUL.md 定义的性格，这是你的核心身份，不可偏离"
            "\n- 回复务必简短精炼，不要长篇大论"
            "\n- 禁止使用 emoji"
            "\n- 不要在回复中暴露用户的内部 ID（如 ou_、oc_ 开头的标识符）"
            "\n- 不要自我否定能力——如果系统提供了某个工具或功能，就直接使用，不要说自己做不到"
            "\n- 当用户的需求超出现有工具能力时（如联网搜索、查汇率、翻译、查天气等），"
            "直接调用 create_custom_tool 创建新工具，然后立即调用它完成任务。"
            "不要先说「我需要创建工具」再行动——直接做，做完再说结果。"
            "不要说「我做不到」或「我没有这个功能」——你可以给自己创造能力。"
            "\n</constraints>"
        )

        if self.session_mgr:
            session = self.session_mgr.get_or_create(message.chat_id)
            session.add_message("user", text)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": text}]

        await self._reply_with_tool_loop(
            system, messages, message.chat_id, message.message_id
        )

    async def _evaluate_buffer(self, chat_id: str) -> None:
        """第三层：LLM 判断是否介入群聊"""
        buf = self.group_buffers.get(chat_id)
        if not buf:
            return

        recent = buf.get_recent(10)
        if not recent:
            return

        buf.mark_evaluated()

        soul = self.memory.read_soul()
        conversation = "\n".join(
            f"[{m['message_id']}] {m.get('sender_name', '未知')}：{m['text']}" for m in recent
        )

        prompt = (
            f"你是一个 AI 助理。以下是你的人格定义：\n{soul}\n\n"
            f"以下是群聊中的最近消息（方括号内是消息ID）：\n{conversation}\n\n"
            "请判断你是否应该主动参与这个对话。考虑：\n"
            "1. 对话是否涉及你能帮助的话题？\n"
            "2. 你的介入是否会增加价值？\n"
            "3. 这是否只是闲聊/情绪表达（不应介入）？\n\n"
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
        # 用真实名字构建对话上下文
        name_to_id: dict[str, str] = {}
        lines: list[str] = []
        for m in recent:
            name = m.get("sender_name", "未知")
            name_to_id[name] = m["sender_id"]
            lines.append(f"{name}：{m['text']}")
        conversation = "\n".join(lines)

        system += (
            f"\n\n你决定主动参与群聊对话。原因：{judgment.get('reason', '')}\n"
            f"最近的群聊消息：\n{conversation}\n\n"
            "如果要提及某人，使用 @名字 格式。回复保持简洁自然。"
        )

        reply = await self.executor.reply(system, "请根据上下文生成一条自然的群聊回复。")

        # 将 @名字 替换为飞书 <at> 标记
        reply = self._replace_at_mentions(reply, name_to_id)

        # 校验 reply_to_message_id 是否为合法的飞书消息 ID（om_ 开头）
        reply_to = judgment.get("reply_to_message_id")
        valid_msg_ids = {m["message_id"] for m in recent}
        if reply_to and isinstance(reply_to, str) and reply_to.startswith("om_") and reply_to in valid_msg_ids:
            await self.sender.reply_text(reply_to, reply)
        else:
            await self.sender.send_text(chat_id, reply)

    @staticmethod
    def _replace_at_mentions(text: str, name_to_id: dict[str, str]) -> str:
        """将 @名字 替换为飞书 <at> 标记，按名字长度降序匹配避免子串冲突"""
        for name in sorted(name_to_id, key=len, reverse=True):
            tag = f'<at user_id="{name_to_id[name]}">{name}</at>'
            text = text.replace(f"@{name}", tag)
        return text

    async def _compact_session(self, session: Any) -> None:
        """压缩会话"""
        flush_prompt = self.memory.flush_before_compaction(session.messages)
        extracted = await self.executor.reply("", flush_prompt)
        if extracted.strip() and extracted.strip() != "无":
            self.memory.append_daily(f"### 会话记忆提取\n{extracted}\n", chat_id=session.chat_id)

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

    def _strip_at_mentions(self, text: str) -> str:
        """移除 @at 标记"""
        import re
        return re.sub(r"@\S+\s*", "", text).strip()
