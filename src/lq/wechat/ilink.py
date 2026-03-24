"""iLink HTTP API client for WeChat bot platform."""

from __future__ import annotations

import base64
import logging
import random
import uuid
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Message types
MSG_TYPE_USER = 1
MSG_TYPE_BOT = 2

# Message states
MSG_STATE_NEW = 0
MSG_STATE_GENERATING = 1
MSG_STATE_FINISH = 2

# Item types
ITEM_TYPE_TEXT = 1
ITEM_TYPE_IMAGE = 2
ITEM_TYPE_VOICE = 3
ITEM_TYPE_FILE = 4
ITEM_TYPE_VIDEO = 5

# Typing status
TYPING_STATUS_TYPING = 1
TYPING_STATUS_CANCEL = 2

DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
CHANNEL_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class TextItem:
    text: str


@dataclass
class ImageItem:
    url: str


@dataclass
class MessageItem:
    type: int
    text_item: TextItem | None = None
    image_item: ImageItem | None = None


@dataclass
class WeixinMessage:
    from_user_id: str
    to_user_id: str
    message_type: int
    message_state: int
    item_list: list[MessageItem] = field(default_factory=list)
    context_token: str = ""


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


def _generate_uin_header() -> str:
    """Generate X-WECHAT-UIN header value: base64 of a random uint32 string."""
    uin = random.randint(0, 0xFFFFFFFF)
    return base64.b64encode(str(uin).encode()).decode()


class ILinkClient:
    """Async HTTP client wrapping the WeChat iLink Bot API."""

    def __init__(
        self,
        bot_token: str,
        bot_id: str,
        base_url: str = "",
    ) -> None:
        self._bot_token = bot_token
        self._bot_id = bot_id
        self._base_url = base_url or DEFAULT_BASE_URL

        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {bot_token}",
            "X-WECHAT-UIN": _generate_uin_header(),
        }
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
        )

    # -- properties ---------------------------------------------------------

    @property
    def bot_id(self) -> str:
        return self._bot_id

    # -- API methods --------------------------------------------------------

    async def get_updates(self, buf: str = "") -> dict:
        """Long-poll ``/ilink/bot/getupdates`` for new messages.

        The server holds the connection for up to ~30 s.  We use a 40 s client
        timeout so the server can close first under normal conditions.

        Returns the parsed response dict, or an empty-ish fallback on timeout.
        """
        payload = {
            "get_updates_buf": buf,
            "base_info": {"channel_version": CHANNEL_VERSION},
        }
        try:
            resp = await self._client.post(
                "/ilink/bot/getupdates",
                json=payload,
                timeout=40.0,
            )
            resp.raise_for_status()
            data: dict = resp.json()
            errcode = data.get("errcode", 0)
            if errcode == -14:
                logger.warning("iLink session expired (errcode -14)")
            elif errcode != 0:
                logger.warning(
                    "get_updates errcode=%s errmsg=%s",
                    errcode,
                    data.get("errmsg", ""),
                )
            return data
        except httpx.ReadTimeout:
            logger.debug("get_updates long-poll timed out, returning empty")
            return {"ret": 0, "errcode": 0, "errmsg": "", "msgs": [], "get_updates_buf": buf}
        except Exception:
            logger.exception("get_updates request failed")
            return {"ret": -1, "errcode": -1, "errmsg": "request failed", "msgs": [], "get_updates_buf": buf}

    async def send_message(
        self,
        to_user_id: str,
        text: str,
        context_token: str,
    ) -> bool:
        """Send a text message via ``/ilink/bot/sendmessage``."""
        payload = {
            "msg": {
                "from_user_id": self._bot_id,
                "to_user_id": to_user_id,
                "client_id": str(uuid.uuid4()),
                "message_type": MSG_TYPE_BOT,
                "message_state": MSG_STATE_FINISH,
                "item_list": [
                    {"type": ITEM_TYPE_TEXT, "text_item": {"text": text}},
                ],
                "context_token": context_token,
            },
            "base_info": {},
        }
        try:
            resp = await self._client.post("/ilink/bot/sendmessage", json=payload)
            resp.raise_for_status()
            data: dict = resp.json()
            if data.get("ret", -1) != 0:
                logger.warning(
                    "send_message failed: ret=%s errmsg=%s",
                    data.get("ret"),
                    data.get("errmsg", ""),
                )
                return False
            return True
        except Exception:
            logger.exception("send_message request failed")
            return False

    async def get_config(self, user_id: str, context_token: str) -> str:
        """Fetch a typing ticket via ``/ilink/bot/getconfig``.

        Returns the ``typing_ticket`` string, or empty string on failure.
        """
        payload = {
            "ilink_user_id": user_id,
            "context_token": context_token,
            "base_info": {},
        }
        try:
            resp = await self._client.post("/ilink/bot/getconfig", json=payload)
            resp.raise_for_status()
            data: dict = resp.json()
            if data.get("ret", -1) != 0:
                logger.warning(
                    "get_config failed: ret=%s errmsg=%s",
                    data.get("ret"),
                    data.get("errmsg", ""),
                )
                return ""
            return data.get("typing_ticket", "")
        except Exception:
            logger.exception("get_config request failed")
            return ""

    async def send_typing(
        self,
        user_id: str,
        typing_ticket: str,
        status: int = TYPING_STATUS_TYPING,
    ) -> bool:
        """Send typing indicator via ``/ilink/bot/sendtyping``."""
        payload = {
            "ilink_user_id": user_id,
            "typing_ticket": typing_ticket,
            "status": status,
            "base_info": {},
        }
        try:
            resp = await self._client.post("/ilink/bot/sendtyping", json=payload)
            resp.raise_for_status()
            data: dict = resp.json()
            if data.get("ret", -1) != 0:
                logger.warning(
                    "send_typing failed: ret=%s errmsg=%s",
                    data.get("ret"),
                    data.get("errmsg", ""),
                )
                return False
            return True
        except Exception:
            logger.exception("send_typing request failed")
            return False

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
