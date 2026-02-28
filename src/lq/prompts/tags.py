"""XML tag constants, wrap_tag helper, time format, truncation, and silence marker."""

from __future__ import annotations


# =====================================================================
# XML Tag Names
# =====================================================================

TAG_SOUL = "soul"
TAG_MEMORY = "memory"
TAG_CHAT_MEMORY = "chat_memory"
TAG_DAILY_LOG = "daily_log"
TAG_SELF_AWARENESS = "self_awareness"
TAG_CONSTRAINTS = "constraints"
TAG_MEMORY_GUIDANCE = "memory_guidance"
TAG_MSG = "msg"
TAG_CONTEXT_SUMMARY = "context_summary"
TAG_TOOL_CALL = "tool_call"
TAG_TOOL_RESULT = "tool_result"
TAG_GROUP_CONTEXT = "group_context"


# =====================================================================
# Tag Helpers
# =====================================================================

def wrap_tag(tag: str, content: str, **attrs: str) -> str:
    """Wrap *content* in an XML-style tag with optional attributes.

    >>> wrap_tag("memory", "hello")
    '<memory>\\nhello\\n</memory>'
    >>> wrap_tag("daily_log", "text", date="2026-01-01")
    '<daily_log date="2026-01-01">\\ntext\\n</daily_log>'
    """
    if attrs:
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        return f"<{tag} {attr_str}>\n{content}\n</{tag}>"
    return f"<{tag}>\n{content}\n</{tag}>"


# =====================================================================
# Time Format
# =====================================================================

# {formatted_time} -> e.g. "2026-02-14 10:30:00"
TIME_DISPLAY = "当前时间：{formatted_time} (CST, UTC+8)"


# =====================================================================
# Truncation Indicators
# =====================================================================

TRUNCATION_BUDGET_EXCEEDED = "...(部分内容因上下文空间限制被省略)"
TRUNCATION_SHORT = "...(已截断)"


# =====================================================================
# Silence Marker
# =====================================================================

# 当模型决定不回复时，应输出此标记而非伪噪音（如"（沉默）"、"（安静等待）"等）。
# _send_reply 检测到此标记后会跳过发送，实现真正的静默。
SILENCE_MARKER = "[SILENCE]"
