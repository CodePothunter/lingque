"""SubAgent — 用 LLM 提取结构化参数（max_tokens=512，低成本回退）"""

from __future__ import annotations

import json
import logging
from typing import Any

from lq.executor.api import DirectAPIExecutor

logger = logging.getLogger(__name__)

# 每种意图的参数提取 prompt 模板
_EXTRACTION_PROMPTS: dict[str, str] = {
    "memory_write": (
        "从用户消息中提取要记住的内容。\n"
        "输出 JSON：{\"section\": \"分区名\", \"content\": \"要记住的内容\"}\n"
        "分区名可选：重要信息、用户偏好、备忘、待办事项。根据内容选择合适分区。\n"
        "只输出 JSON，不要其他文字。"
    ),
    "schedule_reminder": (
        "从用户消息中提取定时提醒的参数。\n"
        "输出 JSON：{\"text\": \"提醒内容\", \"time_expr\": \"原始时间表达式\"}\n"
        "time_expr 保留用户的原始表达（如 '5分钟后'、'明天下午3点'）。\n"
        "text 是要提醒的内容（去掉时间部分）。\n"
        "只输出 JSON，不要其他文字。"
    ),
    "calendar_create": (
        "从用户消息中提取日历事件参数。\n"
        "输出 JSON：{\"summary\": \"事件标题\", \"time_expr\": \"原始时间表达式\", "
        "\"duration_minutes\": 60}\n"
        "summary 是事件的简短标题。\n"
        "time_expr 保留用户的原始时间表达。\n"
        "duration_minutes 是持续时间（分钟），默认60。\n"
        "只输出 JSON，不要其他文字。"
    ),
}

_SYSTEM_PROMPT = (
    "你是一个参数提取器。根据用户消息和指令，提取结构化参数并以 JSON 格式输出。"
    "严格只输出 JSON 对象，不要包含任何其他文字、解释或 markdown 格式。"
)


class SubAgent:
    """轻量 SubAgent：用最小 LLM 调用提取工具参数"""

    def __init__(self, executor: DirectAPIExecutor) -> None:
        self.executor = executor

    async def extract_params(
        self,
        intent_type: str,
        user_message: str,
        llm_response: str,
        chat_id: str,
    ) -> dict | None:
        """提取指定意图类型的参数。

        Returns:
            提取到的参数 dict，或 None（解析失败时优雅降级）。
        """
        prompt_template = _EXTRACTION_PROMPTS.get(intent_type)
        if not prompt_template:
            logger.warning("SubAgent: 未知意图类型 %s", intent_type)
            return None

        messages = [
            {
                "role": "user",
                "content": (
                    f"{prompt_template}\n\n"
                    f"用户消息：{user_message}\n"
                    f"助手回复：{llm_response}"
                ),
            },
        ]

        try:
            raw = await self.executor.reply_with_history(
                system=_SYSTEM_PROMPT,
                messages=messages,
                max_tokens=512,
            )
            # 清理可能的 markdown 包裹
            raw = raw.strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            result = json.loads(raw)
            if not isinstance(result, dict):
                logger.warning("SubAgent: 返回非 dict 类型: %s", type(result))
                return None

            logger.info("SubAgent 提取参数: %s → %s", intent_type, result)
            return result

        except json.JSONDecodeError:
            logger.warning("SubAgent: JSON 解析失败: %s", raw[:200] if 'raw' in dir() else '(no response)')
            return None
        except Exception:
            logger.exception("SubAgent: 参数提取异常")
            return None
