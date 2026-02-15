"""本地交互式对话 — 不依赖飞书，直接在终端与灵雀对话"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

from lq.config import LQConfig

logger = logging.getLogger(__name__)

# 本地对话使用的 chat_id
LOCAL_CHAT_ID = "local_say"


class LocalSender:
    """替代 FeishuSender 的本地终端发送器。

    实现 FeishuSender 的关键接口，将消息输出到终端而非飞书。
    """

    def __init__(self, bot_name: str) -> None:
        self.bot_name = bot_name
        self.bot_open_id = "local_bot"
        self._user_name_cache: dict[str, str] = {
            "local_bot": bot_name,
            "local_cli_user": "用户",
        }
        self._left_chats: set[str] = set()
        self._bot_members: dict[str, set[str]] = {}

    # ── 消息发送 ──

    async def send_text(self, chat_id: str, text: str) -> str | None:
        _print_bot(self.bot_name, text)
        return "local_msg"

    async def reply_text(self, message_id: str, text: str) -> str | None:
        _print_bot(self.bot_name, text)
        return "local_msg"

    async def send_card(self, chat_id: str, card_json: dict, **kw: Any) -> str | None:
        content = _extract_card_text(card_json)
        if content:
            _print_bot(self.bot_name, content, prefix="卡片")
        return "local_msg"

    async def reply_card(self, message_id: str, card_json: dict, **kw: Any) -> str | None:
        content = _extract_card_text(card_json)
        if content:
            _print_bot(self.bot_name, content, prefix="卡片")
        return "local_msg"

    # ── 用户信息 ──

    async def fetch_bot_info(self) -> dict[str, Any]:
        return {"open_id": self.bot_open_id, "app_name": self.bot_name}

    async def get_user_name(self, open_id: str, chat_id: str = "") -> str:
        return self._user_name_cache.get(open_id, "用户")

    async def resolve_name(self, open_id: str) -> str:
        return self._user_name_cache.get(open_id, "用户")

    # ── 群聊/状态相关（本地模式不需要） ──

    def is_chat_left(self, chat_id: str) -> bool:
        return False

    def register_bot_member(self, chat_id: str, bot_open_id: str) -> None:
        pass

    def load_bot_identities(self, home: Path) -> None:
        pass

    def get_bot_members(self, chat_id: str) -> set[str]:
        return set()

    def get_member_name(self, open_id: str) -> str:
        return self._user_name_cache.get(open_id, "")

    async def fetch_chat_messages(self, chat_id: str, count: int = 10) -> list[dict]:
        return []


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

    Args:
        home: 实例工作目录
        config: 实例配置
        single_message: 如果非空，发送单条消息后退出（非交互模式）
    """
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
    from lq.prompts import TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE, wrap_tag

    sender = LocalSender(config.name)
    memory = MemoryManager(home)
    executor = DirectAPIExecutor(config.api, config.model)
    stats = StatsTracker(home)
    executor.stats = stats
    session_mgr = SessionManager(home)
    tool_registry = ToolRegistry(home)
    tool_registry.load_all()
    cc_executor = ClaudeCodeExecutor(home, config.api)
    bash_executor = BashExecutor(home)

    # 日历模块（可选，本地模式可能无飞书 client）
    calendar = None
    try:
        from lq.feishu.calendar import FeishuCalendar
        # FeishuCalendar 需要飞书 client，本地模式下跳过
    except Exception:
        pass

    # 创建路由器并注入依赖
    from lq.router import MessageRouter

    router = MessageRouter(executor, memory, sender, "local_bot", config.name)
    router.session_mgr = session_mgr
    router.calendar = calendar
    router.stats = stats
    router.cc_executor = cc_executor
    router.bash_executor = bash_executor
    router.zhipu_api_key = config.api.zhipu_api_key
    router.tool_registry = tool_registry

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

    if single_message:
        # 单条消息模式
        await _do_one_turn(router, memory, session_mgr, chat_id, single_message)
        session_mgr.save()
        return

    # 交互模式
    print(f"\n\033[1;33m=== 灵雀 @{config.name} · 本地对话模式 ===\033[0m")
    print("输入消息开始对话，输入 /exit 退出\n")

    while True:
        try:
            user_input = input("\033[1;32m你:\033[0m ").strip()
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

        await _do_one_turn(router, memory, session_mgr, chat_id, user_input)

        # 每轮自动保存
        session_mgr.save()

    # 退出时保存
    session_mgr.save()


async def _do_one_turn(
    router: Any,
    memory: Any,
    session_mgr: Any,
    chat_id: str,
    user_text: str,
) -> None:
    """执行一轮对话：构建 system prompt → 调用工具循环 → 记录历史"""
    from lq.prompts import TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE, wrap_tag

    # 构建 system prompt（和 _flush_private 完全一致）
    system = memory.build_context(chat_id=chat_id)
    system += (
        f"\n\n你正在和用户私聊。当前会话 chat_id={chat_id}。请直接、简洁地回复。"
        "如果用户要求记住什么，使用 write_memory 工具。"
        "如果涉及日程，使用 calendar 工具。"
        "如果用户询问你的配置或要求你修改自己（如人格、记忆），使用 read_self_file / write_self_file 工具。"
        "需要联网查询时（搜索、天气、新闻等），使用 web_search / web_fetch 工具。"
        "需要计算或处理数据时，使用 run_python 工具。"
        "需要读写文件时，使用 read_file / write_file 工具。"
        "\n\n" + wrap_tag(TAG_CONSTRAINTS, CONSTRAINTS_PRIVATE)
    )

    # 会话管理
    session = session_mgr.get_or_create(chat_id)
    session.add_message("user", user_text, sender_name="用户")
    messages = session.get_messages()

    # 调用完整工具循环
    try:
        reply_text = await router._reply_with_tool_loop(
            system, messages, chat_id, None,
        )
    except Exception:
        logger.exception("对话失败")
        print("\n\033[1;31m[错误] 对话处理失败，请重试\033[0m")
        reply_text = ""

    if reply_text:
        session = session_mgr.get_or_create(chat_id)
        session.add_message("assistant", reply_text, sender_name="你")
        if session.should_compact():
            await router._compact_session(session)
