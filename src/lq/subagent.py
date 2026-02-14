"""SubAgent — 用 LLM 提取结构化参数（max_tokens=512，低成本回退）"""

from __future__ import annotations

import json
import logging
from typing import Any

from lq.executor.api import DirectAPIExecutor
from lq.prompts import EXTRACTION_PROMPTS, SUBAGENT_SYSTEM, SUBAGENT_CONTEXT_USER, SUBAGENT_CONTEXT_ASSISTANT

logger = logging.getLogger(__name__)


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
        prompt_template = EXTRACTION_PROMPTS.get(intent_type)
        if not prompt_template:
            logger.warning("SubAgent: 未知意图类型 %s", intent_type)
            return None

        messages = [
            {
                "role": "user",
                "content": (
                    f"{prompt_template}\n\n"
                    f"{SUBAGENT_CONTEXT_USER.format(message=user_message)}\n"
                    f"{SUBAGENT_CONTEXT_ASSISTANT.format(reply=llm_response)}"
                ),
            },
        ]

        try:
            raw = await self.executor.reply_with_history(
                system=SUBAGENT_SYSTEM,
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
