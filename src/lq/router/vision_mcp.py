"""Vision MCP 集成：通过 zai-mcp-server (stdio) 进行图像理解与分析"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


class VisionMCPMixin:
    """Vision MCP 图像分析能力（zai-mcp-server，stdio 传输）。"""

    # 类级别缓存：子进程 + 初始化标志
    _vision_proc: asyncio.subprocess.Process | None = None
    _vision_proc_initialized: bool = False
    _vision_req_id: int = 0

    async def _ensure_vision_proc(self) -> asyncio.subprocess.Process:
        """确保 zai-mcp-server 子进程已启动并完成 MCP 初始化。"""
        # 检查已有进程是否仍然存活
        if self._vision_proc is not None and self._vision_proc.returncode is None:
            if self._vision_proc_initialized:
                return self._vision_proc
        else:
            # 进程已退出或不存在，重置状态
            VisionMCPMixin._vision_proc = None
            VisionMCPMixin._vision_proc_initialized = False

        api_key = getattr(self.executor, "mcp_key", "") or os.environ.get("Z_AI_API_KEY", "")
        if not api_key:
            raise ValueError("未配置 Z_AI_API_KEY")

        env = {**os.environ, "Z_AI_API_KEY": api_key, "Z_AI_MODE": "ZHIPU"**os.environ, "Z_AI_API_KEY": api_key}

        proc = await asyncio.create_subprocess_exec(
            "npx", "-y", "@z_ai/mcp-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        VisionMCPMixin._vision_proc = proc

        # MCP 初始化握手
        await self._vision_mcp_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lingque", "version": "1.0.0"},
        })
        await self._vision_mcp_request(
            "notifications/initialized", is_notification=True,
        )
        VisionMCPMixin._vision_proc_initialized = True
        logger.info("zai-mcp-server 子进程已启动并初始化 (pid=%s)", proc.pid)
        return proc

    async def _vision_mcp_request(
        self,
        method: str,
        params: dict | None = None,
        *,
        is_notification: bool = False,
    ) -> dict | None:
        """通过 stdio 向 zai-mcp-server 发送 JSON-RPC 请求并读取响应。"""
        proc = VisionMCPMixin._vision_proc
        if proc is None or proc.stdin is None or proc.stdout is None:
            raise RuntimeError("zai-mcp-server 子进程未启动")

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            VisionMCPMixin._vision_req_id += 1
            payload["id"] = VisionMCPMixin._vision_req_id
        if params:
            payload["params"] = params

        line = json.dumps(payload) + "\n"
        proc.stdin.write(line.encode())
        await proc.stdin.drain()

        if is_notification:
            return None

        # 读取响应行（跳过空行和非 JSON 行）
        while True:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=120.0)
            if not raw:
                raise RuntimeError("zai-mcp-server 子进程意外关闭")
            raw_str = raw.decode().strip()
            if not raw_str:
                continue
            try:
                return json.loads(raw_str)
            except json.JSONDecodeError:
                # 可能是 stderr 混入或调试输出，跳过
                logger.debug("跳过非 JSON 输出: %s", raw_str[:200])
                continue

    async def _kill_vision_proc(self) -> None:
        """终止子进程并重置状态。"""
        proc = VisionMCPMixin._vision_proc
        if proc and proc.returncode is None:
            proc.kill()
            await proc.wait()
        VisionMCPMixin._vision_proc = None
        VisionMCPMixin._vision_proc_initialized = False

    async def _tool_vision_analyze(self, image_source: str, prompt: str) -> dict:
        """通过 zai-mcp-server 的 image_analysis 工具分析图片。"""
        try:
            await self._ensure_vision_proc()

            resp = await self._vision_mcp_request("tools/call", {
                "name": "image_analysis",
                "arguments": {
                    "image_source": image_source,
                    "prompt": prompt,
                },
            })

            # 错误响应时重启子进程重试一次
            if not resp or "result" not in resp:
                if resp and resp.get("error"):
                    logger.warning("Vision MCP 返回错误，重启子进程重试: %s", resp["error"])
                    await self._kill_vision_proc()
                    await self._ensure_vision_proc()
                    resp = await self._vision_mcp_request("tools/call", {
                        "name": "image_analysis",
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
            await self._kill_vision_proc()
            return {"success": False, "error": f"图片分析失败: {e}"}
