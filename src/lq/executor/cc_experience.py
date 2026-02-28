"""CC 执行经验持久化 — 记录每次 Claude Code 执行的结构化信息"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))


@dataclass
class CCExperienceEntry:
    """一次 CC 执行的完整记录"""

    # 原始关键信息
    timestamp: float = 0.0
    session_id: str = ""
    prompt: str = ""
    working_dir: str = ""
    success: bool = False
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0
    tools_used: list[str] = field(default_factory=list)
    tool_calls: list[dict] = field(default_factory=list)
    files_modified: list[str] = field(default_factory=list)
    text_outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    # 经验层
    trace_summary: str = ""
    lessons: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> CCExperienceEntry:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


class CCExperienceStore:
    """CC 执行经验存储"""

    def __init__(self, workspace: Path) -> None:
        self.dir = workspace / "cc_experience"
        self.dir.mkdir(parents=True, exist_ok=True)

    def _today_path(self) -> Path:
        today = datetime.now(CST).strftime("%Y-%m-%d")
        return self.dir / f"{today}.jsonl"

    def record(self, entry: CCExperienceEntry) -> None:
        """追加一条执行记录"""
        path = self._today_path()
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry.to_dict(), ensure_ascii=False) + "\n")
            logger.info(
                "CC 经验已记录: session=%s success=%s cost=$%.4f",
                entry.session_id[:12], entry.success, entry.cost_usd,
            )
        except Exception:
            logger.exception("CC 经验记录失败")

    def query_similar(self, prompt: str, limit: int = 3) -> list[CCExperienceEntry]:
        """通过关键词匹配找到相似的历史执行"""
        # 提取关键词（中文分词简化为字符 bigram + 英文单词）
        keywords = self._extract_keywords(prompt)
        if not keywords:
            return self.get_recent(limit)

        scored: list[tuple[float, CCExperienceEntry]] = []
        for path in sorted(self.dir.glob("*.jsonl"), reverse=True):
            try:
                for line in path.read_text(encoding="utf-8").strip().splitlines():
                    entry = CCExperienceEntry.from_dict(json.loads(line))
                    entry_keywords = self._extract_keywords(entry.prompt)
                    overlap = len(keywords & entry_keywords)
                    if overlap > 0:
                        score = overlap / max(len(keywords), 1)
                        scored.append((score, entry))
            except Exception:
                continue
            # 搜索最近 7 天即可
            if len(scored) > limit * 10:
                break

        scored.sort(key=lambda x: x[0], reverse=True)
        return [entry for _, entry in scored[:limit]]

    def get_recent(self, limit: int = 5) -> list[CCExperienceEntry]:
        """获取最近的执行记录"""
        results: list[CCExperienceEntry] = []
        for path in sorted(self.dir.glob("*.jsonl"), reverse=True):
            try:
                lines = path.read_text(encoding="utf-8").strip().splitlines()
                for line in reversed(lines):
                    results.append(CCExperienceEntry.from_dict(json.loads(line)))
                    if len(results) >= limit:
                        return results
            except Exception:
                continue
        return results

    def get_stats(self) -> dict:
        """总执行数、成功率、平均成本"""
        total = 0
        success_count = 0
        total_cost = 0.0
        for path in self.dir.glob("*.jsonl"):
            try:
                for line in path.read_text(encoding="utf-8").strip().splitlines():
                    entry = json.loads(line)
                    total += 1
                    if entry.get("success"):
                        success_count += 1
                    total_cost += entry.get("cost_usd", 0.0)
            except Exception:
                continue
        return {
            "total_executions": total,
            "success_rate": round(success_count / total, 2) if total > 0 else 0,
            "total_cost_usd": round(total_cost, 4),
            "avg_cost_usd": round(total_cost / total, 4) if total > 0 else 0,
        }

    @staticmethod
    def _extract_keywords(text: str) -> set[str]:
        """提取关键词：英文单词 + 中文字符 bigram"""
        words = set(re.findall(r"[a-zA-Z_]\w{2,}", text.lower()))
        # 中文 bigram
        chinese = re.findall(r"[\u4e00-\u9fff]+", text)
        for seg in chinese:
            for i in range(len(seg) - 1):
                words.add(seg[i : i + 2])
        return words
