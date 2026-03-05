"""Telegram 平台适配器

将 Telegram Bot API 封装为平台无关接口。
使用长轮询 (getUpdates) 模式接收消息。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

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
from lq.telegram.sender import _escape_markdown_entities, TelegramSender

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


class TelegramAdapter(PlatformAdapter):
    """Telegram 平台适配器。

    特性：
    - 长轮询接收消息
    - MarkdownV2 格式支持
    - 图片附件处理
    - Reaction 支持
    - 消息编辑支持
    """

    def __init__(self, bot_token: str, home_path) -> None:
        self._bot_token = bot_token
        self._home = home_path
        self._sender = TelegramSender(bot_token)
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
        text = message.text

        # 图片附件
        if message.image_path:
            # 读取图片并作为 file_id 或 URL 发送
            # 暂不支持本地图片上传，Telegram 需要先上传获取 file_id
            logger.warning("Telegram 暂不支持本地图片上传")
            return None

        # Telegram MarkdownV2 需要转义
        # 简单处理：转义所有特殊字符
        escaped_text = self._escape_for_markdown(text)

        # 回复消息
        reply_to = None
        if message.reply_to:
            # Telegram 的 message_id 是整数
            try:
                reply_to = int(message.reply_to)
            except ValueError:
                pass

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
        """转义文本为 MarkdownV2 格式。"""
        # 简单转义：保留基本换行，转义特殊字符
        # 这里使用保守策略，转义所有特殊字符
        special_chars = r"_*[]()~`>#+-=|{}.!"
        result = []
        for char in text:
            if char in special_chars:
                result.append(f"\\{char}")
            else:
                result.append(char)
        return "".join(result)

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        """发送思考信号 emoji reaction。"""
        try:
            msg_id = int(message_id)
            chat_id = self._msg_chat_map.get(message_id)
            if not chat_id:
                return None

            success = await self._sender.set_message_reaction(
                chat_id, msg_id, THINKING_EMOJI
            )
            if success:
                return f"{chat_id}:{msg_id}:reaction"
        except (ValueError, TypeError):
            pass
        return None

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        """移除思考信号。

        Telegram 不支持删除特定 reaction，需要发送空 reaction 列表。
        """
        try:
            msg_id = int(message_id)
            chat_id = self._msg_chat_map.get(message_id)
            if not chat_id:
                return

            # 发送空 reaction 列表以清除所有 reaction
            await self._sender.set_message_reaction(chat_id, msg_id, "")
        except (ValueError, TypeError):
            pass

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

        # 处理文档类型（可能是图片）
        if "document" in msg:
            doc = msg.get("document", {})
            mime_type = doc.get("mime_type", "")
            if mime_type.startswith("image/"):
                file_id = doc.get("file_id", "")
                if file_id:
                    image_keys.append(file_id)

        # 处理 caption（图片说明）
        caption = msg.get("caption", "")
        if caption:
            text = f"{text}\n{caption}" if text else caption

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
            msg_type = MessageType.FILE
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

    def _record_msg_chat(self, message_id: str, chat_id: str) -> None:
        """记录 message_id → chat_id 映射。"""
        self._msg_chat_map[message_id] = chat_id

        # 限制大小
        while len(self._msg_chat_map) > self._msg_chat_map_max:
            oldest = next(iter(self._msg_chat_map))
            del self._msg_chat_map[oldest]
