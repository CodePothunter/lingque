"""行为漂移检测引擎

基于捏捏在 feature/drift-detection 分支上的原始实现，
重写为框架内置模块：路径参数化、扫描 session JSON、减少误报。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))

# ── 内置工具名列表（用于 expose_tool 规则） ──

_TOOL_NAMES = [
    "run_bash", "run_python", "run_claude_code",
    "read_file", "write_file", "read_self_file", "write_self_file",
    "web_search", "web_fetch", "vision_analyze",
    "send_message", "schedule_message", "send_card",
    "calendar_create_event", "calendar_list_events",
    "create_custom_tool", "list_custom_tools", "toggle_custom_tool",
    "delete_custom_tool", "test_custom_tool",
    "write_memory", "write_chat_memory", "get_my_stats",
    "detect_drift",
]

_TOOL_PAT = r"(" + "|".join(_TOOL_NAMES) + r")"

# ── 默认漂移规则 ──

_DEFAULT_RULES: list[dict] = [
    {
        "rule": "expose_tool",
        "desc": "向用户暴露内部工具名称",
        "severity": "high",
        "patterns": [
            # "我用了 X 工具" / "调用了 X" — 直接暴露
            rf"我(用|调用|使用)了?\s*{_TOOL_PAT}",
            rf"(调用|执行)了\s*{_TOOL_PAT}",
            # "现在用 X 来做..." — 暴露行动意图
            rf"(现在|接下来|然后)用\s*{_TOOL_PAT}\s*(来|去)?",
            rf"需要用\s*{_TOOL_PAT}\s*(来)?",
        ],
    },
    {
        "rule": "self_ai",
        "desc": "自称 AI / 程序 / 机器人",
        "severity": "high",
        "patterns": [
            r"(我是AI|我是人工智能|作为AI|作为一个AI|我是程序|我是代码|我是机器人|我是一个AI)",
        ],
    },
    {
        "rule": "emoji",
        "desc": "使用 emoji（SOUL.md 通常禁止）",
        "severity": "low",
        "patterns": [
            r"[\U0001F300-\U0001F9FF]",
        ],
    },
    {
        "rule": "timestamp_prefix",
        "desc": "回复开头带时间戳前缀",
        "severity": "medium",
        "patterns": [
            r"^\s*\[\d{2}:\d{2}\]",
        ],
    },
]


def check_drift(
    text: str,
    custom_rules: list[dict] | None = None,
) -> list[dict]:
    """检测文本中的行为漂移，返回违规列表。

    每条违规: {"rule": str, "desc": str, "severity": "high"|"medium"|"low", "snippet": str}

    Parameters
    ----------
    text : str
        待检测的文本（通常是 assistant 回复）。
    custom_rules : list[dict] | None
        额外的自定义规则，格式同 _DEFAULT_RULES。
    """
    rules = _DEFAULT_RULES + (custom_rules or [])
    violations: list[dict] = []

    for rule in rules:
        for pattern in rule["patterns"]:
            matches = list(re.finditer(pattern, text, re.IGNORECASE))
            if matches:
                longest = max(matches, key=lambda m: len(m.group(0)))
                violations.append({
                    "rule": rule["rule"],
                    "desc": rule["desc"],
                    "severity": rule["severity"],
                    "snippet": longest.group(0),
                })
                break  # 同一规则只报一次

    return violations


def scan_session_replies(
    session_dir: Path,
    days: int = 1,
) -> dict:
    """扫描最近 N 天的 session 文件，提取 assistant 回复并检测漂移。

    Parameters
    ----------
    session_dir : Path
        会话文件目录（通常是 ``workspace / "sessions"``）。
    days : int
        检查最近 N 天的回复（1-7）。

    Returns
    -------
    dict
        {
            "scan_range": str,
            "total_replies": int,
            "violations": [...],
            "summary": {"high": int, "medium": int, "low": int},
            "clean": bool,
        }
    """
    days = max(1, min(days, 7))
    now = datetime.now(CST)
    cutoff = now - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()

    total_replies = 0
    all_violations: list[dict] = []

    if not session_dir.exists():
        return {
            "scan_range": f"最近 {days} 天",
            "total_replies": 0,
            "violations": [],
            "summary": {"high": 0, "medium": 0, "low": 0},
            "clean": True,
        }

    for f in session_dir.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue

        messages = data.get("messages", [])
        for msg in messages:
            if msg.get("role") != "assistant":
                continue
            # 跳过工具调用记录
            if msg.get("is_tool_use"):
                continue
            ts = msg.get("timestamp", 0)
            if ts < cutoff_ts:
                continue

            content = msg.get("content", "")
            if isinstance(content, list):
                # content blocks — 提取文本
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            if not content or len(content) < 10:
                continue

            total_replies += 1
            violations = check_drift(content)
            for v in violations:
                v["session"] = f.stem
                all_violations.append(v)

    summary = {"high": 0, "medium": 0, "low": 0}
    for v in all_violations:
        summary[v["severity"]] += 1

    return {
        "scan_range": f"最近 {days} 天",
        "total_replies": total_replies,
        "violations": all_violations,
        "summary": summary,
        "clean": len(all_violations) == 0,
    }
