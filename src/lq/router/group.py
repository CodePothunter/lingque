"""群聊处理 + 缓冲区评估 + 介入决策 + 协作记录 + 成员变更"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from collections import OrderedDict
from typing import Any

from lq.buffer import MessageBuffer, rule_check
from lq.platform import IncomingMessage, OutgoingMessage, MessageType, SenderType
from lq.prompts import (
    TAG_CONSTRAINTS, wrap_tag,
    CONSTRAINTS_GROUP,
    NON_TEXT_REPLY_GROUP, EMPTY_AT_FALLBACK,
    GROUP_EVAL_PROMPT,
    GROUP_MSG_SELF, GROUP_MSG_OTHER,
    GROUP_MSG_WITH_ID_SELF, GROUP_MSG_WITH_ID_OTHER,
    GROUP_MSG_REPLY_SUFFIX,
    GROUP_INTERVENE_SYSTEM_SUFFIX,
    SENDER_UNKNOWN, BOT_POLL_AT_REASON,
    BOT_SELF_INTRO_SYSTEM, BOT_SELF_INTRO_USER,
    USER_WELCOME_SYSTEM, USER_WELCOME_USER,
    FLUSH_NO_RESULT,
    COMPACTION_DAILY_HEADER, COMPACTION_MEMORY_HEADER, COMPACTION_SUMMARY_PROMPT,
)

logger = logging.getLogger(__name__)


class GroupChatMixin:
    """群聊消息分发、三层介入策略、协作记录与成员事件处理。"""

    async def _handle_group(self, msg: IncomingMessage) -> None:
        """处理群聊消息"""
        chat_id = msg.chat_id

        if msg.is_mention_bot:
            # 先把 @at 消息也写入缓冲区，保留完整上下文
            if msg.text:
                if chat_id not in self.group_buffers:
                    self.group_buffers[chat_id] = MessageBuffer()
                self.group_buffers[chat_id].add({
                    "text": msg.text,
                    "sender_id": msg.sender_id,
                    "sender_name": msg.sender_name,
                    "message_id": msg.message_id,
                    "chat_id": chat_id,
                    "reply_to_id": msg.reply_to_id,
                    "sender_type": msg.sender_type,
                })
                # 持久化到 session 文件（observe_only，不影响 LLM 上下文）
                self._log_group_message_to_session(msg)
            await self._handle_group_at(msg)
            return

        # 第一层规则：极短无实质消息直接忽略
        text = msg.text
        if not text:
            logger.debug("群聊旁听: 非文本消息，跳过")
            return
        if rule_check(text) == "IGNORE":
            logger.debug("群聊旁听: 无实质消息，跳过: %s", text[:20])
            return

        logger.info("群聊旁听 [%s] %s: %s", chat_id[-8:], msg.sender_name, text[:50])

        # 第二层：缓冲区
        if chat_id not in self.group_buffers:
            self.group_buffers[chat_id] = MessageBuffer()

        buf = self.group_buffers[chat_id]
        buf.add({
            "text": text,
            "sender_id": msg.sender_id,
            "sender_name": msg.sender_name,
            "message_id": msg.message_id,
            "chat_id": chat_id,
            "reply_to_id": msg.reply_to_id,
            "sender_type": msg.sender_type,
        })

        # 持久化到 session 文件（observe_only，不影响 LLM 上下文）
        self._log_group_message_to_session(msg)

        # bot 消息限流：防止 bot 间无限对话循环
        if msg.sender_type == SenderType.BOT:
            count = self._bot_poll_count.get(chat_id, 0)
            if count >= 2:
                logger.debug("群 %s bot 消息已达上限，跳过评估", chat_id[-8:])
                return
            self._bot_poll_count[chat_id] = count + 1

        if buf.should_evaluate():
            logger.info("群聊缓冲区已满 (%d条)，触发评估", buf._new_count)
            await self._evaluate_buffer(chat_id)
        else:
            # 消息数未达阈值，设置超时定时器确保安静群聊也能触发评估
            logger.info("群聊缓冲 %d/%d，%ds 后超时评估", buf._new_count, buf.eval_threshold, int(buf.max_age_seconds))
            loop = asyncio.get_running_loop()
            buf.schedule_timeout(loop, lambda cid=chat_id: asyncio.ensure_future(self._evaluate_buffer(cid)))

    def _log_group_message_to_session(self, msg: IncomingMessage) -> None:
        """将群聊消息以 observe_only 方式写入 session 文件，仅用于持久化记录。"""
        if not self.session_mgr or not msg.text:
            return
        session = self.session_mgr.get_or_create(msg.chat_id)
        session.add_message(
            "user", msg.text,
            sender_name=msg.sender_name or SENDER_UNKNOWN,
            observe_only=True,
        )

    async def _handle_group_at(self, msg: IncomingMessage) -> None:
        """处理群聊 @at 消息 — 必须回复"""
        text = msg.text
        has_images = bool(msg.image_keys)

        if not text and not has_images:
            if msg.message_type not in (MessageType.TEXT, MessageType.RICH_TEXT, MessageType.IMAGE):
                await self.adapter.send(OutgoingMessage(
                    msg.chat_id, NON_TEXT_REPLY_GROUP, reply_to=msg.message_id,
                ))
            return

        if not text and not has_images:
            # 空 @：从缓冲区取该用户最近的消息作为上下文
            buf = self.group_buffers.get(msg.chat_id)
            if buf:
                recent = buf.get_recent(20)
                sender_msgs = [m["text"] for m in recent if m["sender_id"] == msg.sender_id]
                if sender_msgs:
                    text = sender_msgs[-1]
                    logger.info("群聊 @at 空消息，取缓冲区上文: %s", text[:50])
            if not text:
                text = EMPTY_AT_FALLBACK

        log_preview = text[:50] if text else "[图片]"
        logger.info("群聊 @at [%s]: %s", msg.sender_name, log_preview)

        # 构建群聊上下文
        group_context = ""
        buf = self.group_buffers.get(msg.chat_id)
        if buf:
            recent = buf.get_recent(10)
            if recent:
                lines = []
                for m in recent:
                    name = m.get("sender_name", "未知")
                    if m.get("sender_id") == self.bot_open_id:
                        lines.append(f"{name}（你自己）：{m['text']}")
                    else:
                        lines.append(f"{name}：{m['text']}")
                group_context = "\n群聊近期消息：\n" + "\n".join(lines)

        system = self.memory.build_context(chat_id=msg.chat_id)
        system += (
            f"\n\n你在群聊中被 {msg.sender_name} @at 了。当前会话 chat_id={msg.chat_id}。请针对对方的问题简洁回复。"
            f"{group_context}"
            "\n如果用户要求记住什么，使用 write_memory 工具。"
            "如果涉及日程，使用 calendar 工具。"
            "需要联网查询时（搜索、天气、新闻等），使用 web_search / web_fetch 工具。"
            "需要计算或处理数据时，使用 run_python 工具。"
            "如果用户明确要求你执行某个任务且以上工具不够，可以用 create_custom_tool 创建工具来完成。"
            "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)
        )

        # 构建消息内容（可能包含图片）
        if has_images:
            content = await self._build_multimodal_content(msg, text or "")
        else:
            content = text

        if self.session_mgr:
            session = self.session_mgr.get_or_create(msg.chat_id)
            session.add_message("user", content, sender_name=msg.sender_name)
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": content}]

        # 添加 thinking reaction
        thinking_handle = await self.adapter.start_thinking(msg.message_id) or ""

        try:
            reply_text = await self._reply_with_tool_loop(
                system, messages, msg.chat_id, msg.message_id,
            )
        finally:
            if thinking_handle:
                await self.adapter.stop_thinking(msg.message_id, thinking_handle)

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(msg.chat_id)
            session.add_message("assistant", reply_text, sender_name="你")

        if reply_text:
            self._mark_topic_addressed(msg.chat_id, msg.message_id)

    # ── 缓冲区评估 + 介入 ──

    async def _evaluate_buffer(self, chat_id: str) -> None:
        """第三层：LLM 判断是否介入群聊"""
        if self._reply_is_busy(chat_id):
            logger.info("跳过评估: 群 %s 正在回复或冷却中", chat_id[-8:])
            return

        buf = self.group_buffers.get(chat_id)
        if not buf:
            return

        recent = buf.get_recent(10)
        if not recent:
            return

        # 话题归属检查：如果近期非自己的消息都属于已处理话题的引用链，跳过评估
        if self._is_topic_exhausted(chat_id, recent):
            logger.info("话题已被处理，跳过评估 %s", chat_id[-8:])
            return

        buf.mark_evaluated()

        # ── 协作信号预处理（独立 try-except，不阻塞主判断流程）──
        last_msg_id = ""
        reaction_id = ""
        collab_context = ""
        try:
            # 意图信号检查：如果其他 bot 正在思考，延迟让步
            thinking_bots = self._get_thinking_bots(chat_id)
            if thinking_bots:
                names = "、".join(thinking_bots)
                logger.info("%s 正在思考 %s，延迟评估", names, chat_id[-8:])
                self._record_collab_event(chat_id, "deferred", self.bot_name, f"让步给{names}")
                await asyncio.sleep(random.uniform(3, 5))
                recent = buf.get_recent(10)

            # 添加 thinking reaction 到最近一条非自己的消息
            last_msg_id = ""
            for m in reversed(recent):
                if m.get("sender_id") != self.bot_open_id and m.get("message_id"):
                    last_msg_id = m["message_id"]
                    break
            if last_msg_id:
                reaction_id = await self.adapter.start_thinking(last_msg_id) or ""
                if reaction_id:
                    self._my_reaction_ids[last_msg_id] = reaction_id

            # 随机 jitter 降低碰撞概率
            await asyncio.sleep(random.uniform(0, 1.5))

            # 注入协作记忆（具名 bot 协作历史）
            if self.memory:
                chat_mem = self.memory.read_chat_memory(chat_id)
                if chat_mem and "## 协作模式" in chat_mem:
                    section = chat_mem.split("## 协作模式", 1)[1]
                    next_section = section.find("\n## ")
                    if next_section != -1:
                        section = section[:next_section]
                    section = section.strip()
                    if section:
                        collab_context = f"\n\n近期协作记录：\n{section}\n根据历史模式和各助理的表现决定是否介入。"
        except Exception:
            logger.warning("协作信号预处理失败 chat=%s", chat_id[-8:], exc_info=True)

        soul = self.memory.read_soul()
        # 标注自己的消息，其他人（含 bot）正常记录名字
        my_name = self.bot_name
        lines: list[str] = []
        has_my_reply = False
        # 构建 msg_id → sender_name 映射，用于引用链展示
        id_to_name = {m["message_id"]: m.get("sender_name", SENDER_UNKNOWN) for m in recent}
        for m in recent:
            name = m.get("sender_name", SENDER_UNKNOWN)
            if m.get("sender_id") == self.bot_open_id:
                line = GROUP_MSG_WITH_ID_SELF.format(message_id=m['message_id'], name=name, text=m['text'])
                has_my_reply = True
            else:
                line = GROUP_MSG_WITH_ID_OTHER.format(message_id=m['message_id'], name=name, text=m['text'])
            reply_id = m.get("reply_to_id", "")
            if reply_id and reply_id in id_to_name:
                line += GROUP_MSG_REPLY_SUFFIX.format(reply_name=id_to_name[reply_id])
            lines.append(line)
        conversation = "\n".join(lines)

        # 如果最后一条消息就是自己发的，不需要再次介入
        if recent and recent[-1].get("sender_id") == self.bot_open_id:
            logger.info("最后一条消息是自己发的，跳过评估 %s", chat_id[-8:])
            return

        # 如果已经发言过，且之后没有任何新消息，不再介入
        if has_my_reply:
            my_last_idx = max(
                i for i, m in enumerate(recent)
                if m.get("sender_id") == self.bot_open_id
            )
            new_msgs_after = recent[my_last_idx + 1:]
            if not new_msgs_after:
                logger.info("已发言且无新消息，跳过评估 %s", chat_id[-8:])
                return

        prompt = GROUP_EVAL_PROMPT.format(
            bot_name=my_name or SENDER_UNKNOWN, soul=soul,
            conversation=conversation, collab_context=collab_context
        )

        try:
            result = await self.executor.quick_judge(prompt)
            result = result.strip()
            if result.startswith("```"):
                result = result.split("\n", 1)[-1].rsplit("```", 1)[0]
            # GLM-5 可能在 JSON 后附带额外文本，用 raw_decode 只取第一个对象
            try:
                judgment = json.loads(result)
            except json.JSONDecodeError:
                judgment, _ = json.JSONDecoder().raw_decode(result.lstrip())

            if judgment.get("should_intervene"):
                logger.info("决定介入群聊 %s: %s", chat_id, judgment.get("reason"))
                await self._intervene(chat_id, recent, judgment, last_msg_id)
            else:
                logger.info("不介入群聊 %s: %s", chat_id[-8:], judgment.get("reason"))
                # 不介入 → 清除 thinking reaction
                if last_msg_id and reaction_id:
                    await self.adapter.stop_thinking(last_msg_id, reaction_id)
                    self._my_reaction_ids.pop(last_msg_id, None)
                # 被动好奇心信号：从群聊对话中提取感兴趣的话题
                reason = judgment.get("reason", "")
                if reason and len(reason) > 10:
                    # 从评估 reason 中检测好奇心线索
                    self._extract_group_curiosity(chat_id, recent, reason)
        except Exception:
            logger.exception("介入判断失败")
            # 异常时也清除 reaction
            if last_msg_id and reaction_id:
                await self.adapter.stop_thinking(last_msg_id, reaction_id)
                self._my_reaction_ids.pop(last_msg_id, None)

    async def _intervene(
        self, chat_id: str, recent: list[dict], judgment: dict,
        thinking_msg_id: str = "",
    ) -> None:
        """执行群聊介入"""
        if self._reply_is_busy(chat_id):
            logger.info("跳过介入: 群 %s 正在回复或冷却中", chat_id[-8:])
            return

        system = self.memory.build_context(chat_id=chat_id)
        # 用真实名字构建对话上下文，标注 bot 消息
        lines: list[str] = []
        for m in recent:
            name = m.get("sender_name", SENDER_UNKNOWN)
            if m.get("sender_id") == self.bot_open_id:
                lines.append(GROUP_MSG_SELF.format(name=name, text=m['text']))
            else:
                lines.append(GROUP_MSG_OTHER.format(name=name, text=m['text']))
        conversation = "\n".join(lines)

        system += GROUP_INTERVENE_SYSTEM_SUFFIX.format(conversation=conversation)
        system += "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_GROUP)

        # 校验 reply_to_message_id
        reply_to = judgment.get("reply_to_message_id")
        valid_msg_ids = {m["message_id"] for m in recent}
        if not (reply_to and isinstance(reply_to, str) and reply_to in valid_msg_ids):
            reply_to = None

        if self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("user", f"[群聊旁听]\n{conversation}", sender_name="群聊")
            messages = session.get_messages()
        else:
            messages = [{"role": "user", "content": f"[群聊旁听]\n{conversation}"}]

        reply_text = await self._reply_with_tool_loop(
            system, messages, chat_id, reply_to,
            allow_nudge=False,
        )

        if reply_text and self.session_mgr:
            session = self.session_mgr.get_or_create(chat_id)
            session.add_message("assistant", reply_text, sender_name="你")

        if reply_text:
            reply_to = judgment.get("reply_to_message_id")
            if reply_to:
                self._mark_topic_addressed(chat_id, reply_to)
            self._record_collab_event(
                chat_id, "responded", self.bot_name,
                judgment.get("reason", "")[:50],
            )

        # 清除 thinking reaction（无论是否成功回复）
        if thinking_msg_id:
            rid = self._my_reaction_ids.pop(thinking_msg_id, "")
            if rid:
                await self.adapter.stop_thinking(thinking_msg_id, rid)

    # ── 话题归属 ──

    def _mark_topic_addressed(self, chat_id: str, message_id: str) -> None:
        """标记某条消息的话题已被处理（回复过），防止其他 bot 重复介入。"""
        if chat_id not in self._addressed_topics:
            self._addressed_topics[chat_id] = OrderedDict()
        topics = self._addressed_topics[chat_id]
        topics[message_id] = None
        # 只保留最近 20 个（按插入顺序淘汰最旧的）
        while len(topics) > 20:
            topics.popitem(last=False)

    def _is_topic_exhausted(self, chat_id: str, recent: list[dict]) -> bool:
        """最近的非自己消息是否都属于已处理话题的引用链。"""
        topics = self._addressed_topics.get(chat_id, {})
        if not topics:
            return False
        found_other = False
        # 从最后一条向前扫描，跳过自己的消息
        for m in reversed(recent):
            if m.get("sender_id") == self.bot_open_id:
                continue
            found_other = True
            reply_id = m.get("reply_to_id", "")
            if reply_id in topics:
                continue  # 这条是回复已处理话题
            return False  # 有非话题消息，不算 exhausted
        # 如果没找到任何非自己的消息，不算 exhausted
        return found_other

    # ── 协作事件记录 ──

    def _record_collab_event(
        self, chat_id: str, event_type: str, actor_name: str, detail: str = "",
    ) -> None:
        """记录协作事件到 chat_memory 的 ## 协作模式 section"""
        if not self.memory:
            return
        try:
            from datetime import datetime, timedelta, timezone
            cst = timezone(timedelta(hours=8))
            now = datetime.now(cst).strftime("%m-%d %H:%M")
            entry = f"- {now} {actor_name} {event_type}"
            if detail:
                entry += f": {detail}"

            path = self.memory.chat_memories_dir / f"{chat_id}.md"
            content = path.read_text(encoding="utf-8") if path.exists() else ""

            section_header = "## 协作模式"
            if section_header in content:
                # 提取现有 section 内容
                parts = content.split(section_header, 1)
                before = parts[0]
                after = parts[1]
                # 找到下一个 ## 或文件结尾
                next_section = after.find("\n## ")
                if next_section != -1:
                    section_body = after[:next_section]
                    rest = after[next_section:]
                else:
                    section_body = after
                    rest = ""
                # 解析已有条目，保留最近 19 条 + 新条目 = 20 条
                body_lines = [l for l in section_body.strip().split("\n") if l.startswith("- ")]
                body_lines = body_lines[-19:]  # 保留最近 19 条
                body_lines.append(entry)
                content = before + section_header + "\n" + "\n".join(body_lines) + "\n" + rest
            else:
                content = content.rstrip() + f"\n\n{section_header}\n{entry}\n"

            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.debug("协作事件: %s", entry)
        except Exception:
            logger.warning("记录协作事件失败", exc_info=True)

    # ── 成员变更 + 会话压缩 + 卡片回调 ──

    async def _handle_member_change(self, data: dict) -> None:
        """处理群成员变更事件"""
        chat_id = data.get("chat_id", "")
        change_type = data.get("change_type", "")
        if not chat_id:
            return

        if change_type == "bot_joined":
            # Bot 被加入群聊 → 自我介绍
            try:
                context = self.memory.build_context(chat_id=chat_id)
                system = BOT_SELF_INTRO_SYSTEM.format(soul=context)
                intro = await self.executor.reply(system, BOT_SELF_INTRO_USER)
                intro = intro.strip()
                if intro:
                    await self.adapter.send(OutgoingMessage(chat_id, intro))
                    logger.info("Bot 入群自我介绍已发送: %s -> %s", chat_id[-8:], intro[:50])
            except Exception:
                logger.exception("Bot 入群自我介绍失败: %s", chat_id[-8:])

        elif change_type == "user_joined":
            # 新用户入群 → 欢迎
            users = data.get("users", [])
            if not users:
                return
            names = [u.get("name") or u.get("user_id", "")[-6:] for u in users]
            if not names:
                return
            try:
                user_names = "、".join(names)
                context = self.memory.build_context(chat_id=chat_id)
                system = USER_WELCOME_SYSTEM.format(soul=context, user_names=user_names)
                welcome = await self.executor.reply(system, USER_WELCOME_USER)
                welcome = welcome.strip()
                if welcome:
                    await self.adapter.send(OutgoingMessage(chat_id, welcome))
                    logger.info("用户入群欢迎已发送: %s -> %s", chat_id[-8:], welcome[:50])
            except Exception:
                logger.exception("用户入群欢迎失败: %s", chat_id[-8:])

        elif change_type == "bot_left":
            # Bot 被移出群聊 → 清理内部状态
            self._thinking_signals.pop(chat_id, None)
            self.group_buffers.pop(chat_id, None)
            self._addressed_topics.pop(chat_id, None)
            logger.info("Bot 已退出群 %s，清理完成", chat_id[-8:])

    async def _compact_session(self, session: Any) -> None:
        """压缩会话：提取长期记忆到 chat_memory，生成结构化摘要后裁剪消息"""
        # 仅对将被压缩的旧消息做记忆提取
        old_messages = session.get_compaction_context()
        if old_messages:
            flush_prompt = self.memory.flush_before_compaction(old_messages)
            extracted = await self.executor.reply("", flush_prompt)
            if extracted.strip() and extracted.strip() != FLUSH_NO_RESULT:
                self.memory.append_daily(
                    COMPACTION_DAILY_HEADER.format(extracted=extracted),
                    chat_id=session.chat_id,
                )
                # 同时写入 per-chat 长期记忆，避免 daily log 过期后丢失
                from datetime import date as _date
                self.memory.append_chat_memory(
                    session.chat_id,
                    COMPACTION_MEMORY_HEADER.format(date=_date.today().isoformat(), extracted=extracted),
                )

        # 生成结构化摘要
        summary_prompt = COMPACTION_SUMMARY_PROMPT.format(
            old_messages_formatted="\n".join(
                f"[{m.get('role', '?')}] {m.get('content', '')[:200]}"
                for m in old_messages
            )
        )
        summary = await self.executor.reply("", summary_prompt)
        session.compact(summary)

