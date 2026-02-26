"""飞书平台适配器 — 将飞书 SDK 封装为平台无关接口"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path
from typing import Any

import httpx

from lq.feishu.listener import FeishuListener
from lq.feishu.sender import FeishuSender
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

logger = logging.getLogger(__name__)

# 飞书不支持的消息类型
_NON_TEXT_TYPES = {"file", "audio", "media", "sticker", "share_chat", "share_user"}

# 飞书 MessageType 字符串 → 标准枚举映射
_MSG_TYPE_MAP: dict[str, MessageType] = {
    "text": MessageType.TEXT,
    "image": MessageType.IMAGE,
    "post": MessageType.RICH_TEXT,
    "file": MessageType.FILE,
    "audio": MessageType.AUDIO,
    "media": MessageType.VIDEO,
    "sticker": MessageType.STICKER,
    "share_chat": MessageType.SHARE,
    "share_user": MessageType.SHARE,
}

# 意图信号使用的 emoji
THINKING_EMOJI = "OnIt"


class FeishuAdapter(PlatformAdapter):
    """飞书平台适配器。

    内部持有 FeishuSender（发送）和 FeishuListener（接收），
    对外暴露标准 PlatformAdapter 接口。所有飞书补偿行为封装在内部：
    - Bot 消息轮询（WS 收不到的 bot 消息通过 REST 补漏）
    - Bot 身份推断（cli_xxx → 真名）
    - @提及占位符替换
    - Markdown → 卡片自动切换
    - Token 自动刷新
    """

    def __init__(
        self,
        app_id: str,
        app_secret: str,
        home: Path,
    ) -> None:
        self._app_id = app_id
        self._app_secret = app_secret
        self._home = home
        self._sender = FeishuSender(app_id, app_secret)
        self._queue: asyncio.Queue | None = None
        self._raw_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []
        self._shutdown = asyncio.Event()

        # message_id → chat_id 映射（用于 reaction 事件关联群聊）
        self._msg_chat_map: dict[str, str] = {}
        self._msg_chat_map_max = 500

        # 活跃群聊跟踪
        self._active_groups: dict[str, float] = {}
        self._known_group_ids: set[str] = set()
        self._polled_msg_ids: dict[str, set[str]] = {}
        self._poll_fail_count: dict[str, int] = {}

        self._identity: BotIdentity | None = None

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        if self._identity:
            return self._identity
        bot_info = await self._sender.fetch_bot_info()
        bot_id = bot_info.get("open_id", "")
        bot_name = bot_info.get("app_name") or bot_info.get("bot_name") or ""
        self._sender.bot_open_id = bot_id
        # 缓存自己的 ID → name
        if bot_id and bot_name:
            self._sender._user_name_cache[bot_id] = bot_name
        if self._app_id and bot_name:
            self._sender._user_name_cache[self._app_id] = bot_name
        self._sender.load_bot_identities(self._home)
        self._identity = BotIdentity(bot_id=bot_id, bot_name=bot_name)
        return self._identity

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        loop = asyncio.get_running_loop()

        # 启动飞书 WS 监听（daemon 线程），事件写入 _raw_queue
        listener = FeishuListener(
            self._app_id,
            self._app_secret,
            self._raw_queue,
            loop,
        )
        feishu_thread = threading.Thread(
            target=listener.start_blocking,
            name="feishu-ws",
            daemon=True,
        )
        feishu_thread.start()
        logger.info("飞书 WebSocket 线程已启动")

        # 加载已知群聊
        self._load_known_groups()

        # 启动事件转换协程和轮询协程
        self._tasks.append(
            asyncio.create_task(self._event_converter(), name="feishu-converter")
        )
        self._tasks.append(
            asyncio.create_task(self._poll_bot_messages(), name="feishu-poll")
        )

    async def disconnect(self) -> None:
        self._shutdown.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.wait(self._tasks, timeout=3.0)
        self._tasks.clear()
        self._save_known_groups()

    # ── 表达 ──

    async def send(self, message: OutgoingMessage) -> str | None:
        text = message.text
        # @name → 飞书 <at> 标签
        text = self._convert_outgoing_mentions(text)

        if message.card:
            card_json = self._convert_standard_card(message.card)
            if message.reply_to:
                return await self._sender.reply_card(message.reply_to, card_json)
            return await self._sender.send_card(message.chat_id, card_json)

        if message.reply_to:
            return await self._sender.reply_text(message.reply_to, text)
        return await self._sender.send_text(message.chat_id, text)

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        return await self._sender.add_reaction(message_id, THINKING_EMOJI)

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        await self._sender.remove_reaction(message_id, handle)

    # ── 感官 ──

    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        return await self._sender.download_image(message_id, resource_key)

    # ── 认知 ──

    async def resolve_name(self, user_id: str) -> str:
        return await self._sender.resolve_name(user_id)

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        await self._sender._cache_chat_members(chat_id)
        members: list[ChatMember] = []
        # 群成员 API 缓存在 _user_name_cache 和 _bot_members
        # 重新拉取后遍历缓存
        bot_ids = self._sender._bot_members.get(chat_id, set())
        # 通过群成员 API 返回的数据在 _cached_chats 中
        # 但飞书 API 不提供持久化的群成员列表，我们从缓存构建
        # 这里返回已知的 bot 成员信息
        for bid in bot_ids:
            name = self._sender.get_member_name(bid)
            members.append(ChatMember(user_id=bid, name=name, is_bot=True))
        return members

    # ── 可选行为 ──

    async def react(self, message_id: str, emoji: str) -> str | None:
        return await self._sender.add_reaction(message_id, emoji)

    async def unreact(self, message_id: str, handle: str) -> bool:
        return await self._sender.remove_reaction(message_id, handle)

    # ── 内部：标准卡片转换 ──

    @staticmethod
    def _convert_standard_card(card: dict) -> dict:
        """将平台无关标准卡片转为飞书 interactive card JSON。"""
        from lq.feishu.cards import (
            build_confirm_card,
            build_error_card,
            build_info_card,
            build_schedule_card,
            build_task_card,
        )

        card_type = card.get("type", "")
        if card_type == "info":
            return build_info_card(
                card.get("title", ""),
                card.get("content", ""),
                color=card.get("color", "blue"),
            )
        if card_type == "schedule":
            return build_schedule_card(card.get("events", []))
        if card_type == "task_list":
            return build_task_card(card.get("tasks", []))
        if card_type == "error":
            return build_error_card(
                card.get("title", "错误"),
                card.get("message", ""),
            )
        if card_type == "confirm":
            return build_confirm_card(
                card.get("title", ""),
                card.get("content", ""),
                card.get("confirm_text", "确认"),
                card.get("cancel_text", "取消"),
                card.get("callback_data", {}),
            )
        # 未知类型 → 提取可用字段构建 markdown 卡片
        parts: list[str] = []
        if card.get("title"):
            parts.append(f"**{card['title']}**")
        if card.get("content"):
            parts.append(card["content"])
        if card.get("message"):
            parts.append(card["message"])
        text = "\n\n".join(parts) if parts else json.dumps(card, ensure_ascii=False)
        return {"elements": [{"tag": "markdown", "content": text}]}

    # ── 内部：出站 @提及转换 ──

    def _convert_outgoing_mentions(self, text: str) -> str:
        """将 @name 替换为飞书 <at> 标签。"""
        cache = self._sender._user_name_cache
        # 按名字长度降序匹配，避免子串冲突
        name_to_id: dict[str, str] = {}
        for uid, name in cache.items():
            if name and uid.startswith("ou_"):
                name_to_id[name] = uid
        for name in sorted(name_to_id, key=len, reverse=True):
            uid = name_to_id[name]
            tag = f'<at user_id="{uid}">{name}</at>'
            text = text.replace(f"@{name}", tag)
        return text

    # ── 内部：事件转换 ──

    async def _event_converter(self) -> None:
        """从 _raw_queue 读取原始飞书事件，转换为标准格式后投入用户队列。"""
        logger.info("飞书事件转换器启动")
        while not self._shutdown.is_set():
            try:
                data = await asyncio.wait_for(self._raw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            try:
                event_type = data.get("event_type", "")
                if event_type == "im.message.receive_v1":
                    await self._convert_message_event(data)
                elif event_type == "reaction.created":
                    self._convert_reaction_event(data)
                elif event_type == "bot.added":
                    self._convert_bot_added(data)
                elif event_type == "user.added":
                    self._convert_user_added(data)
                elif event_type == "card.action.trigger":
                    self._convert_card_action(data)
                else:
                    logger.debug("忽略飞书事件类型: %s", event_type)
            except Exception:
                logger.exception("转换飞书事件失败: %s", data.get("event_type", "?"))

        logger.info("飞书事件转换器已停止")

    async def _convert_message_event(self, data: dict) -> None:
        """将飞书 IM 消息事件转换为标准 IncomingMessage。"""
        event = data["event"]
        message = event.message
        sender_open_id = event.sender.sender_id.open_id

        # 忽略自己发的消息
        identity = self._identity
        if identity and sender_open_id == identity.bot_id:
            return

        msg_id = message.message_id
        chat_id = message.chat_id
        chat_type_str = message.chat_type
        msg_type_str = getattr(message, "message_type", "") or "text"

        # 记录 message_id → chat_id 映射
        self._record_msg_chat(msg_id, chat_id)

        # 标记群聊为活跃
        if chat_type_str == "group":
            self._active_groups[chat_id] = time.time()
            self._known_group_ids.add(chat_id)

        # 提取文本
        text = self._extract_text(message)
        # 提取图片 key
        image_keys = self._extract_image_keys(message)

        # 解析 @提及
        raw_mentions = getattr(message, "mentions", None)
        mentions: list[Mention] = []
        is_mention_bot = False
        if raw_mentions:
            for m in raw_mentions:
                key = getattr(m, "key", "")
                open_id = ""
                if hasattr(m, "id") and hasattr(m.id, "open_id"):
                    open_id = m.id.open_id
                name = getattr(m, "name", "") or ""
                # 缓存 mention 中的 id → name
                if open_id and name:
                    self._sender._user_name_cache[open_id] = name
                is_self = bool(identity and open_id == identity.bot_id)
                if is_self:
                    is_mention_bot = True
                    # 移除自己的 @ 占位符
                    if key:
                        text = text.replace(key, "")
                elif key and name:
                    # 其他用户的 @ → 替换为 @真名
                    text = text.replace(key, f"@{name}")
                elif key:
                    text = text.replace(key, "")
                mentions.append(Mention(user_id=open_id, name=name, is_bot_self=is_self))
        else:
            # 无 mentions 信息，清理占位符
            text = re.sub(r"@_user_\d+\s*", "", text)

        # 文本层兜底：飞书有时不解析 @
        if not is_mention_bot and identity and identity.bot_name:
            if f"@{identity.bot_name}" in text:
                is_mention_bot = True

        text = text.strip()

        # 判断 sender_type
        # WS 推的消息一般是人类发的，但 inbox 消息可能来自 local_cli_user
        sender_type = SenderType.USER

        # 解析 sender_name（异步，可能调 API）
        sender_name = await self._sender.get_user_name(sender_open_id, chat_id=chat_id)

        # 检查是否已退出群聊
        if self._sender.is_chat_left(chat_id):
            return

        # 提取引用回复的 parent_id
        parent_id = getattr(message, "parent_id", "") or ""

        msg = IncomingMessage(
            message_id=msg_id,
            chat_id=chat_id,
            chat_type=ChatType.PRIVATE if chat_type_str == "p2p" else ChatType.GROUP,
            sender_id=sender_open_id,
            sender_type=sender_type,
            sender_name=sender_name,
            message_type=_MSG_TYPE_MAP.get(msg_type_str, MessageType.UNKNOWN),
            text=text,
            mentions=mentions,
            is_mention_bot=is_mention_bot,
            image_keys=image_keys,
            reply_to_id=parent_id,
            timestamp=int(time.time() * 1000),
            raw=message,
        )

        self._queue.put_nowait({"event_type": "message", "message": msg})

    def _convert_reaction_event(self, data: dict) -> None:
        """将飞书 reaction 事件转换为标准 Reaction。"""
        emoji = data.get("emoji_type", "")
        operator_id = data.get("operator_id", "")
        message_id = data.get("message_id", "")

        # 查找 chat_id
        chat_id = self._msg_chat_map.get(message_id, "")

        is_thinking = emoji == THINKING_EMOJI
        reaction = Reaction(
            reaction_id="",
            chat_id=chat_id,
            message_id=message_id,
            emoji=emoji,
            operator_id=operator_id,
            operator_type=SenderType.USER,
            is_thinking_signal=is_thinking,
        )
        self._queue.put_nowait({"event_type": "reaction", "reaction": reaction})

    def _convert_bot_added(self, data: dict) -> None:
        """将 bot 入群事件转换为标准 member_change。"""
        chat_id = data.get("chat_id", "")
        if chat_id:
            self._active_groups[chat_id] = time.time()
            self._known_group_ids.add(chat_id)
        self._queue.put_nowait({
            "event_type": "member_change",
            "chat_id": chat_id,
            "change_type": "bot_joined",
            "users": [],
        })

    def _convert_user_added(self, data: dict) -> None:
        """将用户入群事件转换为标准 member_change。"""
        chat_id = data.get("chat_id", "")
        raw_users = data.get("users", [])
        users = []
        for u in raw_users:
            open_id = u.get("open_id", "")
            name = u.get("name", "")
            if open_id and name:
                self._sender._user_name_cache[open_id] = name
            users.append({"user_id": open_id, "name": name})
        self._queue.put_nowait({
            "event_type": "member_change",
            "chat_id": chat_id,
            "change_type": "user_joined",
            "users": users,
        })

    def _convert_card_action(self, data: dict) -> None:
        """将卡片交互转换为标准 CardAction。"""
        event = data.get("event")
        if not event:
            return
        action = getattr(event, "action", None) or {}
        if isinstance(action, dict):
            value = action.get("value", {})
        else:
            value = getattr(action, "value", {}) or {}

        action_type = value.get("action", "unknown") if isinstance(value, dict) else "unknown"

        operator = getattr(event, "operator", None)
        operator_id = ""
        if operator:
            open_id_obj = getattr(operator, "open_id", None)
            operator_id = open_id_obj if isinstance(open_id_obj, str) else str(open_id_obj or "")

        card_action = CardAction(
            action_type=action_type,
            value=value if isinstance(value, dict) else {},
            operator_id=operator_id,
        )
        self._queue.put_nowait({"event_type": "interaction", "action": card_action})

    # ── 内部：飞书消息解析 ──

    @staticmethod
    def _extract_text(message: Any) -> str:
        """从飞书消息中提取文本，支持 text 和 post 格式。"""
        try:
            content = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            return ""

        if "text" in content:
            return content["text"]

        # post 富文本 → Markdown
        post = content.get("post") or content
        if isinstance(post, dict) and not post.get("content"):
            post = next(iter(post.values()), {}) if post else {}
        if not isinstance(post, dict):
            return ""

        lines: list[str] = []
        title = post.get("title", "")
        if title:
            lines.append(f"**{title}**")

        for paragraph in post.get("content", []):
            parts: list[str] = []
            for elem in paragraph:
                tag = elem.get("tag", "")
                if tag == "text":
                    parts.append(elem.get("text", ""))
                elif tag == "a":
                    href = elem.get("href", "")
                    text = elem.get("text", href)
                    parts.append(f"[{text}]({href})" if href else text)
                elif tag == "at":
                    name = elem.get("user_name", "") or elem.get("user_id", "")
                    parts.append(f"@{name}")
                elif tag == "img":
                    parts.append("[图片]")
                elif tag == "media":
                    parts.append("[媒体]")
            lines.append("".join(parts))

        return "\n".join(lines)

    @staticmethod
    def _extract_image_keys(message: Any) -> list[str]:
        """从飞书消息中提取所有图片的 image_key。"""
        keys: list[str] = []
        try:
            content = json.loads(message.content)
        except (json.JSONDecodeError, TypeError):
            return keys

        msg_type = getattr(message, "message_type", None) or ""
        if msg_type == "image":
            key = content.get("image_key", "")
            if key:
                keys.append(key)
            return keys

        # post 富文本
        post = content.get("post") or content
        if isinstance(post, dict) and not post.get("content"):
            post = next(iter(post.values()), {}) if post else {}
        if not isinstance(post, dict):
            return keys

        for paragraph in post.get("content", []):
            for elem in paragraph:
                if elem.get("tag") == "img":
                    key = elem.get("image_key", "")
                    if key:
                        keys.append(key)
        return keys

    # ── 内部：bot 消息轮询（飞书补偿） ──

    async def _poll_bot_messages(self) -> None:
        """后台轮询活跃群聊，补充 WS 收不到的 bot 消息。"""
        logger.info("飞书 bot 消息轮询启动")
        while not self._shutdown.is_set():
            try:
                await asyncio.sleep(3.0)
                active = self._get_poll_targets()
                if not active:
                    continue
                identity = self._identity
                bot_self_ids = set()
                if identity:
                    bot_self_ids.add(identity.bot_id)
                bot_self_ids.add(self._app_id)

                for chat_id in active:
                    if self._shutdown.is_set():
                        break
                    if self._poll_fail_count.get(chat_id, 0) >= 3:
                        continue
                    try:
                        api_msgs = await self._sender.fetch_chat_messages(chat_id, 10)
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code in (400, 403):
                            self._poll_fail_count[chat_id] = self._poll_fail_count.get(chat_id, 0) + 1
                            if self._poll_fail_count[chat_id] >= 3:
                                logger.warning("群 %s 连续 3 次失败，停止轮询", chat_id[-8:])
                                self._known_group_ids.discard(chat_id)
                                self._active_groups.pop(chat_id, None)
                                self._save_known_groups()
                                # 投递 bot_left 事件
                                self._queue.put_nowait({
                                    "event_type": "member_change",
                                    "chat_id": chat_id,
                                    "change_type": "bot_left",
                                    "users": [],
                                })
                        continue
                    except Exception:
                        logger.warning("轮询群 %s 失败", chat_id[-8:], exc_info=True)
                        continue

                    self._poll_fail_count.pop(chat_id, None)
                    if not api_msgs:
                        continue

                    for msg in api_msgs:
                        if msg.get("sender_type") != "app":
                            continue
                        self._sender.register_bot_member(chat_id, msg["sender_id"])
                        if msg.get("sender_id") in bot_self_ids:
                            continue
                        # 去重
                        known = self._polled_msg_ids.setdefault(chat_id, set())
                        msg_id = msg.get("message_id", "")
                        if msg_id in known:
                            continue
                        known.add(msg_id)

                        sender_name = await self._sender.resolve_name(msg["sender_id"])
                        self._record_msg_chat(msg_id, chat_id)

                        polled_msg = IncomingMessage(
                            message_id=msg_id,
                            chat_id=chat_id,
                            chat_type=ChatType.GROUP,
                            sender_id=msg["sender_id"],
                            sender_type=SenderType.BOT,
                            sender_name=sender_name,
                            message_type=MessageType.TEXT,
                            text=msg.get("text", ""),
                            reply_to_id=msg.get("parent_id", ""),
                            timestamp=int(msg.get("create_time", "0") or "0"),
                        )
                        self._queue.put_nowait({"event_type": "message", "message": polled_msg})

                    if len(active) > 1:
                        await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("bot 消息轮询异常")
        logger.info("飞书 bot 消息轮询已停止")

    # ── 内部：辅助 ──

    def _record_msg_chat(self, message_id: str, chat_id: str) -> None:
        """记录 message_id → chat_id 映射。"""
        self._msg_chat_map[message_id] = chat_id
        # 限制大小
        while len(self._msg_chat_map) > self._msg_chat_map_max:
            oldest = next(iter(self._msg_chat_map))
            del self._msg_chat_map[oldest]

    def _get_poll_targets(self) -> list[str]:
        """返回需要轮询的群聊 ID 列表。"""
        now = time.time()
        # 清理过期活跃群（10 分钟无消息且非已知群）
        expired = [
            cid for cid, ts in self._active_groups.items()
            if now - ts > 600 and cid not in self._known_group_ids
        ]
        for cid in expired:
            self._active_groups.pop(cid, None)
            self._polled_msg_ids.pop(cid, None)
        # 确保所有已知群在活跃列表
        for cid in self._known_group_ids:
            if cid not in self._active_groups:
                self._active_groups[cid] = now
        return list(self._active_groups)

    def _load_known_groups(self) -> None:
        """从 groups.json 加载已知群聊 ID。"""
        path = self._home / "groups.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self._known_group_ids = set(data.get("known_group_ids", []))
            logger.info("加载 %d 个已知群聊", len(self._known_group_ids))
        except Exception:
            logger.warning("加载 groups.json 失败", exc_info=True)

    def _save_known_groups(self) -> None:
        """保存已知群聊 ID 到 groups.json。"""
        path = self._home / "groups.json"
        try:
            path.write_text(
                json.dumps({"known_group_ids": sorted(self._known_group_ids)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("保存 groups.json 失败", exc_info=True)

    # ── 供 gateway/calendar 使用的内部访问器 ──

    @property
    def feishu_client(self) -> Any:
        """暴露飞书 SDK client 供独立服务（如 FeishuCalendar）使用。"""
        return self._sender.client

    @property
    def known_group_ids(self) -> set[str]:
        """已知群聊 ID 的副本。"""
        return set(self._known_group_ids)

    def register_known_group(self, chat_id: str) -> None:
        """外部注册已知群聊（如从持久化数据恢复）。"""
        self._known_group_ids.add(chat_id)
        self._active_groups.setdefault(chat_id, time.time())

    def remove_known_group(self, chat_id: str) -> None:
        """移除无效群聊。"""
        self._known_group_ids.discard(chat_id)
        self._active_groups.pop(chat_id, None)
        self._polled_msg_ids.pop(chat_id, None)
        self._save_known_groups()
