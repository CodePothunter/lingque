"""微信平台适配器 — 基于 iLink API 实现。"""

from __future__ import annotations

import asyncio
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
from lq.wechat.auth import ensure_credentials, load_credentials, save_credentials
from lq.wechat.ilink import (
    ITEM_TYPE_IMAGE,
    ITEM_TYPE_TEXT,
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
    - 长轮询自动重连（指数退避）
    """

    def __init__(self, home: Path) -> None:
        self._home = home
        self._client: ILinkClient | None = None
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
        """发送文本消息。"""
        if not self._client:
            return None

        # Extract user_id from chat_id (format: user_id@im.wechat)
        user_id = message.chat_id
        if user_id.endswith("@im.wechat"):
            user_id = user_id[: -len("@im.wechat")]

        text = message.text
        if message.card:
            text = self._convert_card_to_text(message.card)

        context_token = self._context_tokens.get(user_id, "")
        success = await self._client.send_message(user_id, text, context_token)

        if success:
            self._msg_counter += 1
            return f"wechat_{self._msg_counter}"
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
        """暂不支持媒体获取。"""
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

                # Extract text and images
                text_parts: list[str] = []
                image_keys: list[str] = []
                for item in msg.get("item_list", []):
                    item_type = item.get("type", 0)
                    if item_type == ITEM_TYPE_TEXT:
                        text_item = item.get("text_item")
                        if text_item and text_item.get("text"):
                            text_parts.append(text_item["text"])
                    elif item_type == ITEM_TYPE_IMAGE:
                        image_item = item.get("image_item")
                        if image_item and image_item.get("url"):
                            image_keys.append(image_item["url"])

                text = "\n".join(text_parts)
                if not text and not image_keys:
                    continue

                # Generate message ID
                self._msg_counter += 1
                msg_id = f"wechat_{self._msg_counter}"

                # Use user_id@im.wechat as chat_id
                chat_id = f"{from_user}@im.wechat"

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
                    message_type=MessageType.IMAGE if image_keys else MessageType.TEXT,
                    text=text.strip(),
                    image_keys=image_keys,
                    is_mention_bot=True,  # private chat = always mentioned
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
