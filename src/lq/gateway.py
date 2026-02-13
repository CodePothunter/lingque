"""AssistantGateway — 主入口，协调所有组件"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import threading
from pathlib import Path

from lq.config import LQConfig
from lq.executor.api import DirectAPIExecutor
from lq.executor.claude_code import ClaudeCodeExecutor
from lq.feishu.listener import FeishuListener
from lq.feishu.sender import FeishuSender
from lq.heartbeat import HeartbeatRunner
from lq.memory import MemoryManager
from lq.router import MessageRouter
from lq.session import SessionManager
from lq.stats import StatsTracker
from lq.tools import ToolRegistry

logger = logging.getLogger(__name__)


class _Namespace:
    """简易属性访问对象，模拟飞书 SDK event 的 obj.attr 风格"""
    def __init__(self, **kwargs: object) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


def _make_fake_event(
    text: str, chat_id: str, message_id: str, sender_open_id: str,
) -> _Namespace:
    """构造与飞书 SDK P2ImMessageReceiveV1Data 兼容的 event 对象"""
    import json as _json
    return _Namespace(
        message=_Namespace(
            message_id=message_id,
            chat_id=chat_id,
            chat_type="p2p",
            content=_json.dumps({"text": text}),
            message_type="text",
        ),
        sender=_Namespace(
            sender_id=_Namespace(open_id=sender_open_id),
        ),
    )


class AssistantGateway:
    def __init__(self, config: LQConfig, home: Path) -> None:
        self.config = config
        self.home = home
        self.shutdown_event = asyncio.Event()
        self.queue: asyncio.Queue = asyncio.Queue()

    async def run(self) -> None:
        """主入口：配置日志 → 写 PID → 启动组件 → 事件循环"""
        self._setup_logging()
        self._write_pid()
        self._setup_signals()

        try:
            await self._start()
        finally:
            self._cleanup()

    async def _start(self) -> None:
        loop = asyncio.get_running_loop()

        # 初始化发送器并获取 bot info
        sender = FeishuSender(
            self.config.feishu.app_id,
            self.config.feishu.app_secret,
        )
        bot_info = await sender.fetch_bot_info()
        bot_open_id = bot_info.get("open_id", self.config.feishu.bot_open_id)
        if bot_open_id:
            self.config.feishu.bot_open_id = bot_open_id
        bot_name = bot_info.get("bot_name", "")
        if bot_open_id and bot_name:
            sender._user_name_cache[bot_open_id] = bot_name
        logger.info("Bot open_id: %s name: %s", bot_open_id, bot_name)

        # 初始化核心组件
        memory = MemoryManager(self.home)
        executor = DirectAPIExecutor(self.config.api, self.config.model)
        cc_executor = ClaudeCodeExecutor(self.home, self.config.api)
        stats = StatsTracker(self.home)
        executor.stats = stats  # 注入统计跟踪
        session_mgr = SessionManager(self.home)

        # 初始化自定义工具注册表
        tool_registry = ToolRegistry(self.home)
        tool_registry.load_all()
        logger.info("自定义工具已加载: %d 个", len(tool_registry.list_tools()))

        # 初始化日历（Phase 4）
        calendar = None
        try:
            from lq.feishu.calendar import FeishuCalendar
            calendar = FeishuCalendar(sender.client)
            logger.info("日历模块已加载")
        except Exception:
            logger.warning("日历模块加载失败", exc_info=True)

        # 创建路由器并注入依赖
        router = MessageRouter(executor, memory, sender, bot_open_id)
        router.session_mgr = session_mgr
        router.calendar = calendar
        router.stats = stats
        router.cc_executor = cc_executor
        router.tool_registry = tool_registry
        logger.info("会话管理器已加载")

        # 初始化后处理管线
        from lq.intent import IntentDetector
        from lq.subagent import SubAgent
        from lq.postprocessor import PostProcessor

        detector = IntentDetector(executor)
        subagent = SubAgent(executor)
        post_processor = PostProcessor(
            detector, subagent, router._execute_tool, router._send_reply,
        )
        router.post_processor = post_processor
        logger.info("后处理管线已加载")

        # 配置心跳回调
        heartbeat = HeartbeatRunner(
            self.config.heartbeat_interval,
            self.config.active_hours,
            self.home,
        )
        heartbeat.on_heartbeat = self._make_heartbeat_callback(
            executor, memory, sender, calendar, stats, router
        )

        # 启动飞书 WS 监听（daemon 线程）
        listener = FeishuListener(
            self.config.feishu.app_id,
            self.config.feishu.app_secret,
            self.queue,
            loop,
        )
        feishu_thread = threading.Thread(
            target=listener.start_blocking,
            name="feishu-ws",
            daemon=True,
        )
        feishu_thread.start()
        logger.info("飞书 WebSocket 线程已启动")

        # 并发运行消费者、心跳和 inbox 轮询
        await asyncio.gather(
            self._consume_messages(router, loop),
            heartbeat.run_forever(self.shutdown_event),
            self._poll_inbox(router),
        )

        # 关闭时保存会话
        session_mgr.save()
        logger.info("会话已保存")

    async def _consume_messages(
        self,
        router: MessageRouter,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """从 Queue 消费消息并路由处理"""
        logger.info("消息消费者启动")
        while not self.shutdown_event.is_set():
            try:
                data = await asyncio.wait_for(self.queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            try:
                await router.handle(data)
            except Exception:
                logger.exception("处理消息失败: %s", data.get("event_type", "unknown"))

    def _make_heartbeat_callback(self, executor, memory, sender, calendar, stats, router):
        """创建心跳回调"""
        config = self.config

        async def heartbeat_callback(is_daily_first: bool, is_weekly_first: bool):
            logger.info("心跳: daily=%s weekly=%s", is_daily_first, is_weekly_first)

            # 执行 HEARTBEAT.md 中定义的自定义任务（带工具调用支持）
            await self._run_heartbeat_tasks(router)

            # 每日晨报
            if is_daily_first and calendar:
                try:
                    from datetime import datetime, timedelta
                    from lq.feishu.cards import build_schedule_card

                    now = datetime.now()
                    start = now.replace(hour=0, minute=0, second=0).isoformat()
                    end = now.replace(hour=23, minute=59, second=59).isoformat()
                    events = await calendar.list_events(start, end)

                    card = build_schedule_card(events)
                    # 发送到主人（bot 的私聊会话）
                    # 使用 heartbeat 的 SOUL.md 读取获取目标
                    soul = memory.read_soul()
                    system = f"{soul}\n\n请生成一条简洁的早安消息，提醒用户今天的日程安排。"
                    if events:
                        event_list = "\n".join(
                            f"- {e['start_time']}-{e['end_time']} {e['summary']}"
                            for e in events
                        )
                        greeting = await executor.reply(system, f"今日日程：\n{event_list}")
                    else:
                        greeting = await executor.reply(system, "今天没有日程安排。")

                    logger.info("晨报已生成: %s", greeting[:50])

                    # 发送晨报给主人
                    owner_chat_id = config.feishu.owner_chat_id
                    if owner_chat_id:
                        await sender.send_text(owner_chat_id, greeting)
                        logger.info("晨报已发送至 %s", owner_chat_id)
                    else:
                        logger.warning("晨报已生成但未发送：未配置 owner_chat_id")
                except Exception:
                    logger.exception("晨报生成失败")

            # 费用告警
            if stats:
                daily = stats.get_daily_summary()
                cost = daily.get("total_cost", 0)
                if cost > config.cost_alert_daily:
                    logger.warning("今日 API 消耗 $%.2f 超过阈值 $%.2f", cost, config.cost_alert_daily)

        return heartbeat_callback

    async def _run_heartbeat_tasks(self, router: MessageRouter) -> None:
        """读取 HEARTBEAT.md 中定义的任务并交给 LLM 带工具执行。

        通过 router 走完整工具调用链，支持自省时的 read/write_self_file 等操作。
        """
        heartbeat_path = self.home / "HEARTBEAT.md"
        if not heartbeat_path.exists():
            return
        try:
            content = heartbeat_path.read_text(encoding="utf-8").strip()
            if not content:
                return
            system = router.memory.build_context()
            system += (
                "\n\n以下是你的心跳任务定义（来自 HEARTBEAT.md）：\n"
                f"{content}\n\n"
                "请根据当前时间判断是否需要执行其中的任务。"
                "如果需要执行，直接使用工具执行（如 read_self_file、write_self_file、write_memory 等）。"
                "深夜自省时：读取 SOUL.md，结合今天的日志和经历，微调它以体现你的成长。"
                "如果当前没有需要执行的任务，输出「无」。"
            )
            chat_id = self.config.feishu.owner_chat_id or "heartbeat"
            messages = [{"role": "user", "content": "请检查并执行心跳任务。"}]
            result = await router._reply_with_tool_loop(system, messages, chat_id, None)
            if result and result.strip() and result.strip() != "无":
                owner_chat_id = self.config.feishu.owner_chat_id
                if owner_chat_id:
                    await router.sender.send_text(owner_chat_id, result)
                router.memory.append_daily(f"- 心跳任务执行: {result[:100]}\n")
                logger.info("心跳任务执行: %s", result[:80])
        except Exception:
            logger.exception("心跳任务执行失败")

    async def _poll_inbox(self, router: MessageRouter) -> None:
        """轮询 inbox.txt，处理 `lq say` 写入的本地消息。

        构造与飞书 SDK 兼容的 event 对象，走 router.handle 完整路径，
        确保防抖、消息解析、工具调用等逻辑都被测试到。
        """
        inbox_path = self.home / "inbox.txt"
        chat_id = self.config.feishu.owner_chat_id or "local_cli"
        msg_counter = 0
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(2.0)
                if not inbox_path.exists():
                    continue
                text = inbox_path.read_text(encoding="utf-8").strip()
                if not text:
                    continue
                # 清空 inbox
                inbox_path.write_text("", encoding="utf-8")
                for line in text.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    msg_counter += 1
                    # 构造与飞书 SDK event 对象属性兼容的 namespace
                    event = _make_fake_event(
                        text=line,
                        chat_id=chat_id,
                        message_id=f"inbox_{msg_counter}",
                        sender_open_id="local_cli_user",
                    )
                    try:
                        await router.handle({
                            "event_type": "im.message.receive_v1",
                            "event": event,
                        })
                    except Exception:
                        logger.exception("处理 inbox 消息失败")
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("inbox 轮询异常")

    def _setup_logging(self) -> None:
        log_dir = self.home / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "gateway.log"

        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        handlers: list[logging.Handler] = [
            logging.FileHandler(log_file, encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ]
        logging.basicConfig(level=logging.INFO, format=fmt, handlers=handlers)

        # 压制第三方库的噪音日志
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("Lark").setLevel(logging.WARNING)

    def _write_pid(self) -> None:
        pid_path = self.home / "gateway.pid"
        pid_path.write_text(str(os.getpid()))
        logger.info("PID %d 写入 %s", os.getpid(), pid_path)

    def _cleanup(self) -> None:
        pid_path = self.home / "gateway.pid"
        if pid_path.exists():
            pid_path.unlink()
            logger.info("PID 文件已清理")

    def _setup_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

    def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info("收到信号 %s，正在关闭...", sig.name)
        self.shutdown_event.set()
