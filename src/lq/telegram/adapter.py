"""Telegram 平台适配器

将 Telegram Bot API 封装为平台无关接口。
使用长轮询 (getUpdates) 模式接收消息。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import telegramify_markdown

from lq.platform.adapter import PlatformAdapter
from lq.platform.types import (
    BotIdentity,
    CardAction,
    ChatMember,
    ChatType,
    IncomingMessage,
    Mention,
    MessageType,
    OutgoingMessage,
    Reaction,
    SenderType,
)
from lq.telegram.sender import TelegramSender

logger = logging.getLogger(__name__)

# Telegram 消息类型映射
_MSG_TYPE_MAP: dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "photo": MessageType.IMAGE,
    "document": MessageType.FILE,
    "sticker": MessageType.STICKER,
    "voice": MessageType.AUDIO,
    "video": MessageType.VIDEO,
    "location": MessageType.UNKNOWN,
    "venue": MessageType.UNKNOWN,
}

# 思考信号 emoji
THINKING_EMOJI = "⏳"

# 可识别为文本文件的 MIME 前缀/类型
_TEXT_MIME_PREFIXES = ("text/",)
_TEXT_MIME_TYPES = frozenset({
    "application/json",
    "application/xml",
    "application/javascript",
    "application/x-python",
    "application/x-sh",
    "application/x-shellscript",
    "application/yaml",
    "application/x-yaml",
    "application/toml",
    "application/sql",
    "application/x-httpd-php",
    "application/x-ruby",
    "application/x-perl",
    "application/x-lua",
    "application/xhtml+xml",
    "application/ld+json",
    "application/graphql",
})

# 通过扩展名识别文本文件
_TEXT_EXTENSIONS = frozenset({
    ".txt", ".md", ".markdown", ".rst", ".csv", ".tsv",
    ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
    ".xml", ".html", ".htm", ".css", ".js", ".ts", ".jsx", ".tsx",
    ".py", ".pyi", ".pyx", ".rb", ".pl", ".lua", ".sh", ".bash", ".zsh",
    ".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".go", ".rs", ".swift",
    ".sql", ".r", ".m", ".tex", ".bib", ".log",
    ".env", ".gitignore", ".dockerignore", ".editorconfig",
    ".makefile", ".cmake",
})

# 文本文件最大下载大小（字节）
_TEXT_FILE_MAX_SIZE = 1024 * 1024  # 1 MB


def _is_text_document(mime_type: str, file_name: str) -> bool:
    """判断文档是否为文本类文件。"""
    mime_lower = mime_type.lower()
    for prefix in _TEXT_MIME_PREFIXES:
        if mime_lower.startswith(prefix):
            return True
    if mime_lower in _TEXT_MIME_TYPES:
        return True
    # 通过文件扩展名判断
    if file_name:
        import os
        _, ext = os.path.splitext(file_name.lower())
        if ext in _TEXT_EXTENSIONS:
            return True
        # 无扩展名的常见文件
        basename = os.path.basename(file_name.lower())
        if basename in ("makefile", "dockerfile", "vagrantfile", "gemfile",
                        "rakefile", "procfile", "brewfile"):
            return True
    return False


class TelegramAdapter(PlatformAdapter):
    """Telegram 平台适配器。

    特性：
    - 长轮询接收消息
    - MarkdownV2 格式支持
    - 本地图片上传（multipart/form-data）
    - Reaction 支持
    - 消息编辑支持
    """

    def __init__(self, bot_token: str, home_path, proxy: str = "") -> None:
        self._bot_token = bot_token
        self._home = home_path

        # 代理：优先用参数，其次读环境变量（与 DiscordAdapter 行为一致）
        if not proxy:
            import os
            proxy = (
                os.environ.get("HTTPS_PROXY")
                or os.environ.get("HTTP_PROXY")
                or os.environ.get("ALL_PROXY")
                or ""
            )
        self._proxy = proxy

        self._sender = TelegramSender(bot_token, proxy=proxy)
        self._queue: asyncio.Queue | None = None
        self._raw_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()

        # 用户名缓存（user_id → first_name）
        self._name_cache: dict[str, str] = {}

        # message_id → chat_id 映射（用于 reaction 事件）
        self._msg_chat_map: dict[str, str] = {}
        self._msg_chat_map_max = 500

        self._identity: BotIdentity | None = None

        # typing indicator tasks: message_id → asyncio.Task
        self._typing_tasks: dict[str, asyncio.Task] = {}

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        """获取 bot 身份信息。"""
        if self._identity:
            return self._identity

        await self._sender.__aenter__()
        bot_info = await self._sender.get_me()
        if not bot_info:
            raise RuntimeError("无法获取 Telegram Bot 信息，请检查 bot_token")

        bot_id = self._sender.bot_id
        bot_name = self._sender.bot_name

        self._identity = BotIdentity(bot_id=bot_id, bot_name=bot_name)
        return self._identity

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        """启动长轮询接收消息。"""
        self._queue = queue

        # 启动事件转换协程
        self._tasks.append(
            asyncio.create_task(self._event_converter(), name="telegram-converter")
        )

        # 启动长轮询协程
        self._tasks.append(
            asyncio.create_task(self._poll_updates(), name="telegram-poll")
        )

        logger.info("Telegram 适配器已启动")

    async def disconnect(self) -> None:
        """停止长轮询，释放资源。"""
        self._shutdown.set()

        # 取消 typing tasks
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()

        for t in self._tasks:
            t.cancel()

        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3.0)

        self._tasks.clear()
        await self._sender.__aexit__(None, None, None)

        logger.info("Telegram 适配器已停止")

    # ── 表达 ──

    async def send(self, message: OutgoingMessage) -> str | None:
        """发送消息。"""
        # 发消息前取消该聊天的 typing task，避免残留
        self._cancel_typing_for_chat(message.chat_id)

        # 优先处理 card，将卡片转换为格式化文本
        if message.card:
            text = self._convert_card_to_text(message.card)
        else:
            text = message.text

        # 回复消息
        reply_to = None
        if message.reply_to:
            try:
                reply_to = int(message.reply_to)
            except ValueError:
                pass

        # 本地图片上传
        if message.image_path:
            caption = self._escape_for_markdown(text) if text else ""
            result = await self._sender.send_photo_local(
                message.chat_id,
                message.image_path,
                caption=caption,
                reply_to_message_id=reply_to,
            )
            if result:
                return str(result.get("message_id", ""))
            return None

        # 本地文件上传
        if message.file_path:
            caption = self._escape_for_markdown(text) if text else ""
            result = await self._sender.send_document(
                message.chat_id,
                message.file_path,
                caption=caption,
                reply_to_message_id=reply_to,
            )
            if result:
                return str(result.get("message_id", ""))
            return None

        # Telegram MarkdownV2 需要转义
        escaped_text = self._escape_for_markdown(text)

        result = await self._sender.send_message(
            message.chat_id,
            escaped_text,
            reply_to_message_id=reply_to,
        )

        if result:
            msg_id = str(result.get("message_id", ""))
            return msg_id

        return None

    def _escape_for_markdown(self, text: str) -> str:
        """将标准 Markdown 转换为 Telegram MarkdownV2 格式。"""
        try:
            # 使用 telegramify-markdown 将标准 Markdown 转为 Telegram MarkdownV2
            return telegramify_markdown.markdownify(text)
        except Exception:
            # 转换失败时，回退到简单转义
            logger.debug("Markdown 转换失败，使用简单转义")
            # 简单转义：保留基本换行，转义特殊字符
            special_chars = r"_*[]()~`>#+-=|{}.!"
            result = []
            for char in text:
                if char in special_chars:
                    result.append(f"\\{char}")
                else:
                    result.append(char)
            return "".join(result)

    @staticmethod
    def _convert_card_to_text(card: dict) -> str:
        """将标准卡片转换为 Telegram 文本格式。

        Telegram 不支持交互式卡片，因此将卡片内容转换为格式化文本。
        """
        card_type = card.get("type", "")
        title = card.get("title", "")
        content = card.get("content", "")

        # 根据不同类型构建格式化文本
        if card_type == "schedule":
            events = card.get("events", [])
            if not events:
                return "📅 今日日程\n\n今天没有日程安排。"

            lines = ["📅 今日日程\n"]
            for e in events:
                start = e.get("start_time", "")
                end = e.get("end_time", "")
                summary = e.get("summary", "未命名事件")
                time_str = f"{start} - {end}" if start else "全天"
                lines.append(f"• *{time_str}*  {summary}")

            return "\n".join(lines)

        elif card_type == "task_list":
            tasks = card.get("tasks", [])
            if not tasks:
                return "📋 任务列表\n\n暂无任务。"

            lines = ["📋 任务列表\n"]
            for t in tasks:
                status = "✅" if t.get("done") else "⬜"
                lines.append(f"{status} {t.get('title', '未命名任务')}")

            return "\n".join(lines)

        elif card_type == "error":
            error_msg = card.get("message", "")
            title_text = card.get("title", "错误")
            return f"⚠️ *{title_text}*\n```\n{error_msg}\n```"

        elif card_type == "confirm":
            confirm_text = card.get("confirm_text", "确认")
            cancel_text = card.get("cancel_text", "取消")
            return (
                f"🔔 *{title}*\n\n{content}\n\n"
                f"请回复: _{confirm_text}_ 或 _{cancel_text}_"
            )

        # 默认 info 类型
        parts = []
        if title:
            parts.append(f"*{title}*")
        if content:
            parts.append(content)

        # 添加额外字段
        fields = card.get("fields", [])
        if fields:
            field_lines = []
            for field in fields:
                key = field.get("key", "")
                value = field.get("value", "")
                if key and value:
                    field_lines.append(f"• *{key}*: {value}")
            if field_lines:
                parts.append("\n".join(field_lines))

        return "\n\n".join(parts) if parts else str(card)

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        """后台 task 每 4 秒刷新 typing indicator。

        Telegram sendChatAction typing 持续约 5 秒，
        每 4 秒刷新一次保证无间断。
        """
        chat_id = self._msg_chat_map.get(message_id)
        if not chat_id:
            return None

        async def _typing_loop() -> None:
            try:
                while True:
                    await self._sender.send_chat_action(chat_id, "typing")
                    await asyncio.sleep(4.0)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(
            _typing_loop(), name=f"tg-typing-{message_id}"
        )
        self._typing_tasks[message_id] = task
        # 立即触发一次
        await self._sender.send_chat_action(chat_id, "typing")
        return message_id

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        """取消 typing indicator 后台任务。"""
        task = self._typing_tasks.pop(message_id, None)
        if task:
            task.cancel()

    # ── 感官 ──

    async def fetch_media(
        self, message_id: str, resource_key: str
    ) -> tuple[str, str] | None:
        """下载图片媒体。

        resource_key 为 Telegram 的 file_id。
        """
        return await self._sender.download_image_as_base64(resource_key)

    # ── 认知 ──

    async def resolve_name(self, user_id: str) -> str:
        """解析用户 ID 为名字。"""
        # 检查缓存
        cached = self._name_cache.get(user_id)
        if cached:
            return cached

        # user_id 在 Telegram 中是数字
        try:
            uid = int(user_id)
            # 尝试从缓存的聊天信息获取
            # 这里返回 ID 尾部作为回退
            fallback = user_id[-6:] if len(user_id) > 6 else user_id
            self._name_cache[user_id] = fallback
            return fallback
        except ValueError:
            return user_id

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        """获取聊天成员列表。

        Telegram 没有直接获取所有成员的 API，
        只能通过 getChatMemberCount 获取数量，
        或通过 getChatAdministrators 获取管理员。
        这里返回管理员列表。
        """
        try:
            admins = await self._sender.get_chat_administrators(chat_id)
            if not admins:
                return []

            members: list[ChatMember] = []
            for admin in admins:
                user = admin.get("user", {})
                user_id = str(user.get("id", ""))
                name = user.get("first_name", "")
                is_bot = user.get("is_bot", False)

                if user_id:
                    self._name_cache[user_id] = name
                    members.append(
                        ChatMember(user_id=user_id, name=name, is_bot=is_bot)
                    )

            return members
        except Exception:
            logger.exception("获取 Telegram 成员列表失败")
            return []

    # ── 可选行为 ──

    async def react(self, message_id: str, emoji: str) -> str | None:
        """添加 emoji reaction。"""
        try:
            msg_id = int(message_id)
            chat_id = self._msg_chat_map.get(message_id)
            if not chat_id:
                return None

            success = await self._sender.set_message_reaction(chat_id, msg_id, emoji)
            if success:
                return f"{chat_id}:{msg_id}:reaction:{emoji}"
        except (ValueError, TypeError):
            pass
        return None

    async def edit(self, message_id: str, new_content: OutgoingMessage) -> bool:
        """编辑已发送的消息。"""
        try:
            msg_id = int(message_id)
            text = self._escape_for_markdown(new_content.text)

            result = await self._sender.edit_message_text(
                new_content.chat_id, msg_id, text
            )
            return result is not None
        except (ValueError, TypeError):
            return False

    # ── 内部：长轮询 ──

    async def _poll_updates(self) -> None:
        """长轮询获取 Telegram 更新。"""
        logger.info("Telegram 长轮询启动")
        offset = 0

        while not self._shutdown.is_set():
            try:
                updates = await self._sender.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=["message", "edited_message", "message_reaction", "my_chat_member", "chat_member"],
                )

                for update in updates:
                    update_id = update.get("update_id", 0)
                    offset = update_id + 1

                    await self._raw_queue.put(update)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("长轮询异常")
                # 发生异常时等待一段时间后重试
                await asyncio.sleep(5)

            # 无论成功或失败，都添加一个短暂延迟，避免过于频繁的请求
            # getUpdates 本身有 30 秒超时，所以这里只需要在发生错误时添加额外延迟
            # 正常情况下，getUpdates 会等待 30 秒，不需要额外延迟

        logger.info("Telegram 长轮询已停止")

    # ── 内部：事件转换 ──

    async def _event_converter(self) -> None:
        """将 Telegram Update 转换为标准事件。"""
        logger.info("Telegram 事件转换器启动")

        while not self._shutdown.is_set():
            try:
                update = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                # 消息事件
                if "message" in update:
                    await self._convert_message(update["message"])
                # 编辑消息事件
                elif "edited_message" in update:
                    await self._convert_message(update["edited_message"], is_edited=True)
                # Reaction 事件
                elif "message_reaction" in update:
                    self._convert_reaction(update["message_reaction"])
                # 成员变更事件
                elif "my_chat_member" in update:
                    self._convert_my_chat_member(update["my_chat_member"])
                elif "chat_member" in update:
                    self._convert_chat_member(update["chat_member"])

            except Exception:
                logger.exception("转换 Telegram 事件失败")

        logger.info("Telegram 事件转换器已停止")

    async def _convert_message(self, msg: dict, is_edited: bool = False) -> None:
        """转换 Telegram 消息为标准 IncomingMessage。"""
        if not self._queue:
            return

        chat = msg.get("chat", {})
        chat_id = str(chat.get("id", ""))
        chat_type_str = chat.get("type", "private")

        from_user = msg.get("from", {})
        sender_id = str(from_user.get("id", ""))
        sender_name = from_user.get("first_name", "")
        is_bot = from_user.get("is_bot", False)

        # 忽略自己的消息
        identity = self._identity
        if identity and sender_id == identity.bot_id:
            return

        msg_id = str(msg.get("message_id", ""))
        self._record_msg_chat(msg_id, chat_id)

        # 缓存用户名
        if sender_id and sender_name:
            self._name_cache[sender_id] = sender_name

        # 提取文本和图片
        text = msg.get("text", "") or ""
        image_keys: list[str] = []

        # 处理图片
        if "photo" in msg:
            photos = msg.get("photo", [])
            if photos:
                # 选择最大尺寸的图片
                largest = photos[-1]
                file_id = largest.get("file_id", "")
                if file_id:
                    image_keys.append(file_id)

        # 处理文档类型（可能是图片或文本文件）
        doc_text_parts: list[str] = []
        if "document" in msg:
            doc = msg.get("document", {})
            mime_type = doc.get("mime_type", "")
            file_id = doc.get("file_id", "")
            file_name = doc.get("file_name", "")
            if mime_type.startswith("image/"):
                if file_id:
                    image_keys.append(file_id)
            elif file_id and _is_text_document(mime_type, file_name):
                # 文本类文件：下载内容并合并到消息文本
                content = await self._download_text_document(file_id)
                if content is not None:
                    header = f"📎 文件: {file_name}" if file_name else "📎 文件"
                    doc_text_parts.append(f"{header}\n```\n{content}\n```")

        # 处理 caption（图片/文件说明）
        caption = msg.get("caption", "")
        if caption:
            text = f"{text}\n{caption}" if text else caption

        # 合并文本文件内容到消息文本
        if doc_text_parts:
            text = "\n".join(filter(None, [text] + doc_text_parts))

        # 检测 @提及
        mentions: list[Mention] = []
        is_mention_bot = False

        # Telegram 的 @提及在 text 中表现为 @username
        if identity and identity.bot_name:
            # 检查是否通过 username 提及 bot
            bot_username = msg.get("bot_username", "")
            if bot_username in text or f"@{identity.bot_name}" in text:
                is_mention_bot = True

        # Telegram 私聊始终视为提及
        if chat_type_str == "private":
            is_mention_bot = True

        # 回复消息
        reply_to_msg = msg.get("reply_to_message", {})
        reply_to_id = str(reply_to_msg.get("message_id", "")) if reply_to_msg else ""

        # 消息类型
        msg_type = MessageType.TEXT
        if "photo" in msg:
            msg_type = MessageType.IMAGE
        elif "sticker" in msg:
            msg_type = MessageType.STICKER
        elif "document" in msg:
            # 文本文件已合并到 text，视为 TEXT；否则为 FILE
            msg_type = MessageType.TEXT if doc_text_parts else MessageType.FILE
        elif "voice" in msg:
            msg_type = MessageType.AUDIO
        elif "video" in msg:
            msg_type = MessageType.VIDEO

        # 时间戳
        timestamp = int(msg.get("date", 0)) * 1000

        incoming = IncomingMessage(
            message_id=msg_id,
            chat_id=chat_id,
            chat_type=ChatType.PRIVATE if chat_type_str == "private" else ChatType.GROUP,
            sender_id=sender_id,
            sender_type=SenderType.BOT if is_bot else SenderType.USER,
            sender_name=sender_name,
            message_type=msg_type,
            text=text.strip(),
            mentions=mentions,
            is_mention_bot=is_mention_bot,
            image_keys=image_keys,
            reply_to_id=reply_to_id,
            timestamp=timestamp,
            raw=msg,
        )

        self._queue.put_nowait({"event_type": "message", "message": incoming})

    def _convert_reaction(self, reaction_data: dict) -> None:
        """转换 reaction 事件。"""
        if not self._queue:
            return

        chat = reaction_data.get("chat", {})
        chat_id = str(chat.get("id", ""))
        msg_id = str(reaction_data.get("message_id", ""))

        from_user = reaction_data.get("from", {})
        user_id = str(from_user.get("id", ""))

        # 获取新添加的 reaction
        old_reaction = reaction_data.get("old_reaction", [])
        new_reaction = reaction_data.get("new_reaction", [])

        # Telegram 的 reaction 是数组
        for reaction in new_reaction:
            if reaction.get("type") == "emoji":
                emoji = reaction.get("emoji", "")
                is_thinking = emoji == THINKING_EMOJI

                std_reaction = Reaction(
                    reaction_id=f"{msg_id}:{emoji}",
                    chat_id=chat_id,
                    message_id=msg_id,
                    emoji=emoji,
                    operator_id=user_id,
                    operator_type=SenderType.USER,
                    is_thinking_signal=is_thinking,
                )

                self._queue.put_nowait({
                    "event_type": "reaction",
                    "reaction": std_reaction,
                })

    def _convert_my_chat_member(self, data: dict) -> None:
        """转换 bot 自己的成员状态变更（如被添加/移除）。"""
        if not self._queue:
            return

        chat = data.get("chat", {})
        chat_id = str(chat.get("id", ""))

        from_user = data.get("from", {})
        old_state = data.get("old_chat_member", {})
        new_state = data.get("new_chat_member", {})

        old_status = old_state.get("status", "")
        new_status = new_state.get("status", "")

        # 检测 bot 被添加到群组
        if old_status in ("left", "kicked") and new_status == "member":
            self._queue.put_nowait({
                "event_type": "member_change",
                "chat_id": chat_id,
                "change_type": "bot_joined",
                "users": [],
            })

        # 检测 bot 被移除
        elif new_status in ("left", "kicked"):
            self._queue.put_nowait({
                "event_type": "member_change",
                "chat_id": chat_id,
                "change_type": "bot_left",
                "users": [],
            })

    def _convert_chat_member(self, data: dict) -> None:
        """转换其他成员状态变更。"""
        if not self._queue:
            return

        chat = data.get("chat", {})
        chat_id = str(chat.get("id", ""))

        from_user = data.get("from", {})
        old_state = data.get("old_chat_member", {})
        new_state = data.get("new_chat_member", {})

        user = new_state.get("user", {})
        user_id = str(user.get("id", ""))
        name = user.get("first_name", "")

        old_status = old_state.get("status", "")
        new_status = new_state.get("status", "")

        # 用户加入
        if old_status == "left" and new_status == "member":
            if user_id and name:
                self._name_cache[user_id] = name

            self._queue.put_nowait({
                "event_type": "member_change",
                "chat_id": chat_id,
                "change_type": "user_joined",
                "users": [{"user_id": user_id, "name": name}],
            })

        # 用户离开
        elif new_status == "left":
            self._queue.put_nowait({
                "event_type": "member_change",
                "chat_id": chat_id,
                "change_type": "user_left",
                "users": [{"user_id": user_id, "name": name}],
            })

    # ── 内部：辅助 ──

    def _cancel_typing_for_chat(self, chat_id: str) -> None:
        """取消指定聊天的所有 typing task。"""
        to_remove: list[str] = []
        for msg_id, task in self._typing_tasks.items():
            if self._msg_chat_map.get(msg_id) == chat_id:
                task.cancel()
                to_remove.append(msg_id)
        for msg_id in to_remove:
            del self._typing_tasks[msg_id]

    def _record_msg_chat(self, message_id: str, chat_id: str) -> None:
        """记录 message_id → chat_id 映射。"""
        self._msg_chat_map[message_id] = chat_id

        # 限制大小
        while len(self._msg_chat_map) > self._msg_chat_map_max:
            oldest = next(iter(self._msg_chat_map))
            del self._msg_chat_map[oldest]

    async def _download_text_document(self, file_id: str) -> str | None:
        """下载文本类文件并返回其 UTF-8 内容。

        超过 _TEXT_FILE_MAX_SIZE 的文件会被截断并附加提示。
        """
        try:
            file_info = await self._sender.get_file(file_id)
            if not file_info:
                return None

            file_size = file_info.get("file_size", 0)
            file_path = file_info.get("file_path", "")
            if not file_path:
                return None

            raw = await self._sender.download_file(file_path)
            if raw is None:
                return None

            # 解码为文本
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                content = raw.decode("utf-8", errors="replace")

            # 截断过大的文件
            if len(content) > _TEXT_FILE_MAX_SIZE:
                content = content[:_TEXT_FILE_MAX_SIZE] + "\n\n... (文件过大，已截断)"

            return content
        except Exception:
            logger.exception("下载文本文件失败: file_id=%s", file_id)
            return None
