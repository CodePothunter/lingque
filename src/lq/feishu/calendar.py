"""飞书日历 API"""

from __future__ import annotations

import logging
from datetime import datetime

import lark_oapi as lark
from lark_oapi.api.calendar.v4 import (
    CalendarEvent,
    CreateCalendarEventRequest,
    ListCalendarEventRequest,
)
from lark_oapi.api.calendar.v4.model.time_info import TimeInfo

logger = logging.getLogger(__name__)


class FeishuCalendar:
    def __init__(self, client: lark.Client) -> None:
        self.client = client
        self._primary_calendar_id: str | None = None

    async def get_primary_calendar_id(self) -> str:
        """获取主日历 ID"""
        if self._primary_calendar_id:
            return self._primary_calendar_id

        # 列出日历，找到主日历
        from lark_oapi.api.calendar.v4 import ListCalendarRequest
        req = ListCalendarRequest.builder().page_size(50).build()
        resp = await self.client.calendar.v4.calendar.alist(req)
        if resp.code == 0 and resp.data and resp.data.calendar_list:
            for cal in resp.data.calendar_list:
                if cal.role == "owner":
                    self._primary_calendar_id = cal.calendar_id
                    break
            if not self._primary_calendar_id and resp.data.calendar_list:
                self._primary_calendar_id = resp.data.calendar_list[0].calendar_id
        return self._primary_calendar_id or ""

    async def create_event(
        self,
        summary: str,
        start_time: str,
        end_time: str,
        description: str = "",
    ) -> dict:
        """创建日历事件

        Args:
            summary: 事件标题
            start_time: ISO 8601 格式 (如 2024-01-01T15:00:00+08:00)
            end_time: ISO 8601 格式
            description: 事件描述
        """
        calendar_id = await self.get_primary_calendar_id()
        if not calendar_id:
            return {"success": False, "error": "未找到主日历"}

        start_ts = self._iso_to_timestamp(start_time)
        end_ts = self._iso_to_timestamp(end_time)

        start_info = TimeInfo()
        start_info.timestamp = str(start_ts)
        end_info = TimeInfo()
        end_info.timestamp = str(end_ts)

        event = (
            CalendarEvent.builder()
            .summary(summary)
            .description(description)
            .start_time(start_info)
            .end_time(end_info)
            .build()
        )

        req = (
            CreateCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .request_body(event)
            .build()
        )

        resp = await self.client.calendar.v4.calendar_event.acreate(req)
        if resp.code != 0:
            logger.error("创建日历事件失败: %d %s", resp.code, resp.msg)
            return {"success": False, "error": resp.msg}

        event_id = resp.data.event.event_id if resp.data and resp.data.event else ""
        logger.info("日历事件已创建: %s (%s)", summary, event_id)
        return {"success": True, "event_id": event_id}

    async def list_events(
        self,
        start_time: str,
        end_time: str,
    ) -> list[dict]:
        """查询日历事件

        Args:
            start_time: ISO 8601 格式
            end_time: ISO 8601 格式
        """
        calendar_id = await self.get_primary_calendar_id()
        if not calendar_id:
            return []

        start_ts = self._iso_to_timestamp(start_time)
        end_ts = self._iso_to_timestamp(end_time)

        req = (
            ListCalendarEventRequest.builder()
            .calendar_id(calendar_id)
            .start_time(str(start_ts))
            .end_time(str(end_ts))
            .page_size(50)
            .build()
        )

        resp = await self.client.calendar.v4.calendar_event.alist(req)
        if resp.code != 0:
            logger.error("查询日历事件失败: %d %s", resp.code, resp.msg)
            return []

        events = []
        if resp.data and resp.data.items:
            for item in resp.data.items:
                events.append({
                    "event_id": item.event_id,
                    "summary": item.summary or "未命名事件",
                    "description": item.description or "",
                    "start_time": self._timestamp_to_display(item.start_time),
                    "end_time": self._timestamp_to_display(item.end_time),
                })
        return events

    @staticmethod
    def _iso_to_timestamp(iso_str: str) -> int:
        """ISO 8601 → Unix timestamp"""
        dt = datetime.fromisoformat(iso_str)
        return int(dt.timestamp())

    @staticmethod
    def _timestamp_to_display(time_info: TimeInfo | None) -> str:
        """TimeInfo → 可读时间字符串"""
        if not time_info or not time_info.timestamp:
            return ""
        ts = int(time_info.timestamp)
        return datetime.fromtimestamp(ts).strftime("%H:%M")
