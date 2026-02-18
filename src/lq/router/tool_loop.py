"""LLM 工具调用循环 + 审批机制 + 主人身份发现"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from lq.platform import OutgoingMessage
from lq.prompts import ACTION_NUDGE

logger = logging.getLogger(__name__)


class ToolLoopMixin:
    """LLM agentic 工具调用循环、审批系统、主人身份自动发现。"""

    async def _reply_with_tool_loop(
        self,
        system: str,
        messages: list[dict],
        chat_id: str,
        reply_to_message_id: str,
        text_transform: Any = None,
        allow_nudge: bool = True,
    ) -> str:
        """执行带工具调用的完整对话循环。

        支持更长的工具调用链（最多 20 轮），适应 Claude Code 和 Bash
        等需要多步骤执行的复杂任务。工具调用记录会写入会话历史。
        Per-chat 互斥锁确保同一群聊不会并发回复。
        """
        lock = self._get_reply_lock(chat_id)
        if lock.locked():
            logger.info("跳过回复: 群 %s 已有回复进行中", chat_id[-8:])
            return ""
        async with lock:
            return await self._reply_with_tool_loop_inner(
                system, messages, chat_id, reply_to_message_id,
                text_transform, allow_nudge,
            )

    async def _reply_with_tool_loop_inner(
        self,
        system: str,
        messages: list[dict],
        chat_id: str,
        reply_to_message_id: str,
        text_transform: Any = None,
        allow_nudge: bool = True,
    ) -> str:
        """_reply_with_tool_loop 的实际实现（已持锁）。"""
        all_tools = self._build_all_tools()
        tool_names = [t["name"] for t in all_tools]
        logger.debug("工具循环开始: chat=%s 共 %d 个工具 %s", chat_id[-8:], len(all_tools), tool_names)
        resp = await self.executor.reply_with_tools(system, messages, all_tools)

        # 复杂任务（如 Claude Code 执行）可能需要更多轮次
        max_iterations = 20
        iteration = 0
        nudge_count = 0
        tools_called: list[str] = []
        sent_to_current_chat = False  # 是否已通过 send_message 向当前 chat 发送过

        while iteration < max_iterations:
            iteration += 1

            if resp.pending and resp.tool_calls:
                # LLM 调用了工具 → 执行并继续
                tool_results = []
                for tc in resp.tool_calls:
                    tools_called.append(tc["name"])
                    # 记录工具调用到会话历史
                    if self.session_mgr:
                        session = self.session_mgr.get_or_create(chat_id)
                        session.add_tool_use(tc["name"], tc["input"], tc["id"])
                    result = await self._execute_tool(tc["name"], tc["input"], chat_id)
                    # 记录工具调用统计
                    self._track_tool_result(
                        tc["name"],
                        result.get("success", True),
                        result.get("error", ""),
                    )
                    # 标记是否已通过 send_message 向当前 chat 发送过
                    if tc["name"] == "send_message" and result.get("success"):
                        target = tc["input"].get("chat_id", "")
                        if not target or target == chat_id:
                            sent_to_current_chat = True
                    result_str = json.dumps(result, ensure_ascii=False)
                    tool_results.append({
                        "tool_use_id": tc["id"],
                        "content": result_str,
                    })
                    # 记录工具结果到会话历史
                    if self.session_mgr:
                        session = self.session_mgr.get_or_create(chat_id)
                        session.add_tool_result(tc["id"], result_str)

                # 工具执行后刷新工具列表（可能有新工具被创建）
                all_tools = self._build_all_tools()
                # 创建自定义工具后失效自我认知缓存
                if "create_custom_tool" in tools_called or "delete_custom_tool" in tools_called:
                    self.memory.invalidate_awareness_cache()
                resp = await self.executor.continue_after_tools(
                    system, resp.messages, all_tools, tool_results, resp.raw_response
                )
            elif (
                allow_nudge
                and resp.text
                and nudge_count < 1
                and self._is_action_preamble(resp.text)
            ):
                nudge_count += 1
                logger.info(
                    "检测到行动前奏，催促执行 (%d/1) 原文: %s",
                    nudge_count, resp.text[:100],
                )
                continued_messages = resp.messages + [
                    {"role": "user", "content": ACTION_NUDGE}
                ]
                resp = await self.executor.reply_with_tools(
                    system, continued_messages, all_tools
                )
            else:
                break

        # 发送最终文本回复（如果已通过 send_message 发到当前 chat 则跳过，避免重复）
        if resp.text and not sent_to_current_chat:
            # 先清理 LLM 模仿的元数据标签，再做 transform
            cleaned = self._CLEAN_RE.sub("", resp.text).strip()
            final = text_transform(cleaned) if text_transform else cleaned
            logger.info("回复: %s", final[:80])
            await self._send_reply(final, chat_id, reply_to_message_id)
            resp.text = final
        elif sent_to_current_chat:
            logger.info("跳过最终回复: 已通过 send_message 发送到当前 chat")

        # 后处理：检测未执行的意图并补救
        if self.post_processor and resp.text:
            original_user_msg = ""
            for m in reversed(messages):
                if m.get("role") == "user" and isinstance(m.get("content"), str):
                    original_user_msg = m["content"]
                    break
            if original_user_msg:
                try:
                    await self.post_processor.process(
                        original_user_msg, resp.text, tools_called,
                        chat_id, reply_to_message_id,
                    )
                except Exception:
                    logger.exception("PostProcessor failed")

        self._reply_cooldown_ts[chat_id] = time.time()
        return resp.text

    async def _send_tool_notification(
        self, text: str, chat_id: str, reply_to_message_id: str | None,
    ) -> None:
        """发送工具执行通知（卡片消息）。"""
        card = {"type": "info", "title": "", "content": text}
        try:
            reply_to = ""
            if reply_to_message_id and not reply_to_message_id.startswith("inbox_"):
                reply_to = reply_to_message_id
            if chat_id and chat_id != "local_cli":
                await self.adapter.send(OutgoingMessage(chat_id, text, reply_to=reply_to, card=card))
        except Exception:
            logger.exception("工具通知发送失败")

    # ── 审批机制 ──

    async def _request_owner_approval(
        self, action_desc: str, callback_id: str,
    ) -> None:
        """向主人发送审批卡片"""
        owner_chat_id = ""
        if self.config:
            owner_chat_id = self.config.feishu.owner_chat_id
        if not owner_chat_id:
            logger.warning("无法发送审批: 未配置 owner_chat_id")
            return

        card = {
            "type": "confirm",
            "title": "操作审批",
            "content": action_desc,
            "confirm_text": "批准",
            "cancel_text": "拒绝",
            "callback_data": {"type": "approval", "id": callback_id},
        }
        await self.adapter.send(OutgoingMessage(owner_chat_id, card=card))

        # 记录待审批
        import json as _json
        log_dir = self.memory.workspace / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "pending-approvals.jsonl"
        entry = {
            "id": callback_id,
            "ts": time.time(),
            "action": action_desc,
            "status": "pending",
        }
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(entry, ensure_ascii=False) + "\n")
        logger.info("审批请求已发送: %s", callback_id)

    def _update_approval_status(self, callback_id: str, status: str) -> None:
        """更新审批记录状态"""
        import json as _json

        log_dir = self.memory.workspace / "logs"
        log_path = log_dir / "pending-approvals.jsonl"
        if not log_path.exists():
            return
        try:
            lines = log_path.read_text(encoding="utf-8").strip().splitlines()
            updated = []
            for line in lines:
                entry = _json.loads(line)
                if entry.get("id") == callback_id:
                    entry["status"] = status
                    entry["resolved_ts"] = time.time()
                updated.append(_json.dumps(entry, ensure_ascii=False))
            log_path.write_text("\n".join(updated) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("更新审批状态失败", exc_info=True)

    def _check_approval(self, callback_id: str) -> str | None:
        """检查审批状态，返回 'approved'/'rejected'/None(pending)"""
        import json as _json

        log_dir = self.memory.workspace / "logs"
        log_path = log_dir / "pending-approvals.jsonl"
        if not log_path.exists():
            return None
        try:
            for line in log_path.read_text(encoding="utf-8").strip().splitlines():
                entry = _json.loads(line)
                if entry.get("id") == callback_id:
                    status = entry.get("status", "pending")
                    return status if status != "pending" else None
        except Exception:
            pass
        return None

    # ── 主人身份自动发现 ──

    def _try_discover_owner(self, chat_id: str, sender_name: str) -> None:
        """尝试自动发现主人身份（首个私聊用户或名字匹配的用户）"""
        if not self.config:
            return
        # 已有 owner_chat_id，不需要发现
        if self.config.feishu.owner_chat_id:
            return
        # 如果配置了 owner_name，只匹配该名字
        if self.config.owner_name:
            if sender_name != self.config.owner_name:
                return
        # 设置 owner_chat_id（首个私聊用户或名字匹配的用户）
        self.config.feishu.owner_chat_id = chat_id
        if not self.config.owner_name:
            self.config.owner_name = sender_name
        # 持久化到 config.json
        try:
            from lq.config import save_config
            save_config(self.memory.workspace, self.config)
            logger.info("主人身份已发现并保存: %s (chat_id: %s)", sender_name, chat_id[-8:])
        except Exception:
            logger.warning("主人身份保存失败", exc_info=True)
        # 刷新自我认知缓存
        self.memory.invalidate_awareness_cache()
