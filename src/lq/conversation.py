"""本地交互式对话 — 不依赖飞书，直接在终端与灵雀对话

走标准事件流：stdin → IncomingMessage → queue → consumer → router.handle() → adapter.send() → stdout
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

    两种模式:
      - **gateway 模式** (home 非 None): connect() 自动启动 stdin 读取 + inbox 监听，
        事件推入 queue，走与飞书完全一致的事件流。
      - **chat 模式** (home 为 None): connect() 被动，由 run_conversation 管理输入循环。

    输出侧：adapter.send() 打印到 stdout。
    同步机制：start_thinking 返回 truthy handle，使 router 的 finally 块
    调用 stop_thinking → 设置 _turn_done 事件，通知对话循环本轮结束。
    """

    # 思考动画帧 (braille spinner)
    _SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, bot_name: str, *, home: Path | None = None) -> None:
        self._bot_name = bot_name
        self._home = home  # 非 None = gateway 模式
        # 对话轮次完成信号（stop_thinking 设置，conversation loop 等待）
        self._turn_done: asyncio.Event = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self._msg_counter: int = 0
        self._shutdown: asyncio.Event = asyncio.Event()
        self._spinner_task: asyncio.Task | None = None

    # ── 身份 ──

    async def get_identity(self) -> BotIdentity:
        return BotIdentity(bot_id="local_bot", bot_name=self._bot_name)

    # ── 感知 ──

    async def connect(self, queue: asyncio.Queue) -> None:
        self._queue = queue
        if self._home is not None:
            # Gateway 模式：启动事件源（类比飞书适配器的 WS + converter）
            if sys.stdin.isatty():
                self._tasks.append(
                    asyncio.create_task(self._read_stdin(), name="local-stdin")
                )
                logger.info("本地适配器: stdin 读取已启动")
            self._tasks.append(
                asyncio.create_task(self._watch_inbox(), name="local-inbox")
            )
            logger.info("本地适配器: inbox 监听已启动")

    async def disconnect(self) -> None:
        self._shutdown.set()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # ── 事件源（gateway 模式）──

    async def _read_stdin(self) -> None:
        """从 stdin 读取用户输入，转换为标准事件推入 queue。

        类比 FeishuAdapter._event_converter：将原始输入转为标准 IncomingMessage。
        """
        print(f"\n\033[1;33m=== 灵雀 @{self._bot_name} · 本地模式 ===\033[0m")
        print("输入消息开始对话，Ctrl+C 退出\n")
        while not self._shutdown.is_set():
            try:
                user_input = await asyncio.to_thread(
                    input, "\033[1;32m你:\033[0m ",
                )
                user_input = user_input.strip()
            except (KeyboardInterrupt, EOFError):
                logger.info("stdin 读取结束（用户中断）")
                return
            except asyncio.CancelledError:
                return
            if not user_input:
                continue
            self._msg_counter += 1
            msg = IncomingMessage(
                message_id=f"local_{self._msg_counter}",
                chat_id=LOCAL_CHAT_ID,
                chat_type=ChatType.PRIVATE,
                sender_id="local_cli_user",
                sender_type=SenderType.USER,
                sender_name="用户",
                message_type=MessageType.TEXT,
                text=user_input,
                platform="local",
            )
            self._turn_done.clear()
            await self._queue.put({"event_type": "message", "message": msg})
            # 等待本轮回复完成再提示下一轮输入
            await self._turn_done.wait()

    async def _watch_inbox(self) -> None:
        """监听 inbox.txt 文件，转换为标准事件推入 queue。

        类比 FeishuAdapter._poll_bot_messages：定期拉取消息并转换。
        """
        if self._home is None:
            return
        inbox_path = self._home / "inbox.txt"
        while not self._shutdown.is_set():
            try:
                try:
                    await asyncio.wait_for(self._shutdown.wait(), timeout=2.0)
                    break
                except asyncio.TimeoutError:
                    pass
                if not inbox_path.exists():
                    continue
                text = inbox_path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                inbox_path.write_text("", encoding="utf-8")
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    self._msg_counter += 1
                    msg = IncomingMessage(
                        message_id=f"inbox_{self._msg_counter}",
                        chat_id=LOCAL_CHAT_ID,
                        chat_type=ChatType.PRIVATE,
                        sender_id="local_cli_user",
                        sender_type=SenderType.USER,
                        sender_name="用户",
                        message_type=MessageType.TEXT,
                        text=line,
                        platform="local",
                    )
                    await self._queue.put({"event_type": "message", "message": msg})
            except asyncio.CancelledError:
                return
            except Exception:
                logger.exception("inbox 监听异常")

    # ── 表达 ──

    async def send(self, message: OutgoingMessage) -> str | None:
        self._stop_spinner()
        self._clear_line()
        if message.image_path:
            _print_bot(self._bot_name, f"[图片: {message.image_path}]")
            if message.text:
                _print_bot(self._bot_name, message.text)
        elif message.card:
            _print_card(self._bot_name, message.card)
        elif message.text:
            _print_bot(self._bot_name, message.text)
        return "local_msg"

    # ── 存在感 ──

    async def start_thinking(self, message_id: str) -> str | None:
        self._stop_spinner()
        self._spinner_task = asyncio.create_task(self._animate_spinner())
        return "local"

    async def stop_thinking(self, message_id: str, handle: str) -> None:
        self._stop_spinner()
        self._clear_line()
        # 信号：本轮处理（含 LLM 回复和发送）已完成
        self._turn_done.set()

    async def notify_queued(self, chat_id: str, count: int) -> None:
        self._stop_spinner()
        sys.stdout.write(f"\r\033[K\033[2m📥 已收到 {count} 条消息，等待更多...\033[0m")
        sys.stdout.flush()

    async def _animate_spinner(self) -> None:
        """循环播放 braille spinner 动画，直到被取消。"""
        frames = self._SPINNER_FRAMES
        i = 0
        try:
            while True:
                frame = frames[i % len(frames)]
                sys.stdout.write(
                    f"\r\033[K\033[2m{frame} {self._bot_name} 思考中...\033[0m"
                )
                sys.stdout.flush()
                i += 1
                await asyncio.sleep(0.08)
        except asyncio.CancelledError:
            return

    def _stop_spinner(self) -> None:
        """取消正在运行的 spinner 任务。"""
        if self._spinner_task is not None:
            self._spinner_task.cancel()
            self._spinner_task = None

    def _clear_line(self) -> None:
        """清除当前行（spinner / 队列指示器）"""
        sys.stdout.write("\r\033[K")
        sys.stdout.flush()

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


def _print_bot(name: str, text: str) -> None:
    """格式化输出 bot 文本回复"""
    print(f"\n\033[1;36m{name}:\033[0m {text}")


# 卡片类型 → (emoji, 颜色 ANSI)
_CARD_STYLES: dict[str, tuple[str, str]] = {
    "info":     ("💡", "\033[34m"),   # 蓝
    "schedule": ("📅", "\033[34m"),   # 蓝
    "task":     ("📋", "\033[35m"),   # 紫
    "error":    ("⚠️",  "\033[31m"),   # 红
    "confirm":  ("❓", "\033[33m"),   # 黄
}


def _print_card(name: str, card_json: dict) -> None:
    """格式化输出卡片消息（与飞书卡片视觉对等）"""
    card_type = card_json.get("type", "info")
    title = card_json.get("title", "")
    emoji, color = _CARD_STYLES.get(card_type, ("📌", "\033[36m"))
    reset = "\033[0m"

    # 提取内容
    content = card_json.get("content", "")
    if not content:
        elements = card_json.get("elements", [])
        parts = []
        for el in elements:
            c = el.get("content", "")
            if c:
                parts.append(c)
        content = "\n".join(parts)

    if not content and not title:
        return

    # 格式: "  💡 标题: 内容" 或 "  💡 内容"
    if title and content:
        print(f"  {color}{emoji} {title}:{reset} {content}")
    elif title:
        print(f"  {color}{emoji} {title}{reset}")
    else:
        print(f"  {color}{emoji} {content}{reset}")


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
    from lq.executor.api import create_executor
    from lq.executor.claude_code import BashExecutor, ClaudeCodeExecutor
    from lq.memory import MemoryManager
    from lq.session import SessionManager
    from lq.stats import StatsTracker
    from lq.tools import ToolRegistry

    adapter = LocalAdapter(config.name)
    queue: asyncio.Queue = asyncio.Queue()
    await adapter.connect(queue)

    memory = MemoryManager(home, config=config)
    executor = create_executor(config.api, config.model)
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
        platform="local",
    )
    adapter._turn_done.clear()

    await router.handle({"event_type": "message", "message": msg})

    # 等待 _flush_private 完成（stop_thinking 设置 _turn_done）
    try:
        await asyncio.wait_for(adapter._turn_done.wait(), timeout=300.0)
    except asyncio.TimeoutError:
        logger.warning("等待回复超时 (chat=%s)", chat_id)
