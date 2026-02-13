"""心跳调度"""

from __future__ import annotations

import asyncio
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
    ) -> None:
        self.interval = interval
        self.active_hours = active_hours
        self.workspace = workspace
        self.shutdown_event: asyncio.Event | None = None

        # 回调（Phase 3+ 注入）
        self.on_heartbeat: Any = None

        # 频率控制
        self._last_daily: str | None = None
        self._last_weekly: str | None = None

    async def run_forever(self, shutdown_event: asyncio.Event) -> None:
        """定时循环，检查活跃时段"""
        self.shutdown_event = shutdown_event
        logger.info("心跳启动: 间隔=%ds, 活跃时段=%s", self.interval, self.active_hours)

        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(),
                    timeout=self.interval,
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

    def _is_active_hour(self) -> bool:
        hour = datetime.now().hour
        start, end = self.active_hours
        if start <= end:
            return start <= hour < end
        else:  # 跨午夜
            return hour >= start or hour < end
