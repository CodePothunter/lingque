"""Vision MCP 集成：通过 zai-mcp-server 进行图像理解与分析"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class VisionMCPMixin:
    """Vision MCP 图像分析能力（zai-mcp-server）。"""

    _vision_mcp_session_id: str | None = None

    async def _vision_mcp_request(
        self,
        method: str,
        params: dict | None = None,
        *,
        is_notification: bool = False,
    ) -> dict | None:
        """向 zai-mcp-server 发送 JSON-RPC 请求。

        传输方式与 web_search MCP 相同：Streamable HTTP，支持 JSON 和 SSE 响应。
        """
        import httpx

        mcp_url = "http://localhost:3100/mcp"

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._vision_mcp_session_id:
            headers["Mcp-Session-Id"] = self._vision_mcp_session_id

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            payload["id"] = hash((method, time.time())) & 0x7FFFFFFF
        if params:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(mcp_url, json=payload, headers=headers)
            resp.raise_for_status()

            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._vision_mcp_session_id = sid

            if is_notification:
                return None

            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                last_data: dict | None = None
                for line in resp.text.splitlines():
                    if line.startswith("data:"):
                        raw = line[5:].lstrip()
                        if not raw:
                            continue
                        try:
                            last_data = json.loads(raw)
                        except json.JSONDecodeError:
                            continue
                return last_data or {}
            return resp.json()

    async def _ensure_vision_mcp_session(self) -> None:
        """确保 Vision MCP 会话已初始化。"""
        if self._vision_mcp_session_id:
            return
        await self._vision_mcp_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lingque", "version": "1.0.0"},
        })
        await self._vision_mcp_request("notifications/initialized", is_notification=True)

    async def _check_vision_mcp_available(self) -> bool:
        """快速检测 Vision MCP 服务是否可达（2 秒超时）。"""
        import httpx

        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.post(
                    "http://localhost:3100/mcp",
                    json={"jsonrpc": "2.0", "method": "ping", "id": 0},
                    headers={"Content-Type": "application/json"},
                )
                return resp.status_code < 500
        except Exception:
            return False

    async def _tool_vision_analyze(self, image_source: str, prompt: str) -> dict:
        """通过 zai-mcp-server 的 analyze_image 工具分析图片"""
        try:
            if not await self._check_vision_mcp_available():
                return {
                    "success": False,
                    "error": "Vision MCP 服务未启动，请先启动 zai-mcp-server",
                }

            try:
                await self._ensure_vision_mcp_session()
            except Exception:
                self._vision_mcp_session_id = None
                await self._ensure_vision_mcp_session()

            resp = await self._vision_mcp_request("tools/call", {
                "name": "analyze_image",
                "arguments": {
                    "image_source": image_source,
                    "prompt": prompt,
                },
            })

            if not resp or "result" not in resp:
                if resp and resp.get("error"):
                    logger.warning("Vision MCP 返回错误，重置会话重试: %s", resp["error"])
                    self._vision_mcp_session_id = None
                    await self._ensure_vision_mcp_session()
                    resp = await self._vision_mcp_request("tools/call", {
                        "name": "analyze_image",
                        "arguments": {
                            "image_source": image_source,
                            "prompt": prompt,
                        },
                    })

            if not resp or "result" not in resp:
                error_msg = (resp or {}).get("error", {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", "未知错误")
                return {"success": False, "error": f"Vision 分析失败: {error_msg}"}

            content_blocks = resp["result"].get("content", [])
            analysis_text = "\n".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )

            return {
                "success": True,
                "image_source": image_source,
                "analysis": analysis_text or "（未返回分析结果）",
                "engine": "zai_vision_mcp",
            }

        except Exception as e:
            logger.exception("Vision MCP 分析失败: %s", image_source)
            self._vision_mcp_session_id = None
            return {"success": False, "error": f"图片分析失败: {e}"}
