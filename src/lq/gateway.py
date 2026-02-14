"""AssistantGateway — 主入口，协调所有组件"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lq.config import LQConfig
from lq.executor.api import DirectAPIExecutor
from lq.executor.claude_code import BashExecutor, ClaudeCodeExecutor
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
        bot_name = bot_info.get("app_name") or bot_info.get("bot_name") or self.config.name
        sender.bot_open_id = bot_open_id
        if bot_open_id and bot_name:
            sender._user_name_cache[bot_open_id] = bot_name
        # 消息列表 API 中 bot 的 sender_id 使用 app_id (cli_xxx) 格式，
        # 缓存 app_id → name 以便识别自己发送的消息
        if self.config.feishu.app_id and bot_name:
            sender._user_name_cache[self.config.feishu.app_id] = bot_name
        # 加载之前推断并记忆的其他 bot 身份
        sender.load_bot_identities(self.home)
        logger.info("Bot open_id: %s app_id: %s name: %s", bot_open_id, self.config.feishu.app_id, bot_name)

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

        # 初始化 Bash 执行器
        bash_executor = BashExecutor(self.home)

        # 创建路由器并注入依赖
        router = MessageRouter(executor, memory, sender, bot_open_id, bot_name)
        router.session_mgr = session_mgr
        router.calendar = calendar
        router.stats = stats
        router.cc_executor = cc_executor
        router.bash_executor = bash_executor
        router.tool_registry = tool_registry
        self._router = router
        logger.info("会话管理器已加载（含 Claude Code + Bash 执行器）")

        # 加载已知群聊 ID（用于早安问候等）
        self._load_known_groups(router)

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

        # 并发运行消费者、心跳、inbox 轮询、会话自动保存和群聊主动轮询
        tasks = [
            asyncio.create_task(self._consume_messages(router, loop), name="consumer"),
            asyncio.create_task(heartbeat.run_forever(self.shutdown_event), name="heartbeat"),
            asyncio.create_task(self._poll_inbox(router), name="inbox"),
            asyncio.create_task(self._auto_save_sessions(session_mgr), name="autosave"),
            asyncio.create_task(self._poll_active_groups(router, sender), name="group-poll"),
        ]

        # 等待 shutdown_event 被信号触发
        await self.shutdown_event.wait()
        logger.info("开始关闭，等待任务结束...")

        # 给各任务一个宽限期，然后强制取消
        _, pending = await asyncio.wait(tasks, timeout=5.0)
        for t in pending:
            logger.warning("强制取消任务: %s", t.get_name())
            t.cancel()
        if pending:
            await asyncio.wait(pending, timeout=2.0)

        # 关闭时保存会话和已知群聊
        session_mgr.save()
        self._save_known_groups(router)
        logger.info("会话已保存，关闭完成")

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

            # 每日群聊早安问候
            if is_daily_first:
                try:
                    known = router.get_known_group_ids()
                    if known:
                        self._schedule_morning_greetings(
                            known, executor, memory, sender,
                            config.name,
                        )
                except Exception:
                    logger.exception("群聊早安问候调度失败")

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
                # 用 shutdown_event.wait + timeout 代替 sleep
                try:
                    await asyncio.wait_for(
                        self.shutdown_event.wait(), timeout=2.0,
                    )
                    break  # shutdown_event 已设置
                except asyncio.TimeoutError:
                    pass  # 正常轮询周期
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

    async def _poll_active_groups(
        self,
        router: MessageRouter,
        sender: FeishuSender,
    ) -> None:
        """主动轮询活跃群聊的消息，补充 WS 收不到的 bot 消息。

        每 3 秒轮询一个活跃群聊，将新发现的 bot 消息注入 router 的 group_buffers。
        """
        logger.info("群聊主动轮询启动")
        while not self.shutdown_event.is_set():
            try:
                await asyncio.sleep(3.0)
                active = router.get_active_groups()
                if not active:
                    continue
                for chat_id in active:
                    if self.shutdown_event.is_set():
                        break
                    try:
                        api_msgs = await sender.fetch_chat_messages(chat_id, 10)
                    except Exception:
                        logger.warning("主动轮询群 %s 失败", chat_id[-8:], exc_info=True)
                        continue
                    if not api_msgs:
                        continue
                    for msg in api_msgs:
                        if msg.get("sender_type") != "app":
                            continue
                        sender.register_bot_member(chat_id, msg["sender_id"])
                        if msg.get("sender_id") == router.bot_open_id:
                            continue
                        sender_name = await sender.resolve_name(msg["sender_id"])
                        await router.inject_polled_message(chat_id, {
                            "text": msg["text"],
                            "sender_id": msg["sender_id"],
                            "sender_name": sender_name,
                            "sender_type": "app",
                            "message_id": msg["message_id"],
                            "chat_id": chat_id,
                        })
                    # 多个群之间稍做间隔，避免 API 频率限制
                    if len(active) > 1:
                        await asyncio.sleep(1.0)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("群聊主动轮询异常")
        logger.info("群聊主动轮询已停止")

    async def _auto_save_sessions(self, session_mgr: SessionManager) -> None:
        """每 60 秒自动保存会话，防止崩溃丢失"""
        while not self.shutdown_event.is_set():
            try:
                # 用 shutdown_event.wait + timeout 代替 sleep，
                # 确保收到关闭信号时立即退出而非阻塞 60 秒
                await asyncio.wait_for(
                    self.shutdown_event.wait(), timeout=60,
                )
                break  # shutdown_event 已设置
            except asyncio.TimeoutError:
                pass  # 正常超时，执行保存
            except asyncio.CancelledError:
                break
            try:
                session_mgr.save()
                if hasattr(self, '_router'):
                    self._save_known_groups(self._router)
            except Exception:
                logger.exception("自动保存会话失败")

    def _load_known_groups(self, router: MessageRouter) -> None:
        """从 groups.json 加载已知群聊 ID"""
        path = self.home / "groups.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            ids = set(data.get("known_group_ids", []))
            router.set_known_group_ids(ids)
            logger.info("已加载 %d 个已知群聊", len(ids))
        except Exception:
            logger.warning("加载 groups.json 失败", exc_info=True)

    def _save_known_groups(self, router: MessageRouter) -> None:
        """保存已知群聊 ID 到 groups.json"""
        path = self.home / "groups.json"
        ids = router.get_known_group_ids()
        try:
            path.write_text(
                json.dumps({"known_group_ids": sorted(ids)}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("保存 groups.json 失败", exc_info=True)

    def _schedule_morning_greetings(
        self,
        known_groups: set[str],
        executor: Any,
        memory: Any,
        sender: Any,
        bot_name: str,
    ) -> None:
        """为每个已知群聊安排延迟早安问候"""
        from lq.prompts import MORNING_GREETING_SYSTEM, MORNING_GREETING_USER

        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        for chat_id in known_groups:
            if sender.is_chat_left(chat_id):
                continue
            # deterministic jitter: 0-1800 秒，基于 hash 保证重启不重发
            h = hashlib.md5(f"{bot_name}:{chat_id}:{today}".encode()).hexdigest()
            delay = int(h[:8], 16) % 1800
            asyncio.ensure_future(
                self._do_morning_greeting(
                    chat_id, delay, executor, memory, sender,
                )
            )
        logger.info("已安排 %d 个群聊的早安问候", len(known_groups))

    async def _do_morning_greeting(
        self,
        chat_id: str,
        delay: int,
        executor: Any,
        memory: Any,
        sender: Any,
    ) -> None:
        """延迟后检查群聊活跃度并发送早安问候"""
        try:
            await asyncio.sleep(delay)
            # 检查今天是否已有消息（包括自己的）
            msgs = await sender.fetch_chat_messages(chat_id, 20)
            cst = timezone(timedelta(hours=8))
            today_start = datetime.now(cst).replace(
                hour=0, minute=0, second=0, microsecond=0,
            )
            today_start_ms = int(today_start.timestamp() * 1000)
            for msg in msgs:
                ct = msg.get("create_time", "")
                if ct and int(ct) >= today_start_ms:
                    logger.debug("群 %s 今天已有消息，跳过早安", chat_id[-8:])
                    return
            # 生成问候
            soul = memory.read_soul()
            system = MORNING_GREETING_SYSTEM.format(soul=soul)
            greeting = await executor.reply(system, MORNING_GREETING_USER)
            greeting = greeting.strip()
            if greeting:
                await sender.send_text(chat_id, greeting)
                logger.info("早安问候已发送: %s -> %s", chat_id[-8:], greeting[:50])
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("早安问候失败: %s", chat_id[-8:])

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
