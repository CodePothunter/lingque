"""飞书消息发送"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
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

_RECEIVE_ID_TYPE_MAP = {"oc_": "chat_id", "ou_": "open_id", "on_": "union_id"}


def _infer_receive_id_type(receive_id: str) -> str:
    """根据 ID 前缀推断 receive_id_type，默认 chat_id。"""
    for prefix, id_type in _RECEIVE_ID_TYPE_MAP.items():
        if receive_id.startswith(prefix):
            return id_type
    return "chat_id"


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
        self._cached_chats: set[str] = set()  # 已拉取成员的 chat_id
        self._left_chats: set[str] = set()    # bot 已退出的 chat_id
        self._bot_members: dict[str, set[str]] = {}  # chat_id → bot open_id 集合
        self.bot_open_id: str = ""  # 本实例的 bot open_id，由 gateway 设置
        # bot 身份推断：cli_xxx → name，持久化到实例工作目录
        self._bot_id_map: dict[str, str] = {}
        self._bot_id_path: Path | None = None  # 由 gateway 设置

    async def send_text(self, chat_id: str, text: str) -> str | None:
        """发送文本消息到指定会话，返回 message_id。
        当文本包含代码块等复杂 Markdown 时自动切换为卡片消息。
        """
        if _has_complex_markdown(text):
            card = _build_markdown_card(text)
            return await self.send_card(chat_id, card)
        text = _strip_markdown(text)
        id_type = _infer_receive_id_type(chat_id)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(id_type)
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
            logger.error("发送消息失败: code=%d msg=%s, receive_id_type=%s", resp.code, resp.msg, id_type)
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
        id_type = _infer_receive_id_type(chat_id)
        req = (
            CreateMessageRequest.builder()
            .receive_id_type(id_type)
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
            "机器人信息: app_name=%s open_id=%s",
            bot.get("app_name"),
            bot.get("open_id"),
        )
        return bot

    async def get_user_name(self, open_id: str, chat_id: str = "") -> str:
        """获取用户名，带内存缓存。通过群成员 API 批量拉取。"""
        cached = self._user_name_cache.get(open_id)
        if cached is not None:
            return cached

        # 如果有 chat_id，通过群成员列表批量缓存
        if chat_id:
            await self._cache_chat_members(chat_id)
            cached = self._user_name_cache.get(open_id)
            if cached is not None:
                return cached

        # 回退：用 open_id 尾部
        fallback = open_id[-6:]
        self._user_name_cache[open_id] = fallback
        return fallback

    async def _cache_chat_members(self, chat_id: str) -> None:
        """通过群成员 API 批量缓存 chat 内所有成员的名字（含机器人）。"""
        if chat_id in self._cached_chats or chat_id in self._left_chats:
            return
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"https://open.feishu.cn/open-apis/im/v1/chats/{chat_id}/members",
                    params={"member_id_type": "open_id", "page_size": 100},
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
            if data.get("code") != 0:
                logger.warning("拉取群成员失败: code=%d msg=%s", data.get("code"), data.get("msg"))
                return
            items = data.get("data", {}).get("items", [])
            bots: set[str] = set()
            for item in items:
                mid = item.get("member_id", "")
                name = item.get("name", "")
                member_type = item.get("member_type", "user")
                if mid and name:
                    self._user_name_cache[mid] = name
                if member_type == "bot" and mid:
                    bots.add(mid)
            self._bot_members[chat_id] = bots
            self._cached_chats.add(chat_id)
            bot_count = len(bots)
            logger.info("缓存群 %s 成员 %d 人（含 %d 个 bot）", chat_id[-8:], len(items), bot_count)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                self._left_chats.add(chat_id)
                logger.warning("Bot 已不在群 %s，标记为已退出", chat_id)
            else:
                logger.warning("拉取群成员异常: %s HTTP %d", chat_id, e.response.status_code)
        except Exception as e:
            logger.warning("拉取群成员异常: %s %s", chat_id, e)

    def is_chat_left(self, chat_id: str) -> bool:
        """检查 bot 是否已退出某群聊（基于成员 API 400 检测）"""
        return chat_id in self._left_chats

    def register_bot_member(self, chat_id: str, bot_open_id: str) -> None:
        """通过消息信号注册群聊中的 bot（补充成员 API 的不足）

        bot_open_id 可能是 app_id (cli_xxx) 或 open_id (ou_xxx)，
        因为飞书消息列表 API 对 bot 返回 app_id 而非 open_id。
        """
        self._bot_members.setdefault(chat_id, set()).add(bot_open_id)


    async def resolve_name(self, open_id: str) -> str:
        """查找用户/bot 的名字，优先缓存，回退到联系人 API。

        注意：不缓存 fallback 值（截断 ID），因为名字可能
        稍后通过其他途径被发现。
        """
        cached = self._user_name_cache.get(open_id)
        if cached is not None:
            return cached
        # app_id (cli_xxx)：先查推断记忆，无则返回截断 ID
        if open_id.startswith("cli_"):
            inferred = self._bot_id_map.get(open_id)
            if inferred:
                self._user_name_cache[open_id] = inferred
                return inferred
            return open_id[-6:]
        # open_id (ou_xxx) → 联系人 API
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    f"https://open.feishu.cn/open-apis/contact/v3/users/{open_id}",
                    params={"user_id_type": "open_id"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
            if data.get("code") == 0:
                name = data.get("data", {}).get("user", {}).get("name", "")
                if name:
                    self._user_name_cache[open_id] = name
                    return name
        except Exception:
            pass
        return open_id[-6:]

    # ── Bot 身份推断与持久化 ──

    def load_bot_identities(self, home: Path) -> None:
        """从实例工作目录加载已记忆的 bot 身份映射。"""
        self._bot_id_path = home / "bot_identities.json"
        if self._bot_id_path.exists():
            try:
                self._bot_id_map = json.loads(self._bot_id_path.read_text())
                # 加载到 name cache 以便立即可用
                self._user_name_cache.update(self._bot_id_map)
                logger.info("加载 bot 身份记忆: %s", self._bot_id_map)
            except Exception:
                logger.warning("加载 bot_identities.json 失败", exc_info=True)

    def _save_bot_identities(self) -> None:
        """持久化 bot 身份映射到文件。"""
        if self._bot_id_path and self._bot_id_map:
            try:
                self._bot_id_path.write_text(
                    json.dumps(self._bot_id_map, ensure_ascii=False, indent=2)
                )
            except Exception:
                logger.warning("保存 bot_identities.json 失败", exc_info=True)

    def infer_bot_identities(self, messages: list[dict]) -> None:
        """从消息序列中推断未知 bot 的 cli_xxx → name 映射。

        策略：
        1. 收集消息中 @提及的所有名字（来自 mentions）
        2. 收集所有未识别的 bot sender_id (cli_xxx)
        3. 排除已知映射后，若一一对应则建立关联
        4. 备用：用时序线索（@提及后紧跟 bot 回复）推断
        """
        # 收集未知 bot sender_id 和 mentions 中的名字
        unknown_bots: list[str] = []
        mentioned_names: set[str] = set()
        known_cli_names = set(self._user_name_cache.get(k, "")
                              for k in self._user_name_cache
                              if k.startswith("cli_"))

        for msg in messages:
            sid = msg.get("sender_id", "")
            if (msg.get("sender_type") == "app"
                    and sid.startswith("cli_")
                    and sid not in self._user_name_cache):
                if sid not in unknown_bots:
                    unknown_bots.append(sid)
            for name in msg.get("_mentioned_names", []):
                mentioned_names.add(name)

        if not unknown_bots:
            return

        # 排除已知名字（包括自己），剩余即候选
        unmatched_names = mentioned_names - known_cli_names
        new_found = False

        # 策略 A：全局排除法 — 1 个未知 bot 对 1 个未匹配名字
        if len(unknown_bots) == 1 and len(unmatched_names) == 1:
            bot_id = unknown_bots[0]
            name = next(iter(unmatched_names))
            self._bot_id_map[bot_id] = name
            self._user_name_cache[bot_id] = name
            logger.info("推断 bot 身份（排除法）: %s → %s", bot_id, name)
            new_found = True
        elif unknown_bots and unmatched_names:
            # 策略 B：时序法 — @提及后紧跟的 bot 回复
            for i, msg in enumerate(messages):
                sid = msg.get("sender_id", "")
                if (msg.get("sender_type") != "app"
                        or not sid.startswith("cli_")
                        or sid in self._user_name_cache):
                    continue
                # 向前扫描最近 3 条消息，找最近的 @提及
                for j in range(i - 1, max(-1, i - 4), -1):
                    prev_names = messages[j].get("_mentioned_names", [])
                    for name in prev_names:
                        if name not in known_cli_names and name in unmatched_names:
                            self._bot_id_map[sid] = name
                            self._user_name_cache[sid] = name
                            known_cli_names.add(name)
                            unmatched_names.discard(name)
                            logger.info("推断 bot 身份（时序法）: %s → %s", sid, name)
                            new_found = True
                            break
                    if sid in self._user_name_cache:
                        break

        if new_found:
            self._save_bot_identities()

    def get_bot_members(self, chat_id: str) -> set[str]:
        """返回群聊中的 bot open_id 集合（不含自己）"""
        bots = self._bot_members.get(chat_id, set())
        return bots - {self.bot_open_id} if self.bot_open_id else bots

    def get_member_name(self, open_id: str) -> str:
        """同步查找已缓存的成员名字，无缓存时返回 ID 尾部"""
        return self._user_name_cache.get(open_id, open_id[-6:])

    # ── Reaction API（意图信号）──

    async def add_reaction(
        self, message_id: str, emoji_type: str = "OnIt",
    ) -> str | None:
        """给消息添加 reaction（用作 bot 间意图信号），返回 reaction_id 或 None"""
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.post(
                    f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions",
                    json={"reaction_type": {"emoji_type": emoji_type}},
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
            if data.get("code") == 0:
                reaction_id = data.get("data", {}).get("reaction_id", "")
                logger.debug("添加 reaction %s 到 %s", emoji_type, message_id[-8:])
                return reaction_id
            logger.warning("添加 reaction 失败: code=%d", data.get("code", -1))
        except Exception:
            logger.warning("添加 reaction 异常", exc_info=True)
        return None

    async def remove_reaction(
        self, message_id: str, reaction_id: str,
    ) -> bool:
        """移除消息上的 reaction"""
        if not reaction_id:
            return False
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.delete(
                    f"https://open.feishu.cn/open-apis/im/v1/messages/{message_id}/reactions/{reaction_id}",
                    headers={"Authorization": f"Bearer {token}"},
                )
                data = resp.json()
            if data.get("code") == 0:
                logger.debug("移除 reaction %s", reaction_id[:8])
                return True
            logger.warning("移除 reaction 失败: code=%d", data.get("code", -1))
        except Exception:
            logger.warning("移除 reaction 异常", exc_info=True)
        return False

    async def fetch_chat_messages(self, chat_id: str, count: int = 10) -> list[dict]:
        """拉取群聊最近消息（含机器人消息），返回按时间正序排列的消息列表。

        每条消息格式: {message_id, sender_id, text, sender_type}
        sender_type: "user" 或 "app"
        """
        try:
            token = await self._get_tenant_token()
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    "https://open.feishu.cn/open-apis/im/v1/messages",
                    params={
                        "container_id_type": "chat",
                        "container_id": chat_id,
                        "sort_type": "ByCreateTimeDesc",
                        "page_size": count,
                        "user_id_type": "open_id",
                    },
                    headers={"Authorization": f"Bearer {token}"},
                )
                resp.raise_for_status()
                data = resp.json()
            if data.get("code") != 0:
                logger.warning("拉取群消息失败: code=%d msg=%s", data.get("code"), data.get("msg"))
                return []
            items = data.get("data", {}).get("items", [])
            results = []
            for item in reversed(items):  # API 返回倒序，反转为正序
                try:
                    body = json.loads(item.get("body", {}).get("content", "{}"))
                    text = body.get("text", "")
                except (json.JSONDecodeError, TypeError, AttributeError):
                    text = ""
                if not text:
                    continue
                # 解析 mentions，将 @_user_N 占位符替换为真名
                mentioned_names: list[str] = []
                mentions = item.get("mentions")
                if mentions:
                    for m in mentions:
                        key = m.get("key", "")
                        name = m.get("name", "")
                        mid = m.get("id", "")
                        if key and name:
                            text = text.replace(key, f"@{name}")
                            mentioned_names.append(name)
                            if mid:
                                self._user_name_cache[mid] = name
                        elif key:
                            text = text.replace(key, "")
                sender = item.get("sender", {})
                sender_id = sender.get("id", "")
                results.append({
                    "message_id": item.get("message_id", ""),
                    "sender_id": sender_id,
                    "sender_type": sender.get("sender_type", "user"),
                    "text": text,
                    "create_time": item.get("create_time", ""),
                    "_mentioned_names": mentioned_names,
                })
            # 尝试从消息上下文推断未知 bot 的身份
            self.infer_bot_identities(results)
            return results
        except httpx.HTTPStatusError:
            raise  # 让 400/403 等 HTTP 错误向上传播，由调用方处理
        except Exception as e:
            logger.warning("拉取群消息异常: %s %s", chat_id, e)
            return []

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
