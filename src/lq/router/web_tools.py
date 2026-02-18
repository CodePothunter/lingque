"""联网工具实现：HTTP 客户端、MCP 搜索、网页抓取、代码执行、文件操作、统计"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from lq.prompts import (
    ERR_FILE_NOT_FOUND, ERR_FILE_READ_FAILED, ERR_FILE_WRITE_FAILED,
    RESULT_FILE_WRITTEN,
)

logger = logging.getLogger(__name__)


class WebToolsMixin:
    """HTTP 客户端构建、MCP 联网搜索、网页抓取、Python 执行、文件操作与统计。"""

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

    async def _tool_run_python(self, code: str, timeout: int = 30) -> dict:
        """在子进程中执行 Python 代码"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.memory.workspace),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            # 截断过长输出
            if len(output) > 10000:
                output = output[:10000] + f"\n... (输出已截断，共 {len(stdout)} 字节)"
            if len(error) > 5000:
                error = error[:5000] + f"\n... (错误输出已截断)"

            exit_code = proc.returncode or 0
            return {
                "success": exit_code == 0,
                "output": output,
                "error": error,
                "exit_code": exit_code,
            }
        except asyncio.TimeoutError:
            logger.error("run_python 超时 (%ds)", timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "", "error": f"执行超时 ({timeout}s)", "exit_code": -1}
        except Exception as e:
            logger.exception("run_python 失败")
            return {"success": False, "output": "", "error": str(e), "exit_code": -1}

    # ── 统计与文件操作 ──

    def _tool_get_my_stats(self, category: str = "today") -> dict:
        """返回自身运行统计信息"""
        result: dict[str, Any] = {"success": True}
        if category == "today" and self.stats:
            daily = self.stats.get_daily_summary()
            result["today"] = daily
            result["uptime"] = self._format_uptime()
            result["model"] = getattr(self.executor, "model", "unknown")
        elif category == "month" and self.stats:
            monthly = self.stats.get_monthly_summary()
            result["month"] = monthly
        elif category == "capability":
            result["tool_stats"] = {
                name: {
                    "total": s["success"] + s["fail"],
                    "success_rate": round(s["success"] / max(s["success"] + s["fail"], 1) * 100),
                    "last_error": s.get("last_error", ""),
                }
                for name, s in self._tool_stats.items()
                if s["success"] + s["fail"] > 0
            }
        else:
            result["message"] = "统计模块未加载或类别无效"
        return result

    def _format_uptime(self) -> str:
        """格式化运行时间"""
        elapsed = int(time.time()) - self._startup_ts // 1000
        if elapsed < 60:
            return f"{elapsed}秒"
        if elapsed < 3600:
            return f"{elapsed // 60}分钟"
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        if hours < 24:
            return f"{hours}小时{minutes}分钟"
        days = hours // 24
        hours = hours % 24
        return f"{days}天{hours}小时"

    def _tool_read_file(self, path: str, max_lines: int = 500) -> dict:
        """读取文件系统中的文件"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        if not file_path.exists():
            return {"success": False, "error": ERR_FILE_NOT_FOUND.format(path=str(file_path))}

        if not file_path.is_file():
            return {"success": False, "error": f"路径不是文件: {file_path}"}

        try:
            # 检查文件大小，避免读取超大文件
            size = file_path.stat().st_size
            if size > 5_000_000:  # 5MB
                return {
                    "success": False,
                    "error": f"文件过大 ({size} 字节，上限 5MB)，请使用 run_bash 的 head/tail 命令读取部分内容",
                }

            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total_lines = len(lines)

            if total_lines > max_lines:
                text = "\n".join(lines[:max_lines])
                text += f"\n... (已显示前 {max_lines} 行，共 {total_lines} 行)"

            return {
                "success": True,
                "path": str(file_path),
                "content": text,
                "lines": min(total_lines, max_lines),
                "total_lines": total_lines,
                "size": size,
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_READ_FAILED.format(error=str(e))}

    def _tool_write_file(self, path: str, content: str) -> dict:
        """写入文件到文件系统"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        try:
            # 自动创建父目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            size = file_path.stat().st_size
            logger.info("write_file: %s (%d 字节)", file_path, size)
            return {
                "success": True,
                "message": RESULT_FILE_WRITTEN.format(path=str(file_path), size=size),
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_WRITE_FAILED.format(error=str(e))}
