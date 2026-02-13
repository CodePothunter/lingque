"""飞书消息发送"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import httpx
import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
)

logger = logging.getLogger(__name__)


def _build_markdown_card(text: str) -> dict:
    """将包含复杂 Markdown 的文本构建为飞书卡片（卡片支持 Markdown 渲染）。"""
    return {
        "config": {"wide_screen_mode": True},
        "elements": [
            {
                "tag": "markdown",
                "content": text,
            }
        ],
    }


def _has_complex_markdown(text: str) -> bool:
    """检测文本是否包含复杂 Markdown（代码块等），适合用卡片发送。"""
    return bool(re.search(r"```", text))


def _strip_markdown(text: str) -> str:
    """清理常见 Markdown 标记，飞书文本消息不支持 Markdown 渲染。"""
    # 代码块 ```lang\ncode\n``` → 保留代码内容
    text = re.sub(r"```\w*\n?(.*?)```", r"\1", text, flags=re.DOTALL)
    # 粗体 **text** 或 __text__
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text)
    # 斜体 *text*（bold ** 已先去除，剩余单 * 对皆为斜体）
    text = re.sub(r"\*(.+?)\*", r"\1", text)
    # 斜体 _text_（仅 ASCII 字母数字边界保护，避免误伤 a_b 变量名，中文相邻正常去除）
    text = re.sub(r"(?<![a-zA-Z0-9])_(.+?)_(?![a-zA-Z0-9])", r"\1", text)
    # 标题 # / ## / ### ...
    text = re.sub(r"^#{1,6}\s+", "", text, flags=re.MULTILINE)
    # 行内代码 `code`
    text = re.sub(r"`(.+?)`", r"\1", text)
    # 无序列表 - item / * item → item（保留缩进层级用空格）
    text = re.sub(r"^(\s*)[-*]\s+", r"\1", text, flags=re.MULTILINE)
    # 有序列表 1. item → item
    text = re.sub(r"^(\s*)\d+\.\s+", r"\1", text, flags=re.MULTILINE)
    return text


class FeishuSender:
    def __init__(self, app_id: str, app_secret: str) -> None:
        self.client = (
            lark.Client.builder()
            .app_id(app_id)
            .app_secret(app_secret)
            .build()
        )
        self._app_id = app_id
        self._app_secret = app_secret
        self._tenant_access_token: str | None = None
        self._token_expires_at: float = 0.0  # Unix timestamp
        self._user_name_cache: dict[str, str] = {}  # open_id → 名字

    async def send_text(self, chat_id: str, text: str) -> str | None:
        """发送文本消息到指定会话，返回 message_id。
        当文本包含代码块等复杂 Markdown 时自动切换为卡片消息。
        """
        if _has_complex_markdown(text):
            card = _build_markdown_card(text)
            return await self.send_card(chat_id, card)
        text = _strip_markdown(text)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if resp.code != 0:
            logger.error("发送消息失败: code=%d msg=%s", resp.code, resp.msg)
            return None
        msg_id = resp.data.message_id
        logger.debug("消息已发送: %s -> %s", chat_id, msg_id)
        return msg_id

    async def reply_text(self, message_id: str, text: str) -> str | None:
        """引用回复文本消息。
        当文本包含代码块等复杂 Markdown 时自动切换为卡片回复。
        """
        if _has_complex_markdown(text):
            card = _build_markdown_card(text)
            return await self.reply_card(message_id, card)
        text = _strip_markdown(text)
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("text")
                .content(json.dumps({"text": text}))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.areply(req)
        if resp.code != 0:
            logger.error("回复消息失败: code=%d msg=%s", resp.code, resp.msg)
            return None
        msg_id = resp.data.message_id
        logger.debug("已回复: %s -> %s", message_id, msg_id)
        return msg_id

    async def send_card(self, chat_id: str, card_json: dict) -> str | None:
        """发送卡片消息"""
        req = (
            CreateMessageRequest.builder()
            .receive_id_type("chat_id")
            .request_body(
                CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type("interactive")
                .content(json.dumps(card_json))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.acreate(req)
        if resp.code != 0:
            logger.error("发送卡片失败: code=%d msg=%s", resp.code, resp.msg)
            return None
        return resp.data.message_id

    async def reply_card(self, message_id: str, card_json: dict) -> str | None:
        """用卡片引用回复"""
        req = (
            ReplyMessageRequest.builder()
            .message_id(message_id)
            .request_body(
                ReplyMessageRequestBody.builder()
                .msg_type("interactive")
                .content(json.dumps(card_json))
                .build()
            )
            .build()
        )
        resp = await self.client.im.v1.message.areply(req)
        if resp.code != 0:
            logger.error("回复卡片失败: code=%d msg=%s", resp.code, resp.msg)
            return None
        return resp.data.message_id

    async def fetch_bot_info(self) -> dict[str, Any]:
        """调用 GET /bot/v3/info 获取机器人信息（含 bot_open_id）"""
        token = await self._get_tenant_token()
        async with httpx.AsyncClient() as http:
            resp = await http.get(
                "https://open.feishu.cn/open-apis/bot/v3/info",
                headers={"Authorization": f"Bearer {token}"},
            )
            resp.raise_for_status()
            data = resp.json()
        bot = data.get("bot", {})
        logger.info(
            "机器人信息: name=%s open_id=%s",
            bot.get("bot_name"),
            bot.get("open_id"),
        )
        return bot

    async def get_user_name(self, open_id: str) -> str:
        """获取用户名，带内存缓存。"""
        cached = self._user_name_cache.get(open_id)
        if cached is not None:
            return cached

        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}",
                    params={"user_id_type": "open_id"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
            name = data.get("data", {}).get("user", {}).get("name", "")
            if name:
                self._user_name_cache[open_id] = name
                return name
        except Exception:
            logger.warning("获取用户名失败: %s", open_id)

        # 回退：用 open_id 尾部
        fallback = open_id[-6:]
        self._user_name_cache[open_id] = fallback
        return fallback

    async def _get_tenant_token(self) -> str:
        """获取 tenant_access_token，过期前自动刷新"""
        import time as _time
        now = _time.time()
        if self._tenant_access_token and now < self._token_expires_at:
            return self._tenant_access_token
        async with httpx.AsyncClient() as http:
            resp = await http.post(
                "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                json={
                    "app_id": self._app_id,
                    "app_secret": self._app_secret,
                },
            )
            resp.raise_for_status()
            data = resp.json()
        self._tenant_access_token = data["tenant_access_token"]
        # 飞书 token 有效期 2 小时，提前 5 分钟刷新
        expire_seconds = data.get("expire", 7200)
        self._token_expires_at = now + expire_seconds - 300
        logger.debug("tenant_access_token 已刷新，有效至 %ds 后", expire_seconds - 300)
        return self._tenant_access_token
