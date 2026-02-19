"""Vision MCP 集成：通过智谱 Studio API 进行图像理解与分析"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class VisionMCPMixin:
    """Vision MCP 图像分析能力（智谱 Studio API）。"""

    async def _tool_vision_analyze(self, image_source: str, prompt: str) -> dict:
        """通过智谱 GLM-4V 模型分析图片。

        Args:
            image_source: 图片 URL 或 base64 编码
            prompt: 分析提示词

        Returns:
            分析结果字典
        """
        api_key = getattr(self.executor, "mcp_key", "") or os.environ.get("Z_AI_API_KEY", "")
        if not api_key:
            return {"success": False, "error": "未配置 Z_AI_API_KEY"}

        # 判断是 URL 还是 base64
        if image_source.startswith("http://") or image_source.startswith("https://"):
            image_url = image_source
        else:
            # 当作 base64，包装成 data URL
            image_url = f"data:image/jpeg;base64,{image_source}"

        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(
                    "https://open.bigmodel.cn/api/paas/v4/chat/completions",
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "glm-4v-flash",
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "image_url",
                                        "image_url": {"url": image_url},
                                    },
                                    {
                                        "type": "text",
                                        "text": prompt,
                                    },
                                ],
                            }
                        ],
                        "max_tokens": 1024,
                    },
                )

                if resp.status_code != 200:
                    return {
                        "success": False,
                        "error": f"API 请求失败: {resp.status_code} - {resp.text}",
                    }

                data = resp.json()
                choices = data.get("choices", [])
                if not choices:
                    return {"success": False, "error": "API 未返回结果"}

                analysis = choices[0].get("message", {}).get("content", "")

                return {
                    "success": True,
                    "image_source": image_source,
                    "analysis": analysis or "（未返回分析结果）",
                    "engine": "zhipu_glm4v",
                }

        except Exception as e:
            logger.exception("Vision 分析失败: %s", image_source)
            return {"success": False, "error": f"图片分析失败: {e}"}
