"""飞书卡片构建工具"""

from __future__ import annotations

from typing import Any


def build_info_card(
    title: str,
    content: str,
    fields: list[dict[str, str]] | None = None,
    color: str = "blue",
) -> dict:
    """通用信息卡片"""
    elements: list[dict] = [
        {
            "tag": "markdown",
            "content": content,
        }
    ]

    if fields:
        field_elements = []
        for f in fields:
            field_elements.append({
                "is_short": f.get("short", True),
                "text": {
                    "tag": "lark_md",
                    "content": f"**{f['key']}**\n{f['value']}",
                },
            })
        elements.append({"tag": "div", "fields": field_elements})

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": color,
        },
        "elements": elements,
    }


def build_schedule_card(events: list[dict]) -> dict:
    """日程卡片"""
    if not events:
        return build_info_card("📅 今日日程", "今天没有日程安排。", color="green")

    lines = []
    for e in events:
        start = e.get("start_time", "")
        end = e.get("end_time", "")
        summary = e.get("summary", "未命名事件")
        time_str = f"{start} - {end}" if start else "全天"
        lines.append(f"• **{time_str}**  {summary}")

    return build_info_card(
        "📅 今日日程",
        "\n".join(lines),
        color="blue",
    )


def build_task_card(tasks: list[dict]) -> dict:
    """任务列表卡片"""
    if not tasks:
        return build_info_card("📋 任务列表", "暂无任务。", color="green")

    lines = []
    for t in tasks:
        status = "✅" if t.get("done") else "⬜"
        lines.append(f"{status} {t.get('title', '未命名任务')}")

    return build_info_card("📋 任务列表", "\n".join(lines), color="purple")


def build_error_card(title: str, error_msg: str) -> dict:
    """错误提示卡片"""
    return build_info_card(
        f"⚠️ {title}",
        f"```\n{error_msg}\n```",
        color="red",
    )


def build_confirm_card(
    title: str,
    content: str,
    confirm_text: str = "确认",
    cancel_text: str = "取消",
    callback_data: dict | None = None,
) -> dict:
    """确认/取消按钮卡片"""
    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": title},
            "template": "orange",
        },
        "elements": [
            {"tag": "markdown", "content": content},
            {
                "tag": "action",
                "actions": [
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": confirm_text},
                        "type": "primary",
                        "value": {"action": "confirm", **(callback_data or {})},
                    },
                    {
                        "tag": "button",
                        "text": {"tag": "plain_text", "content": cancel_text},
                        "type": "default",
                        "value": {"action": "cancel", **(callback_data or {})},
                    },
                ],
            },
        ],
    }
