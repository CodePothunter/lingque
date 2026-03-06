"""Telegram Bot API REST 封装

封装所有 Telegram Bot API 调用，支持自动重试和 MarkdownV2 转义。
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Telegram Bot API 基础 URL
TELEGRAM_API_BASE = "https://api.telegram.org/bot{token}/{method}"

# MarkdownV2 需要转义的字符
_MARKDOWNV2_SPECIAL_CHARS = r"_*[]()~`>#+-=|{}.!"

# Telegram 消息长度限制
MAX_MESSAGE_LENGTH = 4096


def _escape_markdown(text: str) -> str:
    """转义 MarkdownV2 特殊字符。

    仅在需要保留格式的地方转义，避免过度转义导致可读性下降。
    """
    result = []
    for char in text:
        if char in _MARKDOWNV2_SPECIAL_CHARS:
            result.append(f"\\{char}")
        else:
            result.append(char)
    return "".join(result)


def _escape_markdown_entities(text: str, entities: list[dict]) -> str:
    """根据 Telegram entities 高精度转义 MarkdownV2。

    策略：只转义不在任何 entity 范围内的特殊字符。
    """
    if not entities:
        return _escape_markdown(text)

    # 构建字符级别的"是否需要转义"标记
    n = len(text)
    should_escape = [True] * n

    for entity in entities:
        offset = entity.get("offset", 0)
        length = entity.get("length", 0)
        entity_type = entity.get("type", "")

        # 对于预格式化实体（code, pre, text_link），不转义内部内容
        if entity_type in ("code", "pre", "text_link"):
            for i in range(offset, min(offset + length, n)):
                should_escape[i] = False

    # 逐字符处理
    result = []
    for i, char in enumerate(text):
        if should_escape[i] and char in _MARKDOWNV2_SPECIAL_CHARS:
            result.append(f"\\{char}")
        else:
            result.append(char)

    return "".join(result)


class TelegramSender:
    """Telegram Bot API REST 客户端。

    特性：
    - 自动重试（429 Flood Control）
    - MarkdownV2 转义处理
    - 长消息自动分段
    - 代理支持（HTTP/SOCKS）
    """

    def __init__(self, bot_token: str, proxy: str = "") -> None:
        self._bot_token = bot_token
        self._proxy = proxy
        self._base_url = TELEGRAM_API_BASE.format(token=bot_token, method="")
        self._http: httpx.AsyncClient | None = None
        self._bot_id: str = ""
        self._bot_name: str = ""

    async def __aenter__(self) -> TelegramSender:
        # 超时设置为 40 秒，大于 getUpdates 的 30 秒长轮询超时
        self._http = httpx.AsyncClient(proxy=self._proxy or None, timeout=40.0)
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._http:
            await self._http.aclose()

    async def _request(
        self,
        method: str,
        data: dict | None = None,
        params: dict | None = None,
        files: dict | None = None,
        max_retries: int = 3,
        _allow_400: bool = False,
    ) -> dict | None:
        """发送 POST 请求到 Telegram Bot API，带自动重试。

        Args:
            max_retries: 最大重试次数（不含首次请求）
                        0 = 只尝试一次，不重试
                        3 = 首次 + 最多重试 3 次（共 4 次）
        """
        if not self._http:
            self._http = httpx.AsyncClient(proxy=self._proxy or None, timeout=40.0)

        url = f"{self._base_url}{method}"
        attempt = 0  # 尝试次数（从 1 开始）
        delay = 1.0

        while attempt <= max_retries:
            try:
                resp = await self._http.post(url, json=data, params=params, files=files)
                resp.raise_for_status()
                result = resp.json()

                if not result.get("ok"):
                    error_code = result.get("error_code", 0)
                    description = result.get("description", "")

                    # 429 Flood Control - 自动重试
                    if error_code == 429:
                        retry_after = result.get("parameters", {}).get("retry_after", delay)
                        logger.warning(
                            "触发 Flood Control，等待 %d 秒后重试: %s",
                            retry_after,
                            method,
                        )
                        await asyncio.sleep(retry_after)
                        attempt += 1
                        delay = retry_after
                        continue

                    logger.error(
                        "Telegram API 错误: code=%d desc=%s method=%s",
                        error_code,
                        description,
                        method,
                    )
                    return None

                return result.get("result")

            except httpx.TimeoutException:
                # getUpdates 的超时是正常的长轮询行为，使用 DEBUG 级别
                if method == "getUpdates":
                    logger.debug("长轮询超时（无新消息）: %s", method)
                    # 长轮询超时不计入重试，直接返回空列表
                    return []
                # 其他方法超时时才重试
                logger.warning("请求超时: %s", method)
                attempt += 1
                # 如果还有重试机会，等待后继续；否则直接让循环条件判断退出
                if attempt <= max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                continue

            except httpx.HTTPStatusError as e:
                # 尝试读取错误响应内容
                error_detail = ""
                try:
                    error_json = e.response.json()
                    error_detail = f" desc={error_json.get('description', '')}"
                except Exception:
                    pass

                status = e.response.status_code

                # 对于允许 400 的方法（如 reaction），使用 DEBUG 级别
                # 因为部分私聊不支持 reactions 是正常现象
                if _allow_400 and status == 400:
                    logger.debug("HTTP 400（预期内）: %s%s", method, error_detail)
                else:
                    logger.error("HTTP 错误: %s status=%d%s", method, status, error_detail)

                # getUpdates 的 HTTP 错误不应该终止轮询，返回空列表继续
                if method == "getUpdates":
                    return []
                return None

            except Exception:
                logger.exception("请求异常: %s", method)
                # getUpdates 的异常不应该终止轮询，返回空列表继续
                if method == "getUpdates":
                    return []
                return None

        # getUpdates 达到最大重试次数时，不应该打印 ERROR 级别日志
        # 因为这是长轮询的正常行为（网络问题时会持续重试）
        if method == "getUpdates":
            logger.debug("长轮询重试失败，等待后重试: %s", method)
        else:
            logger.error("请求失败，已达最大重试次数: %s", method)
        return None

    # ── 核心 API ──

    async def get_me(self) -> dict | None:
        """获取 bot 信息（GET /getMe）。"""
        result = await self._request("getMe")
        if result:
            self._bot_id = str(result.get("id", ""))
            self._bot_name = result.get("first_name", "")
            logger.info("Telegram Bot 信息: id=%s name=%s", self._bot_id, self._bot_name)
        return result

    async def get_updates(
        self,
        offset: int = 0,
        timeout: int = 30,
        allowed_updates: list[str] | None = None,
    ) -> list[dict]:
        """长轮询获取更新（GET /getUpdates）。

        Args:
            offset: 更新偏移量，从该 ID 之后开始获取
            timeout: 长轮询超时秒数
            allowed_updates: 允许的更新类型列表

        Returns:
            更新列表，每个元素为一个 Update 对象

        Note:
            长轮询超时是正常行为（无新消息），应在超时时返回空列表。
        """
        params = {"offset": offset, "timeout": timeout}
        if allowed_updates:
            params["allowed_updates"] = allowed_updates

        result = await self._request("getUpdates", params=params)
        return result if isinstance(result, list) else []

    async def send_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str = "MarkdownV2",
        reply_to_message_id: str | int | None = None,
    ) -> dict | None:
        """发送文本消息（POST /sendMessage）。

        自动处理长消息分段。
        """
        # 处理长消息
        if len(text) > MAX_MESSAGE_LENGTH:
            return await self._send_long_message(
                chat_id, text, parse_mode, reply_to_message_id
            )

        data = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        return await self._request("sendMessage", data=data)

    async def _send_long_message(
        self,
        chat_id: str | int,
        text: str,
        parse_mode: str,
        reply_to_message_id: str | int | None = None,
    ) -> dict | None:
        """分段发送长消息。"""
        chunks = self._split_text(text, MAX_MESSAGE_LENGTH)
        last_result = None

        for i, chunk in enumerate(chunks):
            data = {
                "chat_id": chat_id,
                "text": chunk,
                "parse_mode": parse_mode,
            }
            # 只有第一条消息引用原消息
            if i == 0 and reply_to_message_id:
                data["reply_to_message_id"] = reply_to_message_id

            result = await self._request("sendMessage", data=data)
            if result:
                last_result = result
            else:
                logger.warning("长消息分段发送失败: chunk %d/%d", i + 1, len(chunks))

        return last_result

    @staticmethod
    def _split_text(text: str, max_length: int) -> list[str]:
        """智能分段文本，优先在换行符处分割。"""
        if len(text) <= max_length:
            return [text]

        chunks = []
        current = ""

        for line in text.split("\n"):
            if len(current) + len(line) + 1 <= max_length:
                current += ("\n" if current else "") + line
            else:
                if current:
                    chunks.append(current)
                # 单行过长，强制分割
                if len(line) > max_length:
                    for i in range(0, len(line), max_length):
                        chunks.append(line[i:i + max_length])
                    current = ""
                else:
                    current = line

        if current:
            chunks.append(current)

        return chunks

    async def send_photo(
        self,
        chat_id: str | int,
        photo: str,
        caption: str = "",
        parse_mode: str = "MarkdownV2",
        reply_to_message_id: str | int | None = None,
    ) -> dict | None:
        """发送图片（POST /sendPhoto）。

        Args:
            chat_id: 目标聊天 ID
            photo: 图片 file_id 或 URL
            caption: 图片说明
            parse_mode: 说明文本解析模式
            reply_to_message_id: 回复的消息 ID
        """
        data = {
            "chat_id": chat_id,
            "photo": photo,
            "caption": caption,
            "parse_mode": parse_mode,
        }
        if reply_to_message_id:
            data["reply_to_message_id"] = reply_to_message_id

        return await self._request("sendPhoto", data=data)

    async def send_document(
        self,
        chat_id: str | int,
        file_path: str,
        caption: str = "",
        parse_mode: str = "MarkdownV2",
        reply_to_message_id: str | int | None = None,
    ) -> dict | None:
        """发送文档文件（POST /sendDocument，multipart/form-data）。

        Args:
            chat_id: 目标聊天 ID
            file_path: 本地文件路径
            caption: 文件说明（可选）
            parse_mode: 说明文本解析模式
            reply_to_message_id: 回复的消息 ID
        """
        import os

        if not self._http:
            self._http = httpx.AsyncClient(timeout=40.0)

        url = f"{self._base_url}sendDocument"
        filename = os.path.basename(file_path)

        data: dict[str, Any] = {"chat_id": str(chat_id)}
        if caption:
            data["caption"] = caption
            data["parse_mode"] = parse_mode
        if reply_to_message_id:
            data["reply_to_message_id"] = str(reply_to_message_id)

        try:
            with open(file_path, "rb") as f:
                files = {"document": (filename, f)}
                resp = await self._http.post(url, data=data, files=files)
                resp.raise_for_status()
                result = resp.json()

            if not result.get("ok"):
                logger.error(
                    "sendDocument 失败: %s",
                    result.get("description", ""),
                )
                return None

            return result.get("result")
        except Exception:
            logger.exception("发送文档失败: %s", file_path)
            return None

    async def edit_message_text(
        self,
        chat_id: str | int,
        message_id: int,
        text: str,
        parse_mode: str = "MarkdownV2",
    ) -> dict | None:
        """编辑消息文本（POST /editMessageText）。"""
        data = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        return await self._request("editMessageText", data=data)

    async def delete_message(
        self,
        chat_id: str | int,
        message_id: int,
    ) -> bool:
        """删除消息（POST /deleteMessage）。"""
        result = await self._request("deleteMessage", data={
            "chat_id": chat_id,
            "message_id": message_id,
        })
        return result is not None

    async def set_message_reaction(
        self,
        chat_id: str | int,
        message_id: int,
        emoji: str,
    ) -> bool:
        """设置消息反应（POST /setMessageReaction）。

        仅支持单个 emoji 反应。
        emoji 为空字符串时清除所有 reactions。

        Note:
            Telegram 部分私聊不支持 reactions，会返回 400 错误。
            此方法会静默失败，不影响主流程。
        """
        # 清除 reaction 时发送空数组
        if not emoji:
            data = {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [],
            }
        else:
            data = {
                "chat_id": chat_id,
                "message_id": message_id,
                "reaction": [{"type": "emoji", "emoji": emoji}],
            }
        result = await self._request("setMessageReaction", data=data, _allow_400=True)
        return result is not None

    async def send_chat_action(
        self,
        chat_id: str | int,
        action: str = "typing",
    ) -> bool:
        """发送聊天动作（POST /sendChatAction）。

        action 默认为 "typing"，在用户端显示"正在输入…"指示器，
        持续约 5 秒或直到 bot 发送消息。
        """
        result = await self._request(
            "sendChatAction",
            data={"chat_id": chat_id, "action": action},
            _allow_400=True,
        )
        return result is not None

    async def send_photo_local(
        self,
        chat_id: str | int,
        file_path: str,
        caption: str = "",
        parse_mode: str = "MarkdownV2",
        reply_to_message_id: str | int | None = None,
    ) -> dict | None:
        """上传本地图片并发送（multipart/form-data）。

        Args:
            chat_id: 目标聊天 ID
            file_path: 本地图片路径
            caption: 图片说明（支持 MarkdownV2）
            reply_to_message_id: 回复的消息 ID
        """
        import os

        url = f"{self._base_url}sendPhoto"
        filename = os.path.basename(file_path)

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mime_map = {"png": "image/png", "gif": "image/gif", "webp": "image/webp"}
        mime_type = mime_map.get(ext, "image/jpeg")

        form_data: dict[str, str] = {
            "chat_id": str(chat_id),
            "parse_mode": parse_mode,
        }
        if caption:
            form_data["caption"] = caption
        if reply_to_message_id:
            form_data["reply_to_message_id"] = str(reply_to_message_id)

        try:
            if not self._http:
                self._http = httpx.AsyncClient(timeout=60.0)

            with open(file_path, "rb") as f:
                files = {"photo": (filename, f, mime_type)}
                resp = await self._http.post(url, data=form_data, files=files)

            resp.raise_for_status()
            result = resp.json()
            if result.get("ok"):
                return result.get("result")
            logger.error("Telegram sendPhoto 失败: %s", result.get("description", ""))
            return None
        except Exception:
            logger.exception("上传本地图片失败: %s", file_path)
            return None

    async def get_file(self, file_id: str) -> dict | None:
        """获取文件信息（POST /getFile）。

        返回包含 file_path 的字典，可用于构建下载 URL。
        """
        return await self._request("getFile", data={"file_id": file_id})

    async def download_file(self, file_path: str) -> bytes | None:
        """下载文件内容。

        Args:
            file_path: getFile 返回的 file_path

        Returns:
            文件内容的 bytes，失败返回 None
        """
        url = f"https://api.telegram.org/file/bot{self._bot_token}/{file_path}"
        try:
            if not self._http:
                self._http = httpx.AsyncClient(proxy=self._proxy or None, timeout=30.0)
            resp = await self._http.get(url)
            resp.raise_for_status()
            return resp.content
        except Exception:
            logger.exception("下载文件失败: %s", file_path)
            return None

    async def get_chat(self, chat_id: str | int) -> dict | None:
        """获取聊天信息（POST /getChat）。"""
        return await self._request("getChat", data={"chat_id": chat_id})

    async def get_chat_member(
        self,
        chat_id: str | int,
        user_id: int,
    ) -> dict | None:
        """获取聊天成员信息（POST /getChatMember）。"""
        return await self._request("getChatMember", data={
            "chat_id": chat_id,
            "user_id": user_id,
        })

    async def get_chat_member_count(self, chat_id: str | int) -> int | None:
        """获取聊天成员数量（POST /getChatMemberCount）。"""
        result = await self._request("getChatMemberCount", data={"chat_id": chat_id})
        return result.get("count") if result else None

    async def get_chat_administrators(self, chat_id: str | int) -> list[dict] | None:
        """获取聊天管理员列表（POST /getChatAdministrators）。"""
        result = await self._request("getChatAdministrators", data={"chat_id": chat_id})
        return result if isinstance(result, list) else None

    # ── 辅助方法 ──

    async def download_image_as_base64(self, file_id: str) -> tuple[str, str] | None:
        """下载图片并转换为 base64。

        Args:
            file_id: 图片的 file_id

        Returns:
            (base64_data, mime_type) 或 None
        """
        file_info = await self.get_file(file_id)
        if not file_info:
            return None

        file_path = file_info.get("file_path", "")
        if not file_path:
            return None

        content = await self.download_file(file_path)
        if not content:
            return None

        # 简单的 MIME 类型推断
        mime_type = "image/jpeg"
        if file_path.endswith(".png"):
            mime_type = "image/png"
        elif file_path.endswith(".gif"):
            mime_type = "image/gif"
        elif file_path.endswith(".webp"):
            mime_type = "image/webp"

        b64 = base64.b64encode(content).decode("ascii")
        return b64, mime_type

    @property
    def bot_id(self) -> str:
        """Bot 的数字 ID。"""
        return self._bot_id

    @property
    def bot_name(self) -> str:
        """Bot 的名称。"""
        return self._bot_name
