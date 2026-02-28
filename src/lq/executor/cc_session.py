"""ClaudeCodeSession — SDK 驱动的交互式 Claude Code 执行器"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lq.config import APIConfig
from lq.executor.cc_experience import CCExperienceEntry, CCExperienceStore
from lq.memory import MemoryManager
from lq.platform import PlatformAdapter, OutgoingMessage

logger = logging.getLogger(__name__)

# ── 安全分类常量 ──

_SAFE_TOOLS = frozenset({
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch",
    "NotebookRead", "TodoRead", "TaskList", "TaskGet",
})

_DANGEROUS_BASH_PATTERNS = (
    "git push", "rm -rf", "chmod", "sudo", "mkfs", "dd if=",
    "shutdown", "reboot", "halt", "poweroff",
    "> /dev/", "curl.*|.*sh", "wget.*|.*sh",
)

_MAX_TEXT_OUTPUT = 2000  # 文本输出截断长度


# ── 执行追踪 ──

class CCExecutionTrace:
    """收集一次 CC 执行的全部事件"""

    def __init__(self) -> None:
        self.text_outputs: list[str] = []
        self.tool_calls: list[dict] = []
        self.tool_results: dict[str, dict] = {}  # tool_use_id → result info
        self.thinking_blocks: list[str] = []
        self.errors: list[str] = []
        self.approvals: list[dict] = []
        self.tools_used: set[str] = set()
        self.files_modified: set[str] = set()
        self.start_time: float = time.time()

    def add_text(self, text: str) -> None:
        self.text_outputs.append(text)

    def add_tool_use(self, name: str, input_data: dict) -> None:
        self.tools_used.add(name)
        self.tool_calls.append({
            "name": name,
            "input_summary": _summarize_input(name, input_data),
        })
        # 追踪文件修改
        if name in ("Write", "Edit", "NotebookEdit"):
            path = input_data.get("file_path", "") or input_data.get("notebook_path", "")
            if path:
                self.files_modified.add(path)

    def add_tool_result(self, tool_use_id: str, content: Any) -> None:
        summary = str(content)[:200] if content else ""
        self.tool_results[tool_use_id] = {"summary": summary}

    def add_thinking(self, text: str) -> None:
        self.thinking_blocks.append(text)

    def add_approval(self, tool: str, action: str, decision: str, by: str) -> None:
        self.approvals.append({
            "tool": tool, "action": action, "decision": decision, "by": by,
        })

    def build_summary(self) -> str:
        """生成执行过程的浓缩叙述"""
        parts: list[str] = []
        if self.tool_calls:
            tool_names = [tc["name"] for tc in self.tool_calls]
            parts.append(f"使用工具: {', '.join(dict.fromkeys(tool_names))}")
        if self.files_modified:
            parts.append(f"修改文件: {', '.join(sorted(self.files_modified))}")
        if self.errors:
            parts.append(f"错误: {'; '.join(self.errors[:3])}")
        if self.approvals:
            approved = sum(1 for a in self.approvals if a["decision"] == "allow")
            denied = sum(1 for a in self.approvals if a["decision"] == "deny")
            parts.append(f"审批: {approved}通过, {denied}拒绝")
        return " | ".join(parts) if parts else "无操作"

    @property
    def duration_ms(self) -> int:
        return int((time.time() - self.start_time) * 1000)


@dataclass
class CCExecutionResult:
    """CC 执行结果"""
    success: bool = False
    output: str = ""
    error: str = ""
    session_id: str = ""
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    tools_used: list[str] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    trace_summary: str = ""
    can_resume: bool = False

    def to_dict(self) -> dict:
        """兼容旧格式的字典输出"""
        return {
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "session_id": self.session_id,
            "cost_usd": self.cost_usd,
            "duration_ms": self.duration_ms,
            "num_turns": self.num_turns,
            "tools_used": self.tools_used,
            "files_modified": self.files_modified,
            "trace_summary": self.trace_summary,
            "can_resume": self.can_resume,
        }


# ── 进度报告器 ──

class _ProgressReporter:
    """累积工具调用，批量报告到 adapter（避免消息洪水）"""

    BATCH_SIZE = 3
    FLUSH_INTERVAL = 10.0  # 秒

    def __init__(self, adapter: PlatformAdapter, chat_id: str) -> None:
        self._adapter = adapter
        self._chat_id = chat_id
        self._pending: list[str] = []
        self._last_flush: float = time.time()

    async def report_tool_use(self, name: str, input_summary: str) -> None:
        icon = "🔍" if name in _SAFE_TOOLS else "🔧"
        self._pending.append(f"{icon} {name}: {input_summary}")
        if len(self._pending) >= self.BATCH_SIZE or self._should_flush():
            await self.flush()

    async def report_approval(self, tool: str, decision: str, by: str) -> None:
        icon = "✓" if decision == "allow" else "✗"
        label = "LLM" if by == "llm" else "人工"
        line = f"{icon} {label}审批 {tool}: {'允许' if decision == 'allow' else '拒绝'}"
        self._pending.append(line)
        await self.flush()

    async def report_completion(self, result: CCExecutionResult) -> None:
        await self.flush()  # 先发送累积的
        status = "✅ 完成" if result.success else "❌ 失败"
        parts = [f"**CC {status}**"]
        if result.cost_usd > 0:
            parts.append(f"成本: ${result.cost_usd:.4f}")
        if result.files_modified:
            parts.append(f"修改: {', '.join(result.files_modified)}")
        if result.trace_summary:
            parts.append(result.trace_summary)
        if result.error:
            parts.append(f"错误: {result.error[:200]}")
        text = " | ".join(parts)
        try:
            await self._adapter.send(OutgoingMessage(self._chat_id, text))
        except Exception:
            logger.debug("完成报告发送失败", exc_info=True)

    async def flush(self) -> None:
        if not self._pending:
            return
        text = "**CC 进度**\n" + "\n".join(self._pending)
        self._pending.clear()
        self._last_flush = time.time()
        try:
            await self._adapter.send(OutgoingMessage(self._chat_id, text))
        except Exception:
            logger.debug("进度报告发送失败", exc_info=True)

    def _should_flush(self) -> bool:
        return time.time() - self._last_flush > self.FLUSH_INTERVAL


# ── 主类 ──

class ClaudeCodeSession:
    """SDK 驱动的交互式 CC 执行器"""

    def __init__(
        self,
        workspace: Path,
        api_config: APIConfig,
        adapter: PlatformAdapter,
        experience_store: CCExperienceStore,
        memory: MemoryManager,
        executor: Any = None,  # DirectAPIExecutor, for LLM approval
    ) -> None:
        self.workspace = workspace
        self.api_config = api_config
        self.adapter = adapter
        self.experience = experience_store
        self.memory = memory
        self.executor = executor
        # 活跃 session 追踪（用于中途打断）
        self._active_clients: dict[str, Any] = {}  # chat_id → ClaudeSDKClient
        # 审批等待队列
        self._pending_approvals: dict[str, asyncio.Future] = {}

    async def execute(
        self,
        prompt: str,
        chat_id: str,
        context: str = "",
        working_dir: str = "",
        timeout: int = 300,
        max_budget_usd: float = 0.5,
        session_id: str | None = None,
    ) -> CCExecutionResult:
        """执行 CC 任务，带流式事件处理和分层审批"""
        # 构建干净的子进程环境（移除 CLAUDECODE 防止嵌套误判）
        clean_env: dict[str, str] = {
            k: v for k, v in os.environ.items() if k != "CLAUDECODE"
        }

        try:
            from claude_agent_sdk import (
                ClaudeSDKClient,
                ClaudeAgentOptions,
                AssistantMessage,
                ResultMessage,
                TextBlock,
                ToolUseBlock,
                ToolResultBlock,
                ThinkingBlock,
            )
        except ImportError:
            return CCExecutionResult(
                error="claude-agent-sdk 未安装，请运行: uv sync",
            )

        trace = CCExecutionTrace()
        reporter = _ProgressReporter(self.adapter, chat_id)
        cwd = working_dir or str(self.workspace)

        # 构建记忆上下文
        memory_context = self._build_memory_context(chat_id)
        enriched_prompt = self._build_enriched_prompt(prompt, chat_id, context)

        # 创建权限回调
        async def permission_handler(
            tool_name: str,
            tool_input: dict[str, Any],
            _context: Any,
        ) -> Any:
            from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
            risk = self._classify_risk(tool_name, tool_input, cwd)
            if risk == "safe":
                trace.add_approval(tool_name, _summarize_input(tool_name, tool_input), "allow", "auto")
                return PermissionResultAllow()
            elif risk == "normal":
                decision = await self._llm_approval(tool_name, tool_input, prompt)
                trace.add_approval(tool_name, _summarize_input(tool_name, tool_input), decision, "llm")
                await reporter.report_approval(tool_name, decision, "llm")
                if decision == "allow":
                    return PermissionResultAllow()
                else:
                    return PermissionResultDeny(message="LLM 审批拒绝此操作")
            else:  # dangerous
                decision = await self._human_approval(tool_name, tool_input, chat_id)
                trace.add_approval(tool_name, _summarize_input(tool_name, tool_input), decision, "human")
                await reporter.report_approval(tool_name, decision, "human")
                if decision == "allow":
                    return PermissionResultAllow()
                else:
                    return PermissionResultDeny(message="人工审批拒绝此操作")

        # SDK 配置
        def _stderr_handler(line: str) -> None:
            logger.debug("CC stderr: %s", line.rstrip())

        options = ClaudeAgentOptions(
            system_prompt={
                "type": "preset",
                "preset": "claude_code",
                "append": memory_context,
            } if memory_context else {
                "type": "preset",
                "preset": "claude_code",
            },
            permission_mode="acceptEdits",
            cwd=cwd,
            max_budget_usd=max_budget_usd,
            can_use_tool=permission_handler,
            resume=session_id,
            env=clean_env,
            stderr=_stderr_handler,
        )

        # 设置 thinking emoji
        try:
            await self.adapter.start_thinking(chat_id)
        except Exception:
            pass

        result = CCExecutionResult()

        try:
            async with ClaudeSDKClient(options=options) as client:
                self._active_clients[chat_id] = client

                await client.query(enriched_prompt)

                # 用 wait_for 包裹一个消费协程（非 async generator）来实现超时
                async def _consume_with_result() -> ResultMessage | None:
                    async for message in client.receive_response():
                        if isinstance(message, AssistantMessage):
                            for block in message.content:
                                if isinstance(block, TextBlock):
                                    trace.add_text(block.text)
                                elif isinstance(block, ToolUseBlock):
                                    trace.add_tool_use(block.name, block.input)
                                    await reporter.report_tool_use(
                                        block.name,
                                        _summarize_input(block.name, block.input),
                                    )
                                elif isinstance(block, ToolResultBlock):
                                    trace.add_tool_result(block.tool_use_id, block.content)
                                elif isinstance(block, ThinkingBlock):
                                    trace.add_thinking(block.thinking)
                        elif isinstance(message, ResultMessage):
                            return message
                    return None

                result_msg = await asyncio.wait_for(
                    _consume_with_result(), timeout=timeout,
                )
                if result_msg is not None:
                    result.success = not result_msg.is_error
                    result.session_id = result_msg.session_id
                    result.cost_usd = result_msg.total_cost_usd or 0.0
                    result.duration_ms = result_msg.duration_ms
                    result.num_turns = result_msg.num_turns
                    result.can_resume = True
                    if result_msg.result:
                        result.output = result_msg.result

        except asyncio.TimeoutError:
            result.error = f"CC 执行超时 ({timeout}s)"
            trace.errors.append(result.error)
            # 尝试中断
            client_ref = self._active_clients.get(chat_id)
            if client_ref:
                try:
                    await client_ref.interrupt()
                except Exception:
                    pass
        except Exception as e:
            result.error = str(e)
            trace.errors.append(str(e))
            logger.exception("CC 执行异常")
        finally:
            self._active_clients.pop(chat_id, None)
            try:
                await self.adapter.stop_thinking(chat_id)
            except Exception:
                pass

        # 填充追踪信息
        result.tools_used = sorted(trace.tools_used)
        result.files_modified = sorted(trace.files_modified)
        result.trace_summary = trace.build_summary()
        if not result.output and trace.text_outputs:
            result.output = "\n".join(trace.text_outputs)[-_MAX_TEXT_OUTPUT:]

        # 发送完成报告
        await reporter.report_completion(result)

        # 记录经验
        await self._record_experience(trace, result, prompt, cwd)

        return result

    async def interrupt(self, chat_id: str) -> None:
        """中途打断正在执行的 CC"""
        client = self._active_clients.get(chat_id)
        if client:
            try:
                await client.interrupt()
                logger.info("CC 已中断: chat=%s", chat_id[-8:])
            except Exception:
                logger.exception("CC 中断失败")

    def resolve_approval(self, approval_id: str, approved: bool) -> None:
        """解析人工审批结果（由卡片回调触发）"""
        future = self._pending_approvals.pop(approval_id, None)
        if future and not future.done():
            future.set_result("allow" if approved else "deny")

    # ── 分层审批 ──

    def _classify_risk(self, tool_name: str, tool_input: dict, workspace_dir: str) -> str:
        """分类工具调用风险: safe / normal / dangerous"""
        if tool_name in _SAFE_TOOLS:
            return "safe"

        if tool_name == "Bash":
            command = tool_input.get("command", "")
            cmd_lower = command.lower()
            for pattern in _DANGEROUS_BASH_PATTERNS:
                if pattern in cmd_lower:
                    return "dangerous"
            return "normal"

        if tool_name in ("Write", "Edit", "NotebookEdit"):
            path = tool_input.get("file_path", "") or tool_input.get("notebook_path", "")
            if path and not self._is_within_workspace(path, workspace_dir):
                return "dangerous"
            return "normal"

        # 其他未知工具默认普通
        return "normal"

    @staticmethod
    def _is_within_workspace(path: str, workspace_dir: str) -> bool:
        """检查路径是否在工作区内"""
        try:
            resolved = Path(path).resolve()
            workspace = Path(workspace_dir).resolve()
            return str(resolved).startswith(str(workspace))
        except Exception:
            return False

    async def _llm_approval(
        self, tool_name: str, tool_input: dict, original_prompt: str,
    ) -> str:
        """LLM 快速审判：判断操作是否符合原始任务意图"""
        if not self.executor:
            return "allow"  # 无 executor 时降级放行

        input_summary = _summarize_input(tool_name, tool_input)
        approval_prompt = (
            f"用户的原始任务: {original_prompt[:500]}\n\n"
            f"Claude Code 想要执行: {tool_name}({input_summary})\n\n"
            "这个操作是否合理且安全？回答 ALLOW 或 DENY 或 UNCERTAIN（拿不准）。"
            "只回答一个词。"
        )

        try:
            response = await self.executor.reply("", approval_prompt, max_tokens=32)
            response = response.strip().upper()
            if "DENY" in response:
                return "deny"
            if "UNCERTAIN" in response:
                # 升级为拿不准 → 放行但记录
                logger.info("LLM 审批不确定: %s(%s)", tool_name, input_summary)
                return "allow"
            return "allow"
        except Exception:
            logger.debug("LLM 审批调用失败，降级放行", exc_info=True)
            return "allow"

    async def _human_approval(
        self, tool_name: str, tool_input: dict, chat_id: str,
    ) -> str:
        """人工审批：发送卡片等待确认"""
        import uuid
        approval_id = f"cc_{uuid.uuid4().hex[:8]}"
        input_summary = _summarize_input(tool_name, tool_input)
        action_desc = f"Claude Code 请求执行高危操作:\n\n**{tool_name}**: {input_summary}"

        # 创建审批 future
        loop = asyncio.get_running_loop()
        future: asyncio.Future[str] = loop.create_future()
        self._pending_approvals[approval_id] = future

        # 发送审批卡片
        card = {
            "type": "confirm",
            "title": "CC 高危操作审批",
            "content": action_desc,
            "confirm_text": "允许",
            "cancel_text": "拒绝",
            "callback_data": {"type": "approval", "id": approval_id},
        }
        try:
            await self.adapter.send(OutgoingMessage(chat_id, card=card))
        except Exception:
            logger.warning("审批卡片发送失败，降级拒绝")
            self._pending_approvals.pop(approval_id, None)
            return "deny"

        # 等待人工回复（超时 120 秒自动拒绝）
        try:
            decision = await asyncio.wait_for(future, timeout=120)
            return decision
        except asyncio.TimeoutError:
            self._pending_approvals.pop(approval_id, None)
            logger.info("人工审批超时，自动拒绝: %s", approval_id)
            return "deny"

    # ── 记忆整合 ──

    def _build_memory_context(self, chat_id: str) -> str:
        """整合 Agent 的完整知识，注入 CC 的 system prompt"""
        parts: list[str] = []

        # 1. SOUL.md 摘要
        soul = self.memory.read_soul()
        if soul:
            # 只取前 500 字符作为摘要
            soul_summary = soul[:500]
            if len(soul) > 500:
                soul_summary += "\n..."
            parts.append(f"## 关于我\n{soul_summary}")

        # 2. MEMORY.md 全局记忆
        global_memory = self.memory.read_memory()
        if global_memory:
            parts.append(f"## 我的记忆\n{global_memory}")

        # 3. 聊天专属记忆
        if chat_id:
            chat_memory = self.memory.read_chat_memory(chat_id)
            if chat_memory:
                parts.append(f"## 与当前用户的记忆\n{chat_memory}")

        return "\n\n".join(parts)

    def _build_enriched_prompt(
        self, prompt: str, chat_id: str, context: str,
    ) -> str:
        """构建包含经验和上下文的任务 prompt"""
        parts: list[str] = []

        # CC 执行经验
        similar = self.experience.query_similar(prompt, limit=3)
        if similar:
            exp_lines: list[str] = []
            for entry in similar:
                status = "成功" if entry.success else "失败"
                line = f"- 任务: \"{entry.prompt[:80]}\" | {status}"
                if entry.lessons:
                    line += f" | 经验: {entry.lessons[:100]}"
                exp_lines.append(line)
            parts.append("## CC 执行经验（相似任务）\n" + "\n".join(exp_lines))

        # 对话上下文
        if context:
            parts.append(f"## 当前对话上下文\n{context}")

        # 任务
        parts.append(f"## 任务\n{prompt}")

        return "\n\n".join(parts)

    # ── 经验记录 ──

    async def _record_experience(
        self,
        trace: CCExecutionTrace,
        result: CCExecutionResult,
        prompt: str,
        working_dir: str,
    ) -> None:
        """记录本次执行经验"""
        entry = CCExperienceEntry(
            timestamp=trace.start_time,
            session_id=result.session_id,
            prompt=prompt,
            working_dir=working_dir,
            success=result.success,
            cost_usd=result.cost_usd,
            duration_ms=result.duration_ms,
            num_turns=result.num_turns,
            tools_used=sorted(trace.tools_used),
            tool_calls=trace.tool_calls,
            files_modified=sorted(trace.files_modified),
            text_outputs=[t[:500] for t in trace.text_outputs[-5:]],
            errors=trace.errors,
            approvals=trace.approvals,
            trace_summary=trace.build_summary(),
        )

        # 经验提取（快速 LLM 调用）
        if self.executor and (result.success or trace.errors):
            try:
                lessons_prompt = (
                    f"根据这次 Claude Code 执行:\n"
                    f"任务: {prompt[:200]}\n"
                    f"过程: {trace.build_summary()}\n"
                    f"结果: {'成功' if result.success else '失败'}\n"
                    f"{'错误: ' + '; '.join(trace.errors[:3]) if trace.errors else ''}\n\n"
                    "有什么经验值得下次记住？只提炼可操作的教训，一两句话。"
                    "如果没有特别的教训，回答「无」。"
                )
                lessons = await self.executor.reply("", lessons_prompt, max_tokens=256)
                lessons = lessons.strip()
                if lessons and lessons != "无":
                    entry.lessons = lessons
                    # 写入 daily log
                    self.memory.append_daily(
                        f"- CC 执行: {prompt[:60]} → "
                        f"{'成功' if result.success else '失败'} | 经验: {lessons[:100]}\n"
                    )
            except Exception:
                logger.debug("经验提取失败", exc_info=True)

        self.experience.record(entry)


# ── 工具函数 ──

def _summarize_input(tool_name: str, tool_input: dict) -> str:
    """生成工具调用输入的简短摘要"""
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        return cmd[:80] if cmd else "(空命令)"
    if tool_name in ("Write", "Edit"):
        path = tool_input.get("file_path", "?")
        return path
    if tool_name == "Read":
        return tool_input.get("file_path", "?")
    if tool_name in ("Glob", "Grep"):
        pattern = tool_input.get("pattern", "?")
        return pattern[:60]
    # 通用：取第一个字符串值
    for v in tool_input.values():
        if isinstance(v, str) and v:
            return v[:60]
    return "(无参数)"
