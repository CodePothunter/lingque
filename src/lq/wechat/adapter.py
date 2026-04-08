"""微信平台适配器 — 基于 iLink API 实现。"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path

from lq.platform.adapter import PlatformAdapter
from lq.platform.types import (
    BotIdentity,
    ChatMember,
    ChatType,
    IncomingMessage,
    MessageType,
    OutgoingMessage,
    SenderType,
)
from lq.wechat.auth import ensure_credentials
from lq.wechat.ilink import (
    ITEM_TYPE_IMAGE,
    ITEM_TYPE_TEXT,
    ITEM_TYPE_VOICE,
    MSG_STATE_FINISH,
    MSG_TYPE_USER,
    TYPING_STATUS_CANCEL,
    TYPING_STATUS_TYPING,
    ILinkClient,
)

logger = logging.getLogger(__name__)


class WechatAdapter(PlatformAdapter):
    """微信平台适配器。

    基于 iLink API 实现，通过长轮询接收消息。
    特性：
    - 首次启动自动 QR 码登录
    - 凭证持久化，重启免登
    - "正在输入..." 状态支持
    - 图片收发（CDN AES-128-ECB 加解密）
    - 长轮询自动重连（指数退避）
    """

    def __init__(self, home: Path) -> None:
        self._home = home
        self._client: ILinkClient | None = None
        self._bot_token = ""
        self._base_url = ""
        self._queue: asyncio.Queue | None = None
        self._raw_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()
        self._identity: BotIdentity | None = None

        # user_id -> context_token (needed for replies and typing)
        self._context_tokens: dict[str, str] = {}
        self._context_tokens_max = 500

        # message counter (iLink doesn't return message IDs)
        self._msg_counter = 0

        # msg_id -> user_id mapping (for start_thinking/stop_thinking)
        self._msg_user_map: dict[str, str] = {}
        self._msg_user_map_max = 500

        # user name cache
        self._name_cache: dict[str, str] = {}

        # sync buf persistence path
        self._sync_path = home / "wechat_sync.json"

    # ------------------------------------------------------------------
    # PlatformAdapter interface
    # ------------------------------------------------------------------

    async def get_identity(self) -> BotIdentity:
        """获取身份。首次调用时触发 QR 登录。"""
        if self._identity:
            return self._identity

        creds = await ensure_credentials(self._home)
        self._bot_token = creds.bot_token
        self._base_url = creds.base_url
        self._client = ILinkClient(creds.bot_token, creds.bot_id, creds.base_url)
        self._identity = BotIdentity(bot_id=creds.bot_id, bot_name="WeChat Bot")
        return self._identity

    async def connect(self, queue: asyncio.Queue) -> None:
        """启动长轮询。"""
        self._queue = queue
        self._tasks.append(
            asyncio.create_task(self._event_converter(), name="wechat-converter")
        )
        self._tasks.append(
            asyncio.create_task(self._poll_updates(), name="wechat-poll")
        )
        logger.info("微信适配器已启动")

    async def disconnect(self) -> None:
        """停止，释放资源。"""
        self._shutdown.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3.0)
        self._tasks.clear()
        if self._client:
            await self._client.close()
        logger.info("微信适配器已停止")

    async def send(self, message: OutgoingMessage) -> str | None:
        """发送消息（文本或图片）。"""
        if not self._client:
            return None

        # iLink 的 user_id 已经包含 @im.wechat 后缀，直接使用
        user_id = message.chat_id

        context_token = self._context_tokens.get(user_id, "")

        # 发送图片
        if message.image_path:
            return await self._send_image(user_id, message.text, message.image_path, context_token)

        # 发送文本
        text = message.text
        if message.card:
            text = self._convert_card_to_text(message.card)

        success = await self._client.send_message(user_id, text, context_token)
        if success:
            self._msg_counter += 1
            return f"wechat_{self._msg_counter}"
        return None

    async def _send_image(
        self, user_id: str, caption: str, image_path: str, context_token: str,
    ) -> str | None:
        """加密上传图片到 CDN 并发送图片消息。"""
        try:
            from lq.wechat.cdn import upload_image

            file_data = Path(image_path).read_bytes()
            uploaded = await upload_image(
                file_data=file_data,
                to_user_id=user_id,
                bot_token=self._bot_token,
                base_url=self._base_url,
            )

            # 构造图片消息 item
            image_item = {
                "type": ITEM_TYPE_IMAGE,
                "image_item": {
                    "media": {
                        "encrypt_query_param": uploaded["encrypt_query_param"],
                        "aes_key": uploaded["aes_key_b64"],
                        "encrypt_type": 1,
                    },
                    "mid_size": uploaded["ciphertext_size"],
                },
            }

            # 如果有文字说明，先发文字再发图片
            items = []
            if caption:
                items.append({"type": ITEM_TYPE_TEXT, "text_item": {"text": caption}})
            items.append(image_item)

            # 每个 item 单独发送（与官方实现一致）
            import uuid
            for item in items:
                payload = {
                    "msg": {
                        "from_user_id": "",
                        "to_user_id": user_id,
                        "client_id": str(uuid.uuid4()),
                        "message_type": 2,  # BOT
                        "message_state": 2,  # FINISH
                        "item_list": [item],
                        "context_token": context_token,
                    },
                    "base_info": {},
                }
                await self._client._client.post(
                    "/ilink/bot/sendmessage", json=payload,
                )

            self._msg_counter += 1
            logger.info("微信图片发送成功: %s", image_path)
            return f"wechat_{self._msg_counter}"
        except Exception:
            logger.exception("微信图片发送失败: %s", image_path)
            return None

    async def start_thinking(self, message_id: str) -> str | None:
        """发送 "正在输入..." 状态。"""
        if not self._client:
            return None

        user_id = self._msg_user_map.get(message_id)
        if not user_id:
            return None

        context_token = self._context_tokens.get(user_id, "")
        if not context_token:
            return None

        try:
            typing_ticket = await self._client.get_config(user_id, context_token)
            if typing_ticket:
                await self._client.send_typing(
                    user_id, typing_ticket, TYPING_STATUS_TYPING
                )
                return f"{user_id}:{typing_ticket}"  # handle = "user_id:ticket"
        except Exception:
            logger.debug("发送 typing 状态失败", exc_info=True)
        return None

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        """取消 "正在输入..." 状态。"""
        if not self._client or not handle:
            return

        try:
            parts = handle.split(":", 1)
            if len(parts) == 2:
                user_id, typing_ticket = parts
                await self._client.send_typing(
                    user_id, typing_ticket, TYPING_STATUS_CANCEL
                )
        except Exception:
            logger.debug("取消 typing 状态失败", exc_info=True)

    async def fetch_media(
        self, message_id: str, resource_key: str
    ) -> tuple[str, str] | None:
        """下载并解密微信 CDN 图片。

        resource_key 是 JSON 字符串: {"encrypt_query_param": "...", "aes_key": "..."}
        返回 (base64_data, mime_type)。
        """
        try:
            from lq.wechat.cdn import download_and_decrypt

            ref = json.loads(resource_key)
            encrypt_query_param = ref.get("encrypt_query_param", "")
            aes_key = ref.get("aes_key", "")
            if not encrypt_query_param or not aes_key:
                logger.warning("fetch_media: 缺少 CDN 参数")
                return None

            decrypted = await download_and_decrypt(encrypt_query_param, aes_key)
            b64 = base64.b64encode(decrypted).decode()

            # 简单检测 MIME 类型
            mime = "image/jpeg"
            if decrypted[:8] == b"\x89PNG\r\n\x1a\n":
                mime = "image/png"
            elif decrypted[:4] == b"GIF8":
                mime = "image/gif"
            elif decrypted[:4] == b"RIFF" and decrypted[8:12] == b"WEBP":
                mime = "image/webp"

            return b64, mime
        except Exception:
            logger.exception("fetch_media 失败: %s", resource_key[:60])
            return None

    async def resolve_name(self, user_id: str) -> str:
        """解析用户名。"""
        cached = self._name_cache.get(user_id)
        if cached:
            return cached
        return user_id[-8:] if len(user_id) > 8 else user_id

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        """列出聊天成员（微信暂不支持）。"""
        return []

    # ------------------------------------------------------------------
    # Internal: long-polling
    # ------------------------------------------------------------------

    async def _poll_updates(self) -> None:
        """长轮询获取微信消息。"""
        buf = self._load_sync_buf()
        backoff = 3.0
        max_backoff = 60.0
        session_expired_count = 0

        while not self._shutdown.is_set():
            try:
                resp = await self._client.get_updates(buf)

                # Session expired
                if resp.get("errcode") == -14:
                    session_expired_count += 1
                    if session_expired_count > 5:
                        logger.error("微信 session 连续过期 %d 次，需要重新登录", session_expired_count)
                        # 删除缓存凭证，下次启动时触发重新扫码登录
                        creds_path = self._home / "wechat_credentials.json"
                        if creds_path.exists():
                            creds_path.unlink()
                            logger.info("已删除缓存凭证，下次启动将重新扫码")
                        break
                    logger.warning("微信 session 过期，重置 sync buf (%d/5)", session_expired_count)
                    buf = ""
                    self._save_sync_buf(buf)
                    await asyncio.sleep(5.0)
                    continue

                # Other server errors — backoff before retry
                if resp.get("ret", 0) != 0 and resp.get("errcode", 0) != 0:
                    logger.warning(
                        "微信服务端错误: ret=%s errcode=%s",
                        resp.get("ret"),
                        resp.get("errcode"),
                    )
                    await asyncio.sleep(backoff)
                    backoff = min(backoff * 2, max_backoff)
                    continue

                # Reset backoff and session expired counter on success
                backoff = 3.0
                session_expired_count = 0

                # Update cursor
                new_buf = resp.get("get_updates_buf", "")
                if new_buf:
                    buf = new_buf
                    self._save_sync_buf(buf)

                # Enqueue messages
                for msg in resp.get("msgs", []):
                    await self._raw_queue.put(msg)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("微信长轮询异常")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)

    # ------------------------------------------------------------------
    # Internal: event conversion
    # ------------------------------------------------------------------

    async def _event_converter(self) -> None:
        """将 iLink 原始消息转换为标准事件。"""
        while not self._shutdown.is_set():
            try:
                msg = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                # Only process finished user messages
                if msg.get("message_type") != MSG_TYPE_USER:
                    continue
                if msg.get("message_state") != MSG_STATE_FINISH:
                    continue

                from_user = msg.get("from_user_id", "")
                if not from_user:
                    continue

                # Store context token (with eviction)
                context_token = msg.get("context_token", "")
                if context_token:
                    self._context_tokens[from_user] = context_token
                    while len(self._context_tokens) > self._context_tokens_max:
                        oldest = next(iter(self._context_tokens))
                        del self._context_tokens[oldest]

                # Extract text, images, and voice
                text_parts: list[str] = []
                image_keys: list[str] = []
                audio_keys: list[str] = []
                for item in msg.get("item_list", []):
                    item_type = item.get("type", 0)
                    if item_type == ITEM_TYPE_TEXT:
                        text_item = item.get("text_item")
                        if text_item and text_item.get("text"):
                            text_parts.append(text_item["text"])
                    elif item_type == ITEM_TYPE_IMAGE:
                        image_item = item.get("image_item")
                        if image_item:
                            cdn_ref = self._extract_cdn_ref(image_item)
                            if cdn_ref:
                                image_keys.append(cdn_ref)
                    elif item_type == ITEM_TYPE_VOICE:
                        voice_item = item.get("voice_item")
                        if voice_item:
                            cdn_ref = self._extract_cdn_ref(voice_item)
                            if cdn_ref:
                                audio_keys.append(cdn_ref)

                text = "\n".join(text_parts)
                if not text and not image_keys and not audio_keys:
                    continue

                # Generate message ID
                self._msg_counter += 1
                msg_id = f"wechat_{self._msg_counter}"

                # from_user_id 已包含 @im.wechat 后缀，直接用作 chat_id
                chat_id = from_user

                # Record mapping for typing
                self._msg_user_map[msg_id] = from_user
                # Trim map
                while len(self._msg_user_map) > self._msg_user_map_max:
                    oldest = next(iter(self._msg_user_map))
                    del self._msg_user_map[oldest]

                incoming = IncomingMessage(
                    message_id=msg_id,
                    chat_id=chat_id,
                    chat_type=ChatType.PRIVATE,  # iLink is always 1v1
                    sender_id=from_user,
                    sender_type=SenderType.USER,
                    sender_name=self._name_cache.get(
                        from_user,
                        from_user[-8:] if len(from_user) > 8 else from_user,
                    ),
                    message_type=(
                        MessageType.AUDIO if audio_keys
                        else MessageType.IMAGE if image_keys
                        else MessageType.TEXT
                    ),
                    text=text.strip(),
                    image_keys=image_keys,
                    audio_keys=audio_keys,
                    is_mention_bot=True,  # private chat = always mentioned
                    platform="wechat",
                    raw=msg,
                )

                self._queue.put_nowait(
                    {"event_type": "message", "message": incoming}
                )

            except Exception:
                logger.exception("转换微信消息失败")

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_cdn_ref(image_item: dict) -> str:
        """从 ImageItem 提取 CDN 引用信息，序列化为 JSON 字符串。

        用于 image_keys，后续 fetch_media 时解析下载。
        """
        media = image_item.get("media", {})
        encrypt_query_param = media.get("encrypt_query_param", "")
        aes_key = media.get("aes_key", "") or image_item.get("aeskey", "")

        if not encrypt_query_param or not aes_key:
            # fallback: 尝试直接 URL
            url = image_item.get("url", "")
            if url:
                return json.dumps({"url": url})
            return ""

        return json.dumps({
            "encrypt_query_param": encrypt_query_param,
            "aes_key": aes_key,
        })

    def _load_sync_buf(self) -> str:
        """Load sync buf from disk."""
        try:
            if self._sync_path.exists():
                data = json.loads(self._sync_path.read_text(encoding="utf-8"))
                return data.get("get_updates_buf", "")
        except Exception:
            pass
        return ""

    def _save_sync_buf(self, buf: str) -> None:
        """Save sync buf to disk."""
        try:
            self._sync_path.write_text(
                json.dumps({"get_updates_buf": buf}),
                encoding="utf-8",
            )
        except Exception:
            logger.debug("保存 sync buf 失败", exc_info=True)

    @staticmethod
    def _convert_card_to_text(card: dict) -> str:
        """Convert standard card to plain text (same pattern as TelegramAdapter)."""
        parts: list[str] = []
        title = card.get("title", "")
        content = card.get("content", "")
        if title:
            parts.append(f"【{title}】")
        if content:
            parts.append(content)
        fields = card.get("fields", [])
        for f in fields:
            key = f.get("key", "")
            value = f.get("value", "")
            if key and value:
                parts.append(f"• {key}: {value}")
        return "\n".join(parts) if parts else str(card)
