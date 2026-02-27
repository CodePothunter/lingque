"""Discord REST API 封装（httpx，与 feishu/sender.py 同模式）"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote as url_quote

import httpx

logger = logging.getLogger(__name__)

BASE_URL = "https://discord.com/api/v10"


class DiscordSender:
    """Discord REST API 封装，使用 httpx 直接调用。

    包含 429 rate-limit 自动重试（读 Retry-After header + JSON body）。
    """

    def __init__(self, bot_token: str) -> None:
        self._token = bot_token
        self._headers = {
            "Authorization": f"Bot {bot_token}",
            "Content-Type": "application/json",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        max_retries: int = 3,
    ) -> dict | list | bytes | None:
        """通用请求方法，包含 429 rate-limit 自动重试。"""
        url = f"{BASE_URL}{path}"
        resp: httpx.Response | None = None
        for attempt in range(max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=30.0) as http:
                    resp = await http.request(
                        method, url, headers=self._headers, json=json,
                    )
                if resp.status_code == 429:
                    # 优先读 JSON body 中的 retry_after（更精确）
                    try:
                        body = resp.json()
                        retry_after = float(body.get("retry_after", 1))
                    except Exception:
                        retry_after = float(resp.headers.get("Retry-After", "1"))
                    logger.warning(
                        "Discord rate-limit，等待 %.1f 秒后重试 (%d/%d)",
                        retry_after, attempt + 1, max_retries,
                    )
                    await asyncio.sleep(retry_after)
                    continue
                resp.raise_for_status()
                if resp.status_code == 204:
                    return None
                content_type = resp.headers.get("content-type", "")
                if "application/json" in content_type:
                    return resp.json()
                return resp.content
            except httpx.HTTPStatusError:
                raise
            except (httpx.ConnectError, httpx.TimeoutException,
                    httpx.ReadError, httpx.WriteError, httpx.PoolTimeout):
                if attempt < max_retries:
                    logger.warning(
                        "Discord API 网络错误，重试 (%d/%d)",
                        attempt + 1, max_retries, exc_info=True,
                    )
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    raise
        # rate-limit 重试耗尽
        logger.error("Discord API 请求在 %d 次重试后仍被 rate-limit: %s %s", max_retries, method, path)
        raise httpx.HTTPStatusError(
            f"Rate limited after {max_retries} retries: {method} {path}",
            request=httpx.Request(method, url),
            response=resp,  # type: ignore[arg-type]
        )

    # ── 消息 ──

    async def send_message(
        self,
        channel_id: str,
        content: str,
        *,
        reply_to: str = "",
        embed: dict | None = None,
    ) -> str | None:
        """发送消息，返回 message_id。"""
        body: dict[str, Any] = {}
        if content:
            body["content"] = content
        if reply_to:
            body["message_reference"] = {"message_id": reply_to}
        if embed:
            body["embeds"] = [embed]
        data = await self._request("POST", f"/channels/{channel_id}/messages", json=body)
        if isinstance(data, dict):
            msg_id = data.get("id", "")
            logger.debug("Discord 消息已发送: channel=%s msg_id=%s", channel_id, msg_id)
            return msg_id
        return None

    async def send_message_with_file(
        self,
        channel_id: str,
        file_path: str,
        content: str = "",
        *,
        reply_to: str = "",
    ) -> str | None:
        """发送带文件附件的消息（multipart/form-data），返回 message_id。"""
        import os
        url = f"{BASE_URL}/channels/{channel_id}/messages"
        filename = os.path.basename(file_path)

        payload: dict[str, Any] = {}
        if content:
            payload["content"] = content
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}

        try:
            async with httpx.AsyncClient(timeout=60.0) as http:
                with open(file_path, "rb") as f:
                    files = {"file": (filename, f)}
                    # multipart 时不能用 Content-Type: application/json
                    headers = {"Authorization": f"Bot {self._token}"}
                    resp = await http.post(
                        url, headers=headers, data=payload, files=files,
                    )
                resp.raise_for_status()
                data = resp.json()
                msg_id = data.get("id", "")
                logger.debug("Discord 文件消息已发送: channel=%s msg_id=%s", channel_id, msg_id)
                return msg_id
        except Exception:
            logger.exception("Discord 发送文件失败: channel=%s file=%s", channel_id, file_path)
            return None

    async def edit_message(
        self, channel_id: str, message_id: str, content: str,
    ) -> bool:
        """编辑消息。"""
        try:
            await self._request(
                "PATCH",
                f"/channels/{channel_id}/messages/{message_id}",
                json={"content": content},
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "编辑 Discord 消息失败: channel=%s msg=%s status=%d",
                channel_id, message_id, exc.response.status_code,
            )
            return False
        except Exception:
            logger.exception("编辑 Discord 消息时发生意外错误: channel=%s msg=%s", channel_id, message_id)
            return False

    async def delete_message(
        self, channel_id: str, message_id: str,
    ) -> bool:
        """删除消息。"""
        try:
            await self._request(
                "DELETE",
                f"/channels/{channel_id}/messages/{message_id}",
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "删除 Discord 消息失败: channel=%s msg=%s status=%d",
                channel_id, message_id, exc.response.status_code,
            )
            return False
        except Exception:
            logger.exception("删除 Discord 消息时发生意外错误: channel=%s msg=%s", channel_id, message_id)
            return False

    # ── Reactions ──

    async def add_reaction(
        self, channel_id: str, message_id: str, emoji: str,
    ) -> bool:
        """添加 reaction。emoji 可以是 Unicode emoji 或 name:id 格式。"""
        encoded = url_quote(emoji)
        try:
            await self._request(
                "PUT",
                f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "添加 Discord reaction 失败: channel=%s msg=%s status=%d",
                channel_id, message_id, exc.response.status_code,
            )
            return False
        except Exception:
            logger.exception("添加 Discord reaction 时发生意外错误")
            return False

    async def remove_reaction(
        self, channel_id: str, message_id: str, emoji: str,
    ) -> bool:
        """移除自己的 reaction。"""
        encoded = url_quote(emoji)
        try:
            await self._request(
                "DELETE",
                f"/channels/{channel_id}/messages/{message_id}/reactions/{encoded}/@me",
            )
            return True
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "移除 Discord reaction 失败: channel=%s msg=%s status=%d",
                channel_id, message_id, exc.response.status_code,
            )
            return False
        except Exception:
            logger.exception("移除 Discord reaction 时发生意外错误")
            return False

    # ── Typing ──

    async def trigger_typing(self, channel_id: str) -> None:
        """触发 typing indicator（持续约 10 秒）。"""
        try:
            await self._request("POST", f"/channels/{channel_id}/typing", json={})
        except Exception:
            logger.debug("触发 typing 失败", exc_info=True)

    # ── 用户信息 ──

    async def fetch_bot_info(self) -> dict:
        """GET /users/@me — 获取 bot 自身信息。

        Raises RuntimeError if the response is invalid (e.g. bad token).
        """
        data = await self._request("GET", "/users/@me")
        if not isinstance(data, dict) or "id" not in data:
            raise RuntimeError(
                "Discord fetch_bot_info 返回无效数据 — 请检查 bot_token 和网络连接"
            )
        return data

    async def get_user(self, user_id: str) -> dict:
        """GET /users/{id} — 获取用户信息。"""
        try:
            data = await self._request("GET", f"/users/{user_id}")
            return data if isinstance(data, dict) else {}
        except Exception:
            logger.warning("获取 Discord 用户信息失败: %s", user_id, exc_info=True)
            return {}

    # ── Guild 成员 ──

    async def get_guild_members(
        self, guild_id: str, limit: int = 100,
    ) -> list[dict]:
        """GET /guilds/{id}/members — 获取服务器成员列表。"""
        try:
            data = await self._request(
                "GET",
                f"/guilds/{guild_id}/members?limit={limit}",
            )
            return data if isinstance(data, list) else []
        except Exception:
            logger.warning("获取 Discord 服务器成员失败: %s", guild_id, exc_info=True)
            return []

    # ── 附件下载 ──

    async def download_attachment(self, url: str) -> tuple[bytes, str] | None:
        """下载附件 URL，返回 (raw_bytes, content_type) 或 None。"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                resp = await http.get(url)
                resp.raise_for_status()
            content_type = resp.headers.get("content-type", "application/octet-stream")
            return resp.content, content_type
        except Exception:
            logger.warning("下载 Discord 附件失败: %s", url, exc_info=True)
            return None
