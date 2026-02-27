"""联网工具实现：HTTP 客户端、MCP 搜索、网页抓取"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class WebToolsMixin:
    """HTTP 客户端构建、MCP 联网搜索、网页抓取。"""

    @staticmethod
    def _build_http_client(**kwargs: Any) -> Any:
        """构建代理感知的 httpx.AsyncClient。

        自动从环境变量读取代理配置（HTTPS_PROXY / HTTP_PROXY / ALL_PROXY），
        使用通用 User-Agent 避免被目标站点拦截。
        """
        import os
        import httpx

        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or os.environ.get("all_proxy")
        )

        defaults: dict[str, Any] = {
            "follow_redirects": True,
            "timeout": 20.0,
            "headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
            },
        }
        if proxy:
            defaults["proxy"] = proxy
        defaults.update(kwargs)
        return httpx.AsyncClient(**defaults)

    # ── MCP 联网搜索（智谱 web-search-prime）──

    _mcp_session_id: str | None = None

    async def _mcp_request(
        self,
        method: str,
        params: dict | None = None,
        *,
        is_notification: bool = False,
    ) -> dict | None:
        """向智谱 MCP 服务器发送 JSON-RPC 请求。

        支持 Streamable HTTP 传输：自动处理 application/json 和 text/event-stream 两种响应。
        """
        import httpx

        mcp_url = "https://open.bigmodel.cn/api/mcp/web_search_prime/mcp"
        mcp_key = getattr(self.executor, "mcp_key", "")
        if not mcp_key:
            raise ValueError("未配置 MCP API Key（ZHIPU_API_KEY）")

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {mcp_key}",
        }
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id

        payload: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if not is_notification:
            payload["id"] = hash((method, time.time())) & 0x7FFFFFFF
        if params:
            payload["params"] = params

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(mcp_url, json=payload, headers=headers)
            resp.raise_for_status()

            # 缓存 session ID
            sid = resp.headers.get("mcp-session-id")
            if sid:
                self._mcp_session_id = sid

            if is_notification:
                return None

            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" in content_type:
                # 从 SSE 流中提取最后一个 JSON-RPC 响应
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

    async def _ensure_mcp_session(self) -> None:
        """确保 MCP 会话已初始化（带缓存，避免每次搜索都握手）。"""
        if self._mcp_session_id:
            return
        await self._mcp_request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "lingque", "version": "1.0.0"},
        })
        await self._mcp_request("notifications/initialized", is_notification=True)

    async def _tool_web_search(self, query: str, max_results: int = 5) -> dict:
        """搜索互联网：优先使用智谱 MCP，失败时自动降级到 DuckDuckGo"""
        mcp_result = await self._tool_web_search_mcp(query, max_results)
        if mcp_result.get("success"):
            return mcp_result

        # MCP 失败，尝试 DuckDuckGo 备用搜索
        logger.info("MCP 搜索不可用（%s），尝试 DuckDuckGo 备用搜索", mcp_result.get("error", ""))
        fallback_result = await self._tool_web_search_fallback(query, max_results)
        if fallback_result.get("success"):
            return fallback_result

        # 两者均失败，合并错误信息
        return {
            "success": False,
            "error": (
                f"主搜索(MCP)失败: {mcp_result.get('error')}；"
                f"备用搜索(DuckDuckGo)失败: {fallback_result.get('error')}"
            ),
        }

    async def _tool_web_search_mcp(self, query: str, max_results: int = 5) -> dict:
        """通过智谱 MCP web-search-prime 搜索互联网"""
        try:
            # 首次调用需初始化 MCP 会话
            try:
                await self._ensure_mcp_session()
            except Exception:
                # 会话可能已过期，重置后重试
                self._mcp_session_id = None
                await self._ensure_mcp_session()

            resp = await self._mcp_request("tools/call", {
                "name": "webSearchPrime",
                "arguments": {"search_query": query},
            })

            if not resp or "result" not in resp:
                # 会话过期时服务器可能返回错误，重置重试一次
                if resp and resp.get("error"):
                    logger.warning("MCP 搜索返回错误，重置会话重试: %s", resp["error"])
                    self._mcp_session_id = None
                    await self._ensure_mcp_session()
                    resp = await self._mcp_request("tools/call", {
                        "name": "webSearchPrime",
                        "arguments": {"search_query": query},
                    })

            if not resp or "result" not in resp:
                error_msg = (resp or {}).get("error", {})
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", "未知错误")
                return {"success": False, "error": f"MCP 搜索失败: {error_msg}"}

            # 解析 MCP 工具返回的 content 列表
            content_blocks = resp["result"].get("content", [])
            raw_text = "\n".join(
                block.get("text", "") for block in content_blocks if block.get("type") == "text"
            )

            if not raw_text.strip():
                return {
                    "success": True,
                    "query": query,
                    "results": [],
                    "count": 0,
                    "engine": "zhipu_mcp",
                }

            # 尝试从文本中解析结构化搜索结果
            results = self._parse_mcp_search_results(raw_text, max_results)
            return {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
                "engine": "zhipu_mcp",
            }

        except Exception as e:
            logger.exception("MCP 联网搜索失败: %s", query)
            self._mcp_session_id = None  # 重置会话以便下次重新初始化
            return {"success": False, "error": f"搜索失败: {e}"}

    async def _tool_web_search_fallback(self, query: str, max_results: int = 5) -> dict:
        """使用 DuckDuckGo 作为备用搜索引擎（无需 API Key，自动继承代理配置）

        duckduckgo-search >= 8.x 只提供同步 DDGS，通过 asyncio.to_thread 在线程池执行。
        """
        import asyncio
        import os

        proxy = (
            os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
            or os.environ.get("https_proxy")
            or os.environ.get("http_proxy")
            or os.environ.get("all_proxy")
        )

        def _run_sync() -> list[dict]:
            from duckduckgo_search import DDGS  # type: ignore[import]
            kwargs: dict = {}
            if proxy:
                kwargs["proxy"] = proxy
            return DDGS(**kwargs).text(query, max_results=max_results)

        try:
            raw = await asyncio.wait_for(
                asyncio.to_thread(_run_sync),
                timeout=15.0,
            )
            results = [
                {
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                }
                for r in (raw or [])
            ]
            return {
                "success": True,
                "query": query,
                "results": results,
                "count": len(results),
                "engine": "duckduckgo",
            }

        except ImportError:
            return {
                "success": False,
                "error": "DuckDuckGo 搜索库未安装，请运行: uv add duckduckgo-search",
            }
        except asyncio.TimeoutError:
            logger.warning("DuckDuckGo 备用搜索超时: %s", query)
            return {"success": False, "error": "DuckDuckGo 搜索超时（15s）"}
        except Exception as e:
            logger.warning("DuckDuckGo 备用搜索失败: %s — %s", query, e)
            return {"success": False, "error": f"备用搜索失败: {e}"}

    @staticmethod
    def _parse_mcp_search_results(raw_text: str, max_results: int) -> list[dict]:
        """解析 MCP webSearchPrime 返回的搜索结果。

        兼容多种格式：JSON 数组、JSON 对象（含 results 字段）、纯文本。
        """
        # 1) 尝试整体解析为 JSON
        try:
            data = json.loads(raw_text)
            if isinstance(data, list):
                return [
                    {
                        "title": item.get("title", ""),
                        "url": item.get("url") or item.get("link", ""),
                        "snippet": item.get("snippet") or item.get("content") or item.get("description", ""),
                    }
                    for item in data[:max_results]
                    if isinstance(item, dict)
                ]
            if isinstance(data, dict):
                items = data.get("results") or data.get("items") or data.get("data", [])
                if isinstance(items, list):
                    return [
                        {
                            "title": item.get("title", ""),
                            "url": item.get("url") or item.get("link", ""),
                            "snippet": item.get("snippet") or item.get("content") or item.get("description", ""),
                        }
                        for item in items[:max_results]
                        if isinstance(item, dict)
                    ]
        except (json.JSONDecodeError, TypeError):
            pass

        # 2) 纯文本：将原始内容作为单条结果返回，由 LLM 自行理解
        return [{"title": "搜索结果", "url": "", "snippet": raw_text[:3000]}]

    async def _tool_web_fetch(self, url: str, max_length: int = 8000) -> dict:
        """抓取网页并提取纯文本内容"""
        import re as _re

        if not url.startswith(("http://", "https://")):
            return {"success": False, "error": "URL 必须以 http:// 或 https:// 开头"}

        try:
            async with self._build_http_client(timeout=20.0) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                raw = resp.text

            # 非 HTML 内容直接返回
            if "html" not in content_type.lower() and "text" not in content_type.lower():
                text = raw[:max_length]
                if len(raw) > max_length:
                    text += f"\n... (内容已截断，原始长度 {len(raw)} 字符)"
                return {"success": True, "url": url, "content": text, "type": content_type}

            # HTML → 纯文本
            # 移除 script/style 标签及其内容
            text = _re.sub(r"<(script|style)[^>]*>.*?</\1>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
            # 将 <br>, <p>, <div>, <li>, <tr> 等块级标签转为换行
            text = _re.sub(r"<(?:br|p|div|li|tr|h[1-6])[^>]*>", "\n", text, flags=_re.IGNORECASE)
            # 移除所有 HTML 标签
            text = _re.sub(r"<[^>]+>", "", text)
            # 解码常见 HTML 实体
            text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
            text = text.replace("&quot;", '"').replace("&apos;", "'").replace("&nbsp;", " ")
            # 合并多个空行
            text = _re.sub(r"\n{3,}", "\n\n", text)
            # 移除行首尾空白
            text = "\n".join(line.strip() for line in text.splitlines())
            text = text.strip()

            # 截断
            if len(text) > max_length:
                text = text[:max_length] + f"\n... (内容已截断，原始长度 {len(text)} 字符)"

            return {"success": True, "url": url, "content": text, "length": len(text)}
        except Exception as e:
            logger.exception("web_fetch 失败: %s", url)
            return {"success": False, "error": f"网页抓取失败: {e}"}
