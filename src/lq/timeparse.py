"""中文时间表达式确定性解析器 — 零 LLM 成本"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

CST = timezone(timedelta(hours=8))

# 中文数字映射
_CN_DIGITS: dict[str, int] = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10,
    "十一": 11, "十二": 12, "半": 30,
}


def _cn_to_int(s: str) -> int | None:
    """将中文数字字符串转为整数，支持 '十二'、'二十三' 等。"""
    s = s.strip()
    if not s:
        return None
    # 纯阿拉伯数字
    if s.isdigit():
        return int(s)
    # 直接命中
    if s in _CN_DIGITS:
        return _CN_DIGITS[s]
    # "十X" → 10+X
    if s.startswith("十"):
        rest = s[1:]
        if not rest:
            return 10
        unit = _CN_DIGITS.get(rest)
        return 10 + unit if unit is not None else None
    # "X十Y" → X*10+Y
    if "十" in s:
        parts = s.split("十", 1)
        tens = _CN_DIGITS.get(parts[0])
        if tens is None:
            return None
        if not parts[1]:
            return tens * 10
        ones = _CN_DIGITS.get(parts[1])
        return tens * 10 + ones if ones is not None else None
    return None


# 时段 → 小时偏移判定
_PERIOD_MAP: dict[str, tuple[int, int]] = {
    "凌晨": (0, 6),
    "早上": (6, 12),
    "上午": (6, 12),
    "中午": (11, 14),
    "下午": (12, 18),
    "傍晚": (16, 19),
    "晚上": (18, 24),
    "晚": (18, 24),
}


def _apply_period(hour: int, period: str | None) -> int:
    """根据时段调整小时数（如 '下午3点' → 15）"""
    if period is None:
        return hour
    period = period.strip()
    if period in ("下午", "晚上", "晚", "傍晚") and hour < 12:
        return hour + 12
    if period in ("凌晨", "早上", "上午") and hour == 12:
        return 0
    return hour


# ──── 相对时间 ────

_REL_PATTERN = re.compile(
    r"(?:再过|过)?"
    r"(\d+|[一二两三四五六七八九十百]+)"
    r"\s*(?:个)?"
    r"(分钟|小时|个小时|天)"
    r"(?:后|之后|以后)?",
)

_HALF_HOUR_PATTERN = re.compile(r"半\s*(?:个)?\s*小时\s*(?:后|之后|以后)?")


def _parse_relative(text: str, now: datetime) -> datetime | None:
    """解析相对时间："5分钟后", "半小时后", "两小时后" """
    m = _HALF_HOUR_PATTERN.search(text)
    if m:
        return now + timedelta(minutes=30)

    m = _REL_PATTERN.search(text)
    if not m:
        return None
    num = _cn_to_int(m.group(1))
    if num is None:
        return None
    unit = m.group(2)
    if "分" in unit:
        return now + timedelta(minutes=num)
    if "小时" in unit or "时" in unit:
        return now + timedelta(hours=num)
    if "天" in unit:
        return now + timedelta(days=num)
    return None


# ──── 绝对时间 ────

_DAY_WORD: dict[str, int] = {"今天": 0, "明天": 1, "后天": 2, "大后天": 3}

_ABS_PATTERN = re.compile(
    r"(?P<day>今天|明天|后天|大后天)?"
    r"\s*"
    r"(?P<period>凌晨|早上|上午|中午|下午|傍晚|晚上|晚)?"
    r"\s*"
    r"(?P<hour>\d{1,2}|[一二两三四五六七八九十]+[一二三四五六七八九十]*)"
    r"\s*(?:点|时|:|：)"
    r"\s*(?:(?P<minute>\d{1,2}|半|[一二三四五六七八九十]+)\s*(?:分)?)?"
)


def _parse_absolute(text: str, now: datetime) -> datetime | None:
    """解析绝对时间："明天下午3点", "今天晚上8点半" """
    m = _ABS_PATTERN.search(text)
    if not m:
        return None

    day_str = m.group("day")
    period = m.group("period")
    hour_str = m.group("hour")
    minute_str = m.group("minute")

    hour = _cn_to_int(hour_str)
    if hour is None:
        return None
    hour = _apply_period(hour, period)
    if hour > 23:
        return None

    minute = 0
    if minute_str:
        if minute_str == "半":
            minute = 30
        else:
            minute = _cn_to_int(minute_str) or 0

    day_offset = _DAY_WORD.get(day_str, -1) if day_str else -1
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    if day_offset >= 0:
        # 明确指定了天
        base = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target = base + timedelta(days=day_offset)
        target = target.replace(hour=hour, minute=minute)
    else:
        # 未指定天：如果时间已过，自动推到明天
        if target <= now:
            target += timedelta(days=1)

    return target


# ──── 公共接口 ────

def parse_time_expression(text: str, now: datetime | None = None) -> datetime | None:
    """解析中文时间表达式，返回 datetime (CST) 或 None。

    优先匹配相对时间，再匹配绝对时间。
    """
    if now is None:
        now = datetime.now(CST)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=CST)

    result = _parse_relative(text, now)
    if result:
        return result.replace(tzinfo=CST) if result.tzinfo is None else result

    result = _parse_absolute(text, now)
    if result:
        return result.replace(tzinfo=CST) if result.tzinfo is None else result

    return None


def to_iso8601(dt: datetime) -> str:
    """datetime → ISO 8601 字符串（含时区偏移）"""
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")
