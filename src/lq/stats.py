"""API 消耗统计"""

from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class StatsTracker:
    def __init__(self, home: Path) -> None:
        self.home = home
        self.stats_file = home / "stats.jsonl"

    def record(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        call_type: str = "reply",
        cost_usd: float = 0.0,
    ) -> None:
        """记录一次 API 调用"""
        entry = {
            "ts": time.time(),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "call_type": call_type,
            "cost_usd": cost_usd,
        }
        with open(self.stats_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_daily_summary(self, target_date: date | None = None) -> dict:
        """获取指定日期的消耗汇总"""
        d = target_date or date.today()
        start = datetime.combine(d, datetime.min.time()).timestamp()
        end = start + 86400

        total_calls = 0
        total_input = 0
        total_output = 0
        total_cost = 0.0
        by_type: dict[str, int] = {}

        for entry in self._read_entries():
            if start <= entry["ts"] < end:
                total_calls += 1
                total_input += entry.get("input_tokens", 0)
                total_output += entry.get("output_tokens", 0)
                total_cost += entry.get("cost_usd", 0.0)
                ct = entry.get("call_type", "unknown")
                by_type[ct] = by_type.get(ct, 0) + 1

        return {
            "date": d.isoformat(),
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost": total_cost,
            "by_type": by_type,
        }

    def get_monthly_summary(self, year: int | None = None, month: int | None = None) -> dict:
        """获取月度消耗汇总"""
        now = date.today()
        y = year or now.year
        m = month or now.month

        total_calls = 0
        total_input = 0
        total_output = 0
        total_cost = 0.0

        for entry in self._read_entries():
            dt = datetime.fromtimestamp(entry["ts"])
            if dt.year == y and dt.month == m:
                total_calls += 1
                total_input += entry.get("input_tokens", 0)
                total_output += entry.get("output_tokens", 0)
                total_cost += entry.get("cost_usd", 0.0)

        return {
            "year": y,
            "month": m,
            "total_calls": total_calls,
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost": total_cost,
        }

    def _read_entries(self) -> list[dict]:
        if not self.stats_file.exists():
            return []
        entries = []
        with open(self.stats_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        return entries
