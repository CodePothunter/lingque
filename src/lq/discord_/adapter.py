"""Discord 平台适配器 — 将 discord.py 封装为平台无关接口"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
from typing import Any

import discord

from lq.discord_.sender import DiscordSender
from lq.platform.adapter import PlatformAdapter
from lq.platform.types import (
    BotIdentity,
    ChatMember,
    ChatType,
    IncomingMessage,
    Mention,
    MessageType,
    OutgoingMessage,
    Reaction,
    SenderType,
)

logger = logging.getLogger(__name__)

# Discord 消息长度限制
DISCORD_MAX_LEN = 2000


class DiscordAdapter(PlatformAdapter):
    """Discord 平台适配器。

    架构与 FeishuAdapter 对称：
      discord.Client (daemon thread, 接收 WS 事件)
          ↓ loop.call_soon_threadsafe
      _raw_queue
          ↓ _event_converter (async task)
      标准事件 → main queue → router.handle()

      DiscordSender (httpx, 主事件循环)
          ← send / react / typing 等出站操作
    """

    def __init__(self, bot_token: str, proxy: str = "") -> None:
        self._bot_token = bot_token
        self._proxy = proxy
        self._sender = DiscordSender(bot_token)
        self._queue: asyncio.Queue | None = None
        self._raw_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()
        self._identity: BotIdentity | None = None

        # discord.Client 实例（在 connect 中创建）
        self._client: discord.Client | None = None
        self._client_ready = threading.Event()

        # 缓存：user_id → display_name
        self._name_cache: dict[str, str] = {}

        # message_id → channel_id 映射（用于 react/edit/unsend）
        self._msg_channel_map: dict[str, str] = {}
        self._msg_channel_map_max = 500

        # typing indicator tasks: message_id → asyncio.Task
        self._typing_tasks: dict[str, asyncio.Task] = {}

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        if self._identity:
            return self._identity
        info = await self._sender.fetch_bot_info()
        bot_id = info.get("id", "")
        bot_name = info.get("username", "")
        self._identity = BotIdentity(bot_id=bot_id, bot_name=bot_name)
        logger.info("Discord bot 身份: id=%s name=%s", bot_id, bot_name)
        return self._identity

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        main_loop = asyncio.get_running_loop()

        # 创建 discord.Client（需要 message_content + members intents）
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        # discord.py 内部用 aiohttp，不读环境变量代理，需要显式传入
        proxy = self._proxy
        if not proxy:
            import os
            proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
        client = discord.Client(intents=intents, proxy=proxy or None)
        self._client = client

        raw_queue = self._raw_queue
        client_ready = self._client_ready

        @client.event
        async def on_ready() -> None:
            logger.info("Discord client ready: %s (%s)", client.user, client.user.id)
            client_ready.set()

        @client.event
        async def on_message(message: discord.Message) -> None:
            # 等待 client ready，避免 client.user 为 None 的竞态
            if not client_ready.is_set():
                return
            # 忽略自己的消息
            if message.author == client.user:
                return
            # 跨线程投递到主事件循环的 _raw_queue
            main_loop.call_soon_threadsafe(
                raw_queue.put_nowait,
                {"type": "message", "message": message},
            )

        @client.event
        async def on_raw_reaction_add(payload: discord.RawReactionActionEvent) -> None:
            if not client_ready.is_set():
                return
            if payload.user_id == client.user.id:
                return
            main_loop.call_soon_threadsafe(
                raw_queue.put_nowait,
                {"type": "reaction", "payload": payload},
            )

        @client.event
        async def on_member_join(member: discord.Member) -> None:
            main_loop.call_soon_threadsafe(
                raw_queue.put_nowait,
                {"type": "member_join", "member": member},
            )

        # 在 daemon 线程中运行 discord.Client
        def _run_client() -> None:
            try:
                client.run(self._bot_token, log_handler=None)
            except Exception:
                logger.exception("Discord client 线程异常退出")

        thread = threading.Thread(target=_run_client, name="discord-ws", daemon=True)
        thread.start()
        logger.info("Discord WebSocket 线程已启动")

        # 启动事件转换协程
        self._tasks.append(
            asyncio.create_task(self._event_converter(), name="discord-converter")
        )

    async def disconnect(self) -> None:
        self._shutdown.set()
        # 取消 typing tasks
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        # 取消 converter tasks
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3.0)
        self._tasks.clear()
        # 关闭 discord.Client — 必须在 client 自己的事件循环上调用
        if self._client and not self._client.is_closed():
            try:
                client_loop = self._client.loop
                if client_loop and client_loop.is_running():
                    future = asyncio.run_coroutine_threadsafe(
                        self._client.close(), client_loop,
                    )
                    future.result(timeout=5.0)
                else:
                    logger.warning("Discord client loop 未运行，无法优雅关闭")
            except Exception:
                logger.warning("关闭 Discord client 失败", exc_info=True)

    # ── 表达 ──

    # 飞书 chat_id 前缀：oc_（群聊）、ou_（用户）、on_（通知群）
    _FEISHU_PREFIXES = ("oc_", "ou_", "on_")

    async def send(self, message: OutgoingMessage) -> str | None:
        # 防御性校验：拒绝飞书格式的 chat_id
        if message.chat_id and message.chat_id.startswith(self._FEISHU_PREFIXES):
            raise ValueError(
                f"Discord adapter 收到飞书格式 chat_id: {message.chat_id[:20]}，"
                "请检查路由逻辑是否将消息发送到了错误的平台"
            )

        # 发消息前先取消该频道的 typing task，
        # 避免 send 后 typing 再次触发导致"正在输入"残留
        self._cancel_typing_for_channel(message.chat_id)

        text = message.text or ""

        # card → Discord Embed
        embed = None
        if message.card:
            embed = self._convert_card_to_embed(message.card)
            if not text:
                text = ""

        # 长消息自动分片（Discord 2000 字符限制）
        if len(text) > DISCORD_MAX_LEN and not embed:
            return await self._send_chunked(message.chat_id, text, message.reply_to)

        return await self._sender.send_message(
            message.chat_id,
            text,
            reply_to=message.reply_to,
            embed=embed,
        )

    async def _send_chunked(
        self, channel_id: str, text: str, reply_to: str = "",
    ) -> str | None:
        """将长消息分片发送，返回最后一片的 message_id。"""
        chunks: list[str] = []
        while text:
            if len(text) <= DISCORD_MAX_LEN:
                chunks.append(text)
                break
            # 在 2000 字符内找最后一个换行符分割
            split_at = text.rfind("\n", 0, DISCORD_MAX_LEN)
            if split_at <= 0:
                split_at = DISCORD_MAX_LEN
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        last_id = None
        for i, chunk in enumerate(chunks):
            msg_id = await self._sender.send_message(
                channel_id, chunk,
                reply_to=reply_to if i == 0 else "",
            )
            if msg_id:
                last_id = msg_id
                self._record_msg_channel(msg_id, channel_id)
        return last_id

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        """后台 task 每 8 秒刷新 typing indicator。"""
        channel_id = self._msg_channel_map.get(message_id, "")
        if channel_id and channel_id.startswith(self._FEISHU_PREFIXES):
            logger.warning("start_thinking 收到飞书格式 channel_id: %s，跳过", channel_id[:20])
            return None
        if not channel_id:
            return None

        async def _typing_loop() -> None:
            try:
                while True:
                    await self._sender.trigger_typing(channel_id)
                    await asyncio.sleep(8.0)
            except asyncio.CancelledError:
                pass

        task = asyncio.create_task(_typing_loop(), name=f"typing-{message_id}")
        self._typing_tasks[message_id] = task
        # 立即触发一次
        await self._sender.trigger_typing(channel_id)
        return message_id

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        task = self._typing_tasks.pop(message_id, None)
        if task:
            task.cancel()

    # ── 感官 ──

    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        """下载附件 URL → base64。resource_key 就是附件 URL。"""
        result = await self._sender.download_attachment(resource_key)
        if result is None:
            return None
        raw_bytes, content_type = result

        # 归一化 MIME 类型
        if "jpeg" in content_type or "jpg" in content_type:
            media_type = "image/jpeg"
        elif "png" in content_type:
            media_type = "image/png"
        elif "gif" in content_type:
            media_type = "image/gif"
        elif "webp" in content_type:
            media_type = "image/webp"
        else:
            media_type = content_type

        b64 = base64.b64encode(raw_bytes).decode("ascii")
        return b64, media_type

    # ── 认知 ──

    async def resolve_name(self, user_id: str) -> str:
        cached = self._name_cache.get(user_id)
        if cached:
            return cached
        # REST API fallback
        info = await self._sender.get_user(user_id)
        name = info.get("global_name") or info.get("username") or user_id[-6:]
        self._name_cache[user_id] = name
        return name

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        """通过 discord.Client 缓存获取 guild 成员。"""
        if not self._client:
            return []
        # chat_id 是 channel_id，需要找到对应的 guild
        try:
            channel = self._client.get_channel(int(chat_id))
        except (ValueError, TypeError):
            logger.warning("无效的 chat_id: %s", chat_id)
            return []
        if not channel or not hasattr(channel, "guild"):
            return []
        guild = channel.guild
        members: list[ChatMember] = []
        for member in guild.members:
            members.append(ChatMember(
                user_id=str(member.id),
                name=member.display_name,
                is_bot=member.bot,
            ))
        return members

    # ── 可选行为 ──

    async def react(self, message_id: str, emoji: str) -> str | None:
        channel_id = self._msg_channel_map.get(message_id, "")
        if not channel_id:
            return None
        ok = await self._sender.add_reaction(channel_id, message_id, emoji)
        return emoji if ok else None

    async def unreact(self, message_id: str, handle: str) -> bool:
        channel_id = self._msg_channel_map.get(message_id, "")
        if not channel_id:
            return False
        return await self._sender.remove_reaction(channel_id, message_id, handle)

    async def edit(self, message_id: str, new_content: OutgoingMessage) -> bool:
        channel_id = self._msg_channel_map.get(message_id, "")
        if not channel_id:
            return False
        return await self._sender.edit_message(channel_id, message_id, new_content.text)

    async def unsend(self, message_id: str) -> bool:
        channel_id = self._msg_channel_map.get(message_id, "")
        if not channel_id:
            return False
        return await self._sender.delete_message(channel_id, message_id)

    # ── 内部：事件转换 ──

    async def _event_converter(self) -> None:
        """从 _raw_queue 读取原始 Discord 事件，转换为标准格式后投入用户队列。"""
        logger.info("Discord 事件转换器启动")
        while not self._shutdown.is_set():
            try:
                data = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                event_type = data.get("type", "")
                if event_type == "message":
                    await self._convert_message(data["message"])
                elif event_type == "reaction":
                    self._convert_reaction(data["payload"])
                elif event_type == "member_join":
                    self._convert_member_join(data["member"])
            except Exception:
                logger.exception("转换 Discord 事件失败: %s", data.get("type", "?"))

        logger.info("Discord 事件转换器已停止")

    async def _convert_message(self, message: discord.Message) -> None:
        """将 discord.Message 转换为标准 IncomingMessage。"""
        if self._queue is None:
            logger.warning("Discord 事件在 connect() 前到达，丢弃")
            return

        # 判断聊天类型
        if isinstance(message.channel, discord.DMChannel):
            chat_type = ChatType.PRIVATE
        else:
            chat_type = ChatType.GROUP

        channel_id = str(message.channel.id)
        sender_id = str(message.author.id)
        msg_id = str(message.id)

        # 记录 msg_id → channel_id
        self._record_msg_channel(msg_id, channel_id)

        # 缓存发送者名字
        sender_name = message.author.display_name or message.author.name
        self._name_cache[sender_id] = sender_name

        # 检测是否 @了 bot（用户提及 + 角色提及）
        is_mention_bot = False
        mentions: list[Mention] = []
        identity = self._identity
        for user in message.mentions:
            is_self = bool(identity and str(user.id) == identity.bot_id)
            if is_self:
                is_mention_bot = True
            mentions.append(Mention(
                user_id=str(user.id),
                name=user.display_name or user.name,
                is_bot_self=is_self,
            ))

        # 角色提及：检查 bot 是否拥有被 @ 的角色
        if not is_mention_bot and identity and self._client and self._client.user:
            for role in message.role_mentions:
                # 检查 bot 是否是该角色的成员
                if hasattr(message, "guild") and message.guild:
                    bot_member = message.guild.get_member(self._client.user.id)
                    if bot_member and role in bot_member.roles:
                        is_mention_bot = True
                        break

        # 将所有 Discord 占位符替换为可读的显示名
        text = message.content
        # 用户提及 <@user_id> → @显示名
        for user in message.mentions:
            display = user.display_name or user.name
            text = text.replace(f"<@{user.id}>", f"@{display}")
            text = text.replace(f"<@!{user.id}>", f"@{display}")  # 旧版昵称格式
        # 角色提及 <@&role_id> → @角色名
        for role in message.role_mentions:
            text = text.replace(f"<@&{role.id}>", f"@{role.name}")
        # 频道提及 <#channel_id> → #频道名
        for channel in message.channel_mentions:
            text = text.replace(f"<#{channel.id}>", f"#{channel.name}")
        text = text.strip()

        # 判断消息类型 + 提取图片 + 读取 txt 附件
        image_keys: list[str] = []
        msg_type = MessageType.TEXT
        txt_parts: list[str] = []
        for attachment in message.attachments:
            content_type = attachment.content_type or ""
            if content_type.startswith("image/"):
                image_keys.append(attachment.url)
                msg_type = MessageType.IMAGE
            elif (
                content_type.startswith("text/plain")
                or (attachment.filename or "").endswith(".txt")
            ):
                # Discord 大段文本自动转成 .txt 附件，下载还原为文本
                result = await self._sender.download_attachment(attachment.url)
                if result:
                    raw_bytes, _ = result
                    try:
                        txt_parts.append(raw_bytes.decode("utf-8"))
                    except UnicodeDecodeError:
                        txt_parts.append(raw_bytes.decode("utf-8", errors="replace"))
        if txt_parts:
            text = "\n".join(filter(None, [text] + txt_parts))

        # reply_to
        reply_to_id = ""
        if message.reference and message.reference.message_id:
            reply_to_id = str(message.reference.message_id)

        # sender_type
        sender_type = SenderType.BOT if message.author.bot else SenderType.USER

        # message_type：纯图片用 IMAGE，有文本时用 TEXT（图片通过 image_keys 传递）
        final_msg_type = msg_type if (not text and image_keys) else MessageType.TEXT

        msg = IncomingMessage(
            message_id=msg_id,
            chat_id=channel_id,
            chat_type=chat_type,
            sender_id=sender_id,
            sender_type=sender_type,
            sender_name=sender_name,
            message_type=final_msg_type,
            text=text,
            mentions=mentions,
            is_mention_bot=is_mention_bot,
            image_keys=image_keys,
            reply_to_id=reply_to_id,
            timestamp=int(message.created_at.timestamp() * 1000),
            raw=message,
        )

        self._queue.put_nowait({"event_type": "message", "message": msg})

    def _convert_reaction(self, payload: discord.RawReactionActionEvent) -> None:
        """将 Discord reaction 事件转换为标准格式。"""
        if self._queue is None:
            return

        channel_id = str(payload.channel_id)
        message_id = str(payload.message_id)
        emoji = str(payload.emoji)

        reaction = Reaction(
            reaction_id="",
            chat_id=channel_id,
            message_id=message_id,
            emoji=emoji,
            operator_id=str(payload.user_id),
            operator_type=SenderType.USER,
        )
        self._queue.put_nowait({"event_type": "reaction", "reaction": reaction})

    def _convert_member_join(self, member: discord.Member) -> None:
        """将成员加入事件转换为标准 member_change。"""
        if self._queue is None:
            return

        # 缓存名字
        self._name_cache[str(member.id)] = member.display_name
        # 使用第一个文字频道的 ID 作为 chat_id
        guild = member.guild
        if not guild.text_channels:
            logger.warning("成员加入 guild %s 但无文字频道，跳过事件", guild.id)
            return
        channel_id = str(guild.text_channels[0].id)

        self._queue.put_nowait({
            "event_type": "member_change",
            "chat_id": channel_id,
            "change_type": "user_joined",
            "users": [{"user_id": str(member.id), "name": member.display_name}],
        })

    # ── 内部：辅助 ──

    def _cancel_typing_for_channel(self, channel_id: str) -> None:
        """取消指定频道的所有 typing task。"""
        to_remove: list[str] = []
        for msg_id, task in self._typing_tasks.items():
            if self._msg_channel_map.get(msg_id) == channel_id:
                task.cancel()
                to_remove.append(msg_id)
        for msg_id in to_remove:
            del self._typing_tasks[msg_id]

    def _record_msg_channel(self, message_id: str, channel_id: str) -> None:
        """记录 message_id → channel_id 映射。"""
        if len(self._msg_channel_map) >= self._msg_channel_map_max:
            oldest = next(iter(self._msg_channel_map))
            del self._msg_channel_map[oldest]
        self._msg_channel_map[message_id] = channel_id

    @staticmethod
    def _convert_card_to_embed(card: dict) -> dict:
        """将标准 card 转为 Discord Embed。"""
        embed: dict[str, Any] = {}

        card_type = card.get("type", "")
        title = card.get("title", "")
        content = card.get("content", "") or card.get("message", "")

        if title:
            embed["title"] = title
        if content:
            embed["description"] = content

        # 颜色映射
        color_map = {
            "blue": 0x3498DB,
            "green": 0x2ECC71,
            "red": 0xE74C3C,
            "yellow": 0xF1C40F,
            "purple": 0x9B59B6,
        }
        color = card.get("color", "blue")
        embed["color"] = color_map.get(color, 0x3498DB)

        # schedule 类型：事件列表放入 fields
        if card_type == "schedule":
            fields = []
            for event in card.get("events", []):
                fields.append({
                    "name": event.get("summary", "事件"),
                    "value": f"{event.get('start_time', '')} - {event.get('end_time', '')}",
                    "inline": False,
                })
            if fields:
                embed["fields"] = fields

        # task_list 类型
        if card_type == "task_list":
            lines = []
            for task in card.get("tasks", []):
                status = "✅" if task.get("done") else "⬜"
                lines.append(f"{status} {task.get('title', '')}")
            if lines:
                embed["description"] = "\n".join(lines)

        # error 类型用红色
        if card_type == "error":
            embed["color"] = 0xE74C3C

        return embed
