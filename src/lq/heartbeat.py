"""心跳调度"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class HeartbeatRunner:
    def __init__(
        self,
        interval: int,
        active_hours: tuple[int, int],
        workspace: Path | None = None,
        min_interval: int = 300,
        bored_threshold: int = 3,
    ) -> None:
        self.interval = interval
        self.base_interval = interval        # 基准间隔（用于退避上限）
        self.min_interval = min_interval     # 最短间隔（秒）
        self._current_interval = interval    # 当前实际间隔
        self.active_hours = active_hours
        self.workspace = workspace
        self._shutdown_event: asyncio.Event | None = None

        # 回调（Phase 3+ 注入）
        self.on_heartbeat: Any = None

        # 频率控制（从持久化文件恢复）
        self._last_daily: str | None = None
        self._last_weekly: str | None = None
        
        # 无聊感算法：追踪连续空闲次数
        self._idle_streak = 0
        self._bored_threshold = bored_threshold  # 触发"无聊"的阈值
        
        self._load_state()

    def notify_did_work(self) -> None:
        """自主行动做了有意义的事，缩短下次间隔，重置空闲计数。"""
        self._current_interval = self.min_interval
        self._idle_streak = 0
        logger.info("心跳间隔缩短至 %ds（有自主行动），空闲计数重置", self._current_interval)

    def notify_idle(self) -> None:
        """自主行动无事可做，指数退避恢复间隔，增加空闲计数。"""
        self._current_interval = min(
            self._current_interval * 2,
            self.base_interval,
        )
        self._idle_streak += 1
        logger.info(
            "心跳间隔调整为 %ds（无事可做，退避），空闲连续 %d 次",
            self._current_interval, self._idle_streak,
        )

    def is_bored(self) -> bool:
        """是否处于"无聊"状态（连续多次空闲）。
        
        当返回 True 时，建议触发主动探索模式。
        """
        return self._idle_streak >= self._bored_threshold

    def get_idle_streak(self) -> int:
        """获取当前空闲连续次数。"""
        return self._idle_streak

    async def run_forever(self, shutdown_event: asyncio.Event) -> None:
        """定时循环，检查活跃时段"""
        self._shutdown_event = shutdown_event
        logger.info(
            "心跳启动: 基准间隔=%ds, 最短间隔=%ds, 活跃时段=%s, 无聊阈值=%d",
            self.base_interval, self.min_interval, self.active_hours, self._bored_threshold,
        )

        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=self._current_interval,
                )
                break  # shutdown_event 被设置
            except asyncio.TimeoutError:
                pass  # 正常超时，执行心跳

            if self._is_active_hour():
                await self._heartbeat()
            else:
                logger.debug("非活跃时段，跳过心跳")

    async def _heartbeat(self) -> None:
        """执行一次心跳"""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        weekday = now.strftime("%A")

        logger.info("心跳触发: %s", now.isoformat())

        is_daily = self._last_daily != today
        is_weekly = is_daily and weekday == "Monday" and self._last_weekly != today

        if self.on_heartbeat:
            try:
                await self.on_heartbeat(
                    is_daily_first=is_daily,
                    is_weekly_first=is_weekly,
                )
            except Exception:
                logger.exception("心跳回调执行失败")

        if is_daily:
            self._last_daily = today
        if is_weekly:
            self._last_weekly = today
        if is_daily or is_weekly:
            self._save_state()

    def _state_path(self) -> Path | None:
        if self.workspace:
            return self.workspace / "heartbeat-state.json"
        return None

    def _load_state(self) -> None:
        p = self._state_path()
        if not p or not p.exists():
            return
        try:
            data = json.loads(p.read_text())
            self._last_daily = data.get("last_daily")
            self._last_weekly = data.get("last_weekly")
            self._idle_streak = data.get("idle_streak", 0)
            logger.info(
                "心跳状态已恢复: daily=%s weekly=%s idle_streak=%d",
                self._last_daily, self._last_weekly, self._idle_streak,
            )
        except Exception:
            logger.warning("心跳状态文件损坏，忽略")

    def _save_state(self) -> None:
        p = self._state_path()
        if not p:
            return
        try:
            p.write_text(json.dumps({
                "last_daily": self._last_daily,
                "last_weekly": self._last_weekly,
                "idle_streak": self._idle_streak,
            }))
        except Exception:
            logger.warning("心跳状态保存失败")

    def _is_active_hour(self) -> bool:
        hour = datetime.now().hour
        start, end = self.active_hours
        if start <= end:
            return start <= hour < end
        else:  # 跨午夜
            return hour >= start or hour < end
