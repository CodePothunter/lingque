"""AssistantGateway — 主入口，协调所有组件"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

from lq.config import LQConfig
from lq.evolution import EvolutionEngine
from lq.executor.api import DirectAPIExecutor
from lq.executor.claude_code import BashExecutor, ClaudeCodeExecutor
from lq.heartbeat import HeartbeatRunner
from lq.memory import MemoryManager
from lq.platform import PlatformAdapter, OutgoingMessage, IncomingMessage, ChatType, SenderType, MessageType
from lq.router import MessageRouter
from lq.session import SessionManager
from lq.stats import StatsTracker
from lq.tools import ToolRegistry

logger = logging.getLogger(__name__)


KNOWN_ADAPTERS = {"feishu", "local"}


class AssistantGateway:
    def __init__(self, config: LQConfig, home: Path, adapter_types: list[str] | None = None) -> None:
        self.config = config
        self.home = home
        self.adapter_types = adapter_types or ["feishu"]
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

        # 将 config 中的代理设置注入环境变量，
        # 使 httpx（含 Anthropic SDK 内部客户端）自动使用代理
        if self.config.api.proxy:
            proxy = self.config.api.proxy
            for var in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
                        "https_proxy", "http_proxy", "all_proxy"):
                os.environ.setdefault(var, proxy)
            logger.info("代理已注入环境变量: %s", proxy)

        # 初始化适配器
        adapters: list[PlatformAdapter] = []
        primary: PlatformAdapter | None = None
        bot_open_id = "local_bot"
        bot_name = self.config.name
        has_feishu = "feishu" in self.adapter_types
        has_local = "local" in self.adapter_types

        # 凭证校验 + 提醒
        if has_feishu:
            if not self.config.feishu.app_id or not self.config.feishu.app_secret:
                if len(self.adapter_types) > 1:
                    logger.warning("飞书凭证未配置，跳过飞书适配器")
                    self.adapter_types = [t for t in self.adapter_types if t != "feishu"]
                    has_feishu = False
                else:
                    raise RuntimeError("飞书凭证未配置（app_id / app_secret 为空），无法启动飞书适配器")

        if has_feishu:
            from lq.feishu.adapter import FeishuAdapter
            feishu_adapter = FeishuAdapter(
                self.config.feishu.app_id,
                self.config.feishu.app_secret,
                self.home,
            )
            identity = await feishu_adapter.get_identity()
            bot_open_id = identity.bot_id
            bot_name = identity.bot_name or self.config.name
            if bot_open_id:
                self.config.feishu.bot_open_id = bot_open_id
            logger.info("飞书适配器: open_id=%s app_id=%s name=%s",
                        bot_open_id, self.config.feishu.app_id, bot_name)
            adapters.append(feishu_adapter)
            primary = feishu_adapter

        if has_local:
            from lq.conversation import LocalAdapter
            local_adapter = LocalAdapter(self.config.name, home=self.home)
            adapters.append(local_adapter)
            if primary is None:
                primary = local_adapter
            logger.info("本地适配器已加载（gateway 模式）")

        if not adapters:
            raise RuntimeError("没有可用的适配器，无法启动")

        # 单适配器直接使用，多适配器用 MultiAdapter 组合
        if len(adapters) == 1:
            adapter = adapters[0]
        else:
            from lq.platform.multi import MultiAdapter
            adapter = MultiAdapter(adapters, primary)
            logger.info("多平台模式: %s", ", ".join(type(a).__name__ for a in adapters))
        self._adapter = adapter

        # 初始化核心组件
        executor = DirectAPIExecutor(self.config.api, self.config.model)
        cc_executor = ClaudeCodeExecutor(self.home, self.config.api)
        stats = StatsTracker(self.home)
        executor.stats = stats  # 注入统计跟踪
        session_mgr = SessionManager(self.home)

        # stats_provider 闭包 — router 会在后面赋值给 _stats_router_ref
        startup_ts = int(time.time() * 1000)
        _stats_router_ref: list[MessageRouter | None] = [None]

        def _stats_provider() -> dict:
            """收集运行状态给 MemoryManager 的自我认知模块"""
            router_ref = _stats_router_ref[0]
            # uptime
            elapsed = int(time.time()) - startup_ts // 1000
            if elapsed < 3600:
                uptime = f"{elapsed // 60}分钟"
            elif elapsed < 86400:
                uptime = f"{elapsed // 3600}小时{(elapsed % 3600) // 60}分钟"
            else:
                uptime = f"{elapsed // 86400}天{(elapsed % 86400) // 3600}小时"

            daily = stats.get_daily_summary()
            monthly = stats.get_monthly_summary()

            active_sessions = 0
            if session_mgr:
                active_sessions = len(session_mgr._sessions)

            tool_stats: dict = {}
            if router_ref:
                tool_stats = router_ref._tool_stats

            # 通过飞书群聊发现姐妹实例（仅飞书适配器可用）
            siblings: list[str] = []
            seen: set[str] = set()
            if hasattr(adapter, '_sender'):
                try:
                    feishu_sender = adapter._sender
                    for cid in list(feishu_sender._bot_members.keys()):
                        for bid in feishu_sender.get_bot_members(cid):
                            if bid not in seen:
                                seen.add(bid)
                                siblings.append(feishu_sender.get_member_name(bid))
                except Exception:
                    pass

            # 解析主人身份
            owner_chat_id = self.config.feishu.owner_chat_id
            owner_name = self.config.owner_name
            if not owner_name and owner_chat_id:
                # 尝试从 session 中推断主人名字：
                # 私聊 session 文件名就是 chat_id，里面的 user 消息有 sender_name
                try:
                    sess = session_mgr.get_or_create(owner_chat_id)
                    for msg in sess.get_messages():
                        sn = msg.get("sender_name", "")
                        if msg.get("role") == "user" and sn and sn != "你":
                            owner_name = sn
                            break
                except Exception:
                    pass

            return {
                "model": self.config.model,
                "uptime": uptime,
                "today_calls": daily.get("total_calls", 0),
                "today_tokens": daily.get("total_input_tokens", 0) + daily.get("total_output_tokens", 0),
                "today_cost": daily.get("total_cost", 0.0),
                "monthly_cost": monthly.get("total_cost", 0.0),
                "active_sessions": active_sessions,
                "tool_stats": tool_stats,
                "siblings": siblings,
                "owner_name": owner_name,
                "owner_chat_id": owner_chat_id,
            }

        memory = MemoryManager(self.home, stats_provider=_stats_provider, config=self.config)

        # 初始化自定义工具注册表
        tool_registry = ToolRegistry(self.home)
        tool_registry.load_all()
        logger.info("自定义工具已加载: %d 个", len(tool_registry.list_tools()))

        # 初始化日历（仅飞书适配器支持）
        calendar = None
        if hasattr(adapter, 'feishu_client'):
            try:
                from lq.feishu.calendar import FeishuCalendar
                calendar = FeishuCalendar(adapter.feishu_client)
                logger.info("日历模块已加载")
            except Exception:
                logger.warning("日历模块加载失败", exc_info=True)
        else:
            logger.info("非飞书适配器，跳过日历模块")

        # 初始化 Bash 执行器
        bash_executor = BashExecutor(self.home)

        # 创建路由器并注入依赖
        router = MessageRouter(executor, memory, adapter, bot_open_id, bot_name)
        router.config = self.config  # 注入配置引用（用于主人身份自动发现等）
        _stats_router_ref[0] = router  # 完成闭包引用
        router.session_mgr = session_mgr
        router.calendar = calendar
        router.stats = stats
        router.cc_executor = cc_executor
        router.bash_executor = bash_executor
        router.tool_registry = tool_registry
        self._router = router
        logger.info("会话管理器已加载（含 Claude Code + Bash 执行器）")

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
        logger.info("后处理管线已加载")

        # 初始化自进化引擎 + 启动守护检查
        self._evolution = EvolutionEngine(
            self.home,
            max_daily=self.config.evolution_max_daily,
        )
        if self._evolution.source_root:
            logger.info("自进化引擎已加载: source=%s, max_daily=%d",
                        self._evolution.source_root, self.config.evolution_max_daily)
            # 进化守护：检查上次进化是否导致崩溃
            was_clean = self._was_clean_shutdown()
            if not was_clean:
                logger.warning("检测到上次非正常退出，检查进化安全性...")
            self._evolution.startup_check(was_clean)
        else:
            logger.warning("自进化引擎: 无法定位源代码目录，进化功能受限")
        # 标记本次启动（清除 clean shutdown 标记，在 _cleanup 中重新写入）
        self._clean_shutdown_path.unlink(missing_ok=True)

        # 配置心跳回调
        heartbeat = HeartbeatRunner(
            self.config.heartbeat_interval,
            self.config.active_hours,
            self.home,
        )
        heartbeat.on_heartbeat = self._make_heartbeat_callback(
            executor, memory, adapter, calendar, stats, router
        )

        # 通过适配器启动连接
        await adapter.connect(self.queue)
        logger.info("适配器已连接: %s", "+".join(self.adapter_types))

        # 并发运行消费者、心跳、会话自动保存
        tasks = [
            asyncio.create_task(self._consume_messages(router, loop), name="consumer"),
            asyncio.create_task(heartbeat.run_forever(self.shutdown_event), name="heartbeat"),
            asyncio.create_task(self._auto_save_sessions(session_mgr), name="autosave"),
        ]
        # inbox 轮询：local adapter 已内置 inbox 监听，无需重复；纯飞书模式保留
        if not has_local:
            tasks.append(asyncio.create_task(self._poll_inbox(), name="inbox"))

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

        # 关闭时保存会话并断开适配器
        session_mgr.save()
        await adapter.disconnect()
        logger.info("会话已保存，适配器已断开，关闭完成")

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

    def _make_heartbeat_callback(self, executor, memory, adapter, calendar, stats, router):
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

                    now = datetime.now()
                    start = now.replace(hour=0, minute=0, second=0).isoformat()
                    end = now.replace(hour=23, minute=59, second=59).isoformat()
                    events = await calendar.list_events(start, end)

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
                        await adapter.send(OutgoingMessage(owner_chat_id, greeting))
                        logger.info("晨报已发送至 %s", owner_chat_id)
                    else:
                        logger.warning("晨报已生成但未发送：未配置 owner_chat_id")
                except Exception:
                    logger.exception("晨报生成失败")

            # 每日群聊早安问候（仅飞书适配器支持）
            if is_daily_first and hasattr(adapter, 'known_group_ids'):
                try:
                    known = adapter.known_group_ids
                    if known:
                        self._schedule_morning_greetings(
                            known, executor, memory, adapter,
                            config.name,
                        )
                except Exception:
                    logger.exception("群聊早安问候调度失败")

            # 自主行动：好奇心探索 + 自我进化（统一系统）
            try:
                await self._run_autonomous_cycle(router, stats)
            except Exception:
                logger.exception("自主行动周期失败")

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
            # 注入今日反思摘要和工具统计（漂移检测上下文）
            system += self._build_heartbeat_drift_context(router)
            chat_id = self.config.feishu.owner_chat_id or "heartbeat"
            messages = [{"role": "user", "content": "请检查并执行心跳任务。"}]
            result = await router._reply_with_tool_loop(system, messages, chat_id, None)
            if result and result.strip() and result.strip() != "无":
                owner_chat_id = self.config.feishu.owner_chat_id
                if owner_chat_id:
                    await router.adapter.send(OutgoingMessage(owner_chat_id, result))
                router.memory.append_daily(f"- 心跳任务执行: {result[:100]}\n")
                logger.info("心跳任务执行: %s", result[:80])
        except Exception:
            logger.exception("心跳任务执行失败")

    def _build_heartbeat_drift_context(self, router: MessageRouter) -> str:
        """构建心跳任务的漂移检测上下文（反思摘要 + 工具统计）"""
        parts: list[str] = []
        # 今日反思摘要
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        ref_path = self.home / "logs" / f"reflections-{today}.jsonl"
        if ref_path.exists():
            try:
                lines = ref_path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    reflections = []
                    for line in lines[-10:]:  # 最近10条
                        entry = json.loads(line)
                        reflections.append(f"  - {entry.get('reflection', '')}")
                    parts.append("\n### 今日自我反思记录\n" + "\n".join(reflections))
            except Exception:
                pass
        # 工具成功/失败摘要
        if router._tool_stats:
            tool_lines = []
            for tname, ts in router._tool_stats.items():
                total = ts.get("success", 0) + ts.get("fail", 0)
                if total > 0:
                    rate = round(ts["success"] / total * 100)
                    tool_lines.append(f"  - {tname}: {total}次, 成功率{rate}%")
            if tool_lines:
                parts.append("\n### 工具使用摘要\n" + "\n".join(tool_lines))
        if parts:
            return "\n" + "\n".join(parts) + "\n请对比 SOUL.md 中的行为准则，判断是否存在行为漂移。"
        return ""

    async def _run_autonomous_cycle(
        self, router: MessageRouter, stats: StatsTracker,
    ) -> None:
        """统一的自主行动周期：好奇心驱动探索与自我进化。

        好奇心是驱动力，它决定每个周期做什么：
        - 探索外部世界（学习新知识、创建新工具）
        - 审视并改进自身框架代码（自我进化）

        由 LLM 在统一的 prompt 下自主决策行动方向。
        """
        from lq.prompts import CURIOSITY_EXPLORE_PROMPT, CURIOSITY_INIT_TEMPLATE

        # 预算检查：好奇心预算 + 进化预算 = 自主行动总预算
        autonomous_budget = self.config.curiosity_budget + self.config.evolution_budget
        if stats:
            daily = stats.get_daily_summary()
            today_cost = daily.get("total_cost", 0.0)
            ceiling = self.config.cost_alert_daily - autonomous_budget
            if today_cost > ceiling:
                logger.debug(
                    "今日费用 $%.4f 超过自主行动阈值 $%.2f "
                    "(总预算 $%.2f - 自主预算 $%.2f)，跳过",
                    today_cost, ceiling,
                    self.config.cost_alert_daily, autonomous_budget,
                )
                return

        # ── 收集好奇心上下文 ──
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")

        # 好奇心信号（最近 20 条）
        signals_path = self.home / "logs" / f"curiosity-signals-{today}.jsonl"
        signals_text = "（暂无信号）"
        if signals_path.exists():
            try:
                lines = signals_path.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    entries = []
                    for line in lines[-20:]:
                        entry = json.loads(line)
                        entries.append(f"- {entry.get('topic', '?')}（来源: {entry.get('source', '?')}）")
                    signals_text = "\n".join(entries)
            except Exception:
                logger.debug("读取好奇心信号失败", exc_info=True)

        # CURIOSITY.md
        curiosity_path = self.home / "CURIOSITY.md"
        if not curiosity_path.exists():
            curiosity_path.write_text(CURIOSITY_INIT_TEMPLATE, encoding="utf-8")
            logger.info("已创建 CURIOSITY.md")
        curiosity_md = curiosity_path.read_text(encoding="utf-8")

        # ── 收集进化上下文 ──
        evolution_md = "（进化引擎未加载）"
        source_summary = "（无源代码信息）"
        git_log = "（无 git 信息）"
        source_root = ""
        remaining_today = 0

        if self._evolution:
            self._evolution.ensure_evolution_file()
            evolution_md = self._evolution.read_evolution()
            remaining_today = self._evolution.remaining_today
            if self._evolution.source_root:
                source_summary = self._evolution.get_source_summary()
                git_log = self._evolution.get_recent_git_log()
                source_root = str(self._evolution.source_root)

        # 如果没有任何驱动力（无信号、无兴趣、无待办），跳过
        has_curiosity = signals_text != "（暂无信号）" or "## 当前兴趣\n\n##" not in curiosity_md
        has_evolution_backlog = "## 待办\n" in evolution_md and not evolution_md.endswith("## 待办\n发现但尚未实施的改进：\n\n## 进行中\n\n## 已完成\n\n## 失败记录\n")
        if not has_curiosity and not has_evolution_backlog:
            logger.debug("无好奇心信号、无当前兴趣、无进化待办，跳过自主行动")
            return

        # 反思和工具统计
        reflections_summary = self._get_reflections_summary()
        tool_stats_summary = self._get_tool_stats_summary(router)

        # ── 构建统一 prompt ──
        system = router.memory.build_context()
        system += "\n\n" + CURIOSITY_EXPLORE_PROMPT.format(
            signals=signals_text,
            curiosity_md=curiosity_md,
            evolution_md=evolution_md,
            source_summary=source_summary,
            git_log=git_log,
            remaining_today=remaining_today,
            reflections_summary=reflections_summary,
            tool_stats_summary=tool_stats_summary,
            source_root=source_root or "（未知）",
        )

        chat_id = self.config.feishu.owner_chat_id or "autonomous"
        messages = [{"role": "user", "content": "请根据你的好奇心决定下一步行动。"}]

        # 记录行动前的文件状态用于变更检测
        old_curiosity = curiosity_md
        old_evolution = evolution_md

        # 进化守护：如果可能执行进化，先保存 checkpoint
        if self._evolution and remaining_today > 0 and self._evolution.source_root:
            self._evolution.save_checkpoint()

        try:
            result = await router._reply_with_tool_loop(
                system, messages, chat_id, None,
            )
            if not result or not result.strip() or result.strip() == "无":
                # 没有行动，清除 checkpoint
                if self._evolution:
                    self._evolution.clear_checkpoint()
                logger.debug("自主行动周期: 无需行动")
                return

            router.memory.append_daily(f"- 自主行动: {result[:100]}\n")
            logger.info("自主行动周期完成: %s", result[:80])

            # 检测是否执行了进化（EVOLUTION.md 发生了变化）
            did_evolve = False
            if self._evolution and self._evolution.evolution_path.exists():
                new_evolution = self._evolution.evolution_path.read_text(encoding="utf-8")
                if new_evolution != old_evolution:
                    did_evolve = True
                    self._evolution.record_attempt()
                    logger.info("检测到进化行为，已计数")

            # 进化守护：如果没有执行进化，清除 checkpoint
            # （如果执行了进化，保留 checkpoint 等下次启动验证）
            if self._evolution and not did_evolve:
                self._evolution.clear_checkpoint()

            # 检测好奇心日志变化（保持原有的改进建议通知逻辑）
            new_curiosity = curiosity_path.read_text(encoding="utf-8")
            if new_curiosity != old_curiosity and "改进建议" in new_curiosity:
                import re as _re
                m = _re.search(r"##\s*改进建议\s*\n(.*?)(?:\n##|\Z)",
                               new_curiosity, _re.DOTALL)
                section = m.group(1).strip() if m else ""
                owner_chat_id = self.config.feishu.owner_chat_id
                if section and owner_chat_id:
                    await router.adapter.send(OutgoingMessage(
                        owner_chat_id,
                        "我在探索中发现了一些改进建议，已记录在 CURIOSITY.md 中。",
                    ))

        except Exception:
            logger.exception("自主行动周期执行失败")

    def _get_reflections_summary(self) -> str:
        """收集今日反思日志摘要"""
        from datetime import datetime as _dt, timedelta as _td, timezone as _tz
        cst = _tz(_td(hours=8))
        today = _dt.now(cst).strftime("%Y-%m-%d")
        ref_path = self.home / "logs" / f"reflections-{today}.jsonl"
        if not ref_path.exists():
            return "（今日暂无反思记录）"
        try:
            lines = ref_path.read_text(encoding="utf-8").strip().splitlines()
            if not lines:
                return "（今日暂无反思记录）"
            entries = []
            for line in lines[-15:]:  # 最近 15 条
                entry = json.loads(line)
                entries.append(f"- {entry.get('reflection', '')}")
            return "\n".join(entries)
        except Exception:
            return "（反思记录读取失败）"

    def _get_tool_stats_summary(self, router: MessageRouter) -> str:
        """收集工具使用统计摘要"""
        if not router._tool_stats:
            return "（暂无工具使用记录）"
        lines = []
        for tname, ts in router._tool_stats.items():
            total = ts.get("success", 0) + ts.get("fail", 0)
            if total > 0:
                rate = round(ts["success"] / total * 100)
                last_err = ts.get("last_error", "")
                line = f"- {tname}: {total}次, 成功率{rate}%"
                if last_err:
                    line += f" (最近错误: {last_err[:80]})"
                lines.append(line)
        return "\n".join(lines) if lines else "（暂无工具使用记录）"

    async def _poll_inbox(self) -> None:
        """轮询 inbox.txt，构造标准事件推入 queue（走完整的适配器路径）。"""
        inbox_path = self.home / "inbox.txt"
        chat_id = self.config.feishu.owner_chat_id or "local_cli"
        msg_counter = 0
        while not self.shutdown_event.is_set():
            try:
                try:
                    await asyncio.wait_for(
                        self.shutdown_event.wait(), timeout=2.0,
                    )
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
                    msg_counter += 1
                    msg = IncomingMessage(
                        message_id=f"inbox_{msg_counter}",
                        chat_id=chat_id,
                        chat_type=ChatType.PRIVATE,
                        sender_id="local_cli_user",
                        sender_type=SenderType.USER,
                        sender_name="用户",
                        message_type=MessageType.TEXT,
                        text=line,
                    )
                    await self.queue.put({"event_type": "message", "message": msg})
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("inbox 轮询异常")


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
            except Exception:
                logger.exception("自动保存会话失败")


    def _schedule_morning_greetings(
        self,
        known_groups: set[str],
        executor: Any,
        memory: Any,
        adapter: Any,
        bot_name: str,
    ) -> None:
        """为每个已知群聊安排延迟早安问候"""
        today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
        for chat_id in known_groups:
            # deterministic jitter: 0-1800 秒，基于 hash 保证重启不重发
            h = hashlib.md5(f"{bot_name}:{chat_id}:{today}".encode()).hexdigest()
            delay = int(h[:8], 16) % 1800
            asyncio.ensure_future(
                self._do_morning_greeting(
                    chat_id, delay, executor, memory, adapter,
                )
            )
        logger.info("已安排 %d 个群聊的早安问候", len(known_groups))

    async def _do_morning_greeting(
        self,
        chat_id: str,
        delay: int,
        executor: Any,
        memory: Any,
        adapter: Any,
    ) -> None:
        """延迟后发送早安问候"""
        from lq.prompts import MORNING_GREETING_SYSTEM, MORNING_GREETING_USER
        try:
            await asyncio.sleep(delay)
            # 生成问候
            soul = memory.read_soul()
            system = MORNING_GREETING_SYSTEM.format(soul=soul)
            greeting = await executor.reply(system, MORNING_GREETING_USER)
            greeting = greeting.strip()
            if greeting:
                await adapter.send(OutgoingMessage(chat_id, greeting))
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

    @property
    def _clean_shutdown_path(self) -> Path:
        return self.home / ".clean-shutdown"

    def _was_clean_shutdown(self) -> bool:
        """检测上次运行是否正常关闭。

        正常关闭时 _cleanup 会写入 .clean-shutdown 标记文件；
        如果标记不存在，说明上次是崩溃退出。
        """
        return self._clean_shutdown_path.exists()

    def _write_pid(self) -> None:
        pid_path = self.home / "gateway.pid"
        pid_path.write_text(str(os.getpid()))
        logger.info("PID %d 写入 %s", os.getpid(), pid_path)

    def _cleanup(self) -> None:
        pid_path = self.home / "gateway.pid"
        if pid_path.exists():
            pid_path.unlink()
            logger.info("PID 文件已清理")
        # 标记正常关闭，供下次启动时判断是否需要回滚进化
        try:
            self._clean_shutdown_path.write_text(
                datetime.now(CST).isoformat(), encoding="utf-8",
            )
            logger.info("clean shutdown 标记已写入")
        except Exception:
            logger.warning("clean shutdown 标记写入失败")

    def _setup_signals(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._handle_signal, sig)

    def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info("收到信号 %s，正在关闭...", sig.name)
        self.shutdown_event.set()
