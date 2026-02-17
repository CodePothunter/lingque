"""本地交互式对话 — 不依赖飞书，直接在终端与灵雀对话

走标准事件流：stdin → IncomingMessage → router.handle() → adapter.send() → stdout
与飞书模式使用同一条代码路径，仅适配器不同。
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any

from lq.config import LQConfig
from lq.platform import (
    PlatformAdapter,
    BotIdentity,
    ChatMember,
    IncomingMessage,
    OutgoingMessage,
    ChatType,
    SenderType,
    MessageType,
)

logger = logging.getLogger(__name__)

# 本地对话使用的 chat_id
LOCAL_CHAT_ID = "local_say"


class LocalAdapter(PlatformAdapter):
    """本地终端适配器 — 实现 PlatformAdapter，将消息输出到终端。

    输入侧：由 run_conversation 构造 IncomingMessage 投入 queue。
    输出侧：adapter.send() 打印到 stdout。
    同步机制：start_thinking 返回 truthy handle，使 router 的 finally 块
    调用 stop_thinking → 设置 _turn_done 事件，通知对话循环本轮结束。
    """

    def __init__(self, bot_name: str) -> None:
        self._bot_name = bot_name
        # 对话轮次完成信号（stop_thinking 设置，conversation loop 等待）
        self._turn_done: asyncio.Event = asyncio.Event()

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        return BotIdentity(bot_id="local_bot", bot_name=self._bot_name)

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        self._queue = queue

    async def disconnect(self) -> None:
        pass

    # ── 表达 ──

    async def send(self, message: OutgoingMessage) -> str | None:
        if message.card:
            content = _extract_card_text(message.card)
            if content:
                _print_bot(self._bot_name, content, prefix="卡片")
        elif message.text:
            _print_bot(self._bot_name, message.text)
        return "local_msg"

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        # 返回 truthy handle，确保 router finally 块中的 stop_thinking 被调用
        return "local"

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        # 信号：本轮处理（含 LLM 回复和发送）已完成
        self._turn_done.set()

    # ── 感官 ──

    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        return None  # 本地模式不支持媒体

    # ── 认知 ──

    async def resolve_name(self, user_id: str) -> str:
        if user_id == "local_cli_user":
            return "用户"
        return user_id[-8:]

    async def list_members(self, chat_id: str) -> list[ChatMember]:
        return []  # 本地模式无群聊


def _print_bot(name: str, text: str, prefix: str = "") -> None:
    """格式化输出 bot 回复"""
    label = f"{name}"
    if prefix:
        label = f"{name} · {prefix}"
    print(f"\n\033[1;36m{label}:\033[0m {text}")


def _extract_card_text(card_json: dict) -> str:
    """从卡片 JSON 中提取文本内容"""
    elements = card_json.get("elements", [])
    parts = []
    for el in elements:
        content = el.get("content", "")
        if content:
            parts.append(content)
    return "\n".join(parts)


async def run_conversation(home: Path, config: LQConfig, single_message: str = "") -> None:
    """运行本地交互式对话。

    走标准事件流：用户输入 → IncomingMessage → router.handle() → _handle_private
    → _flush_private → adapter.send() → 终端输出。
    与 gateway.py 的飞书模式使用同一条代码路径。

    Args:
        home: 实例工作目录
        config: 实例配置
        single_message: 如果非空，发送单条消息后退出（非交互模式）
    """
    # 将 config 中的代理设置注入环境变量
    if config.api.proxy:
        for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                    "https_proxy", "http_proxy", "all_proxy"):
            os.environ.setdefault(var, config.api.proxy)

    # 压低日志噪音
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)],
    )

    # 初始化核心组件
    from lq.executor.api import DirectAPIExecutor
    from lq.executor.claude_code import BashExecutor, ClaudeCodeExecutor
    from lq.memory import MemoryManager
    from lq.session import SessionManager
    from lq.stats import StatsTracker
    from lq.tools import ToolRegistry

    adapter = LocalAdapter(config.name)
    queue: asyncio.Queue = asyncio.Queue()
    await adapter.connect(queue)

    memory = MemoryManager(home, config=config)
    executor = DirectAPIExecutor(config.api, config.model)
    stats = StatsTracker(home)
    executor.stats = stats
    session_mgr = SessionManager(home)
    tool_registry = ToolRegistry(home)
    tool_registry.load_all()
    cc_executor = ClaudeCodeExecutor(home, config.api)
    bash_executor = BashExecutor(home)

    # 创建路由器并注入依赖
    from lq.router import MessageRouter

    router = MessageRouter(executor, memory, adapter, "local_bot", config.name)
    router.config = config
    router.session_mgr = session_mgr
    router.calendar = None  # 本地模式无飞书日历
    router.stats = stats
    router.cc_executor = cc_executor
    router.bash_executor = bash_executor
    router.tool_registry = tool_registry

    # CLI 不需要防抖（用户手动输入，每条消息立即处理）
    router._private_debounce_seconds = 0.01

    # 初始化后处理管线
    from lq.intent import IntentDetector
    from lq.subagent import SubAgent
    from lq.postprocessor import PostProcessor

    detector = IntentDetector(executor)
    subagent = SubAgent(executor)
    post_processor = PostProcessor(
        detector, subagent, router._execute_tool, router._send_tool_notification,
    )
    router.post_processor = post_processor

    chat_id = LOCAL_CHAT_ID
    msg_counter = 0

    if single_message:
        msg_counter += 1
        await _dispatch_and_wait(adapter, router, chat_id, msg_counter, single_message)
        session_mgr.save()
        return

    # 交互模式
    print(f"\n\033[1;33m=== 灵雀 @{config.name} · 本地对话模式 ===\033[0m")
    print("输入消息开始对话，输入 /exit 退出\n")

    while True:
        try:
            user_input = await asyncio.to_thread(
                input, "\033[1;32m你:\033[0m ",
            )
            user_input = user_input.strip()
        except (KeyboardInterrupt, EOFError):
            print("\n再见！")
            break

        if not user_input:
            continue
        if user_input in ("/exit", "/quit", "/q"):
            print("再见！")
            break
        if user_input == "/clear":
            session = session_mgr.get_or_create(chat_id)
            session.messages.clear()
            session._summary = ""
            session._total_tokens = 0
            print("[会话已清空]")
            continue
        if user_input == "/history":
            session = session_mgr.get_or_create(chat_id)
            if not session.messages:
                print("[暂无对话历史]")
            else:
                for m in session.messages:
                    role = m.get("role", "?")
                    content = m.get("content", "")
                    if isinstance(content, str):
                        print(f"  [{role}] {content[:120]}")
            continue

        msg_counter += 1
        await _dispatch_and_wait(adapter, router, chat_id, msg_counter, user_input)

        # 每轮自动保存
        session_mgr.save()

    # 退出时保存
    session_mgr.save()


async def _dispatch_and_wait(
    adapter: LocalAdapter,
    router: Any,
    chat_id: str,
    msg_counter: int,
    text: str,
) -> None:
    """构造标准 IncomingMessage → router.handle → 等待回复完成。

    利用 LocalAdapter 的 _turn_done 事件：
    router._flush_private 的 finally 块调用 adapter.stop_thinking → 设置事件。
    """
    msg = IncomingMessage(
        message_id=f"local_{msg_counter}",
        chat_id=chat_id,
        chat_type=ChatType.PRIVATE,
        sender_id="local_cli_user",
        sender_type=SenderType.USER,
        sender_name="用户",
        message_type=MessageType.TEXT,
        text=text,
    )
    adapter._turn_done.clear()

    await router.handle({"event_type": "message", "message": msg})

    # 等待 _flush_private 完成（stop_thinking 设置 _turn_done）
    # 防御：如果 _flush_private 提前退出（无内容），pending 被清空但事件未设置
    while not adapter._turn_done.is_set():
        try:
            await asyncio.wait_for(adapter._turn_done.wait(), timeout=1.0)
        except asyncio.TimeoutError:
            if chat_id not in router._private_pending:
                break  # flush 已完成但无需回复
