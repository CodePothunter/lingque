"""浏览器操控工具实现：通过 CDP 连接 Chromium，提供原子化浏览器操作"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# get_content 返回的最大字符数，避免 token 爆炸
_MAX_CONTENT_LENGTH = 8000
# evaluate 结果序列化后的最大字符数
_MAX_EVAL_RESULT_LENGTH = 8000
# get_elements 默认最大返回数
_DEFAULT_MAX_ELEMENTS = 20
# 默认 CDP 端口
_DEFAULT_CDP_PORT = 9222


class BrowserToolsMixin:
    """通过 Playwright CDP 连接已运行的 Chromium，提供原子化浏览器操作。"""

    @property
    def _cdp_url(self) -> str:
        """从实例 config 读取 browser_port，构造 CDP 连接地址。"""
        port = getattr(getattr(self, "config", None), "browser_port", _DEFAULT_CDP_PORT)
        return f"http://localhost:{port}"

    async def _tool_browser_action(self, input_data: dict) -> dict:
        action = input_data.get("action", "")
        if not action:
            return {"success": False, "error": "缺少 action 参数"}

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return {
                "success": False,
                "error": "playwright 未安装。请用 pip install playwright && playwright install chromium 安装。",
            }

        if action == "status":
            return await self._browser_status()

        cdp_url = self._cdp_url
        logger.info("browser_action: %s (cdp=%s)", action, cdp_url)
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                try:
                    context = browser.contexts[0] if browser.contexts else await browser.new_context()
                    page = context.pages[0] if context.pages else await context.new_page()
                    result = await self._dispatch_browser_action(page, context, action, input_data)
                    logger.info("browser_action %s 完成: success=%s", action, result.get("success"))
                    return result
                finally:
                    browser.close()
        except Exception as e:
            error_msg = str(e)
            port = cdp_url.rsplit(":", 1)[-1]
            if "connect" in error_msg.lower() or "ECONNREFUSED" in error_msg:
                return {
                    "success": False,
                    "error": (
                        f"无法连接浏览器（{cdp_url}）：{error_msg}\n"
                        "请先用 run_bash 启动 Chromium：\n"
                        f"chromium-browser --headless --no-sandbox --remote-debugging-port={port} &"
                    ),
                }
            return {"success": False, "error": f"浏览器操作失败: {error_msg}"}

    async def _dispatch_browser_action(
        self, page: Any, context: Any, action: str, input_data: dict,
    ) -> dict:
        """根据 action 分发到具体操作"""

        if action == "navigate":
            url = input_data.get("url", "")
            if not url:
                return {"success": False, "error": "navigate 需要 url 参数"}
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            return {
                "success": True,
                "url": page.url,
                "title": await page.title(),
                "status": resp.status if resp else None,
            }

        elif action == "get_content":
            selector = input_data.get("selector", "body")
            try:
                text = await page.inner_text(selector, timeout=5000)
            except Exception:
                text = await page.inner_text("body", timeout=5000)
            original_length = len(text)
            if original_length > _MAX_CONTENT_LENGTH:
                text = text[:_MAX_CONTENT_LENGTH] + f"\n...(已截断，原始长度 {original_length} 字符)"
            return {
                "success": True,
                "url": page.url,
                "title": await page.title(),
                "content": text,
                "length": original_length,
            }

        elif action == "screenshot":
            path = input_data.get("path", "")
            if not path:
                import time as _time
                ws = getattr(getattr(self, "memory", None), "workspace", None)
                if ws:
                    path = str(ws / f"browser_screenshot_{int(_time.time())}.png")
                else:
                    path = f"/tmp/browser_screenshot_{int(_time.time())}.png"
            await page.screenshot(path=path, full_page=False)
            return {
                "success": True,
                "url": page.url,
                "path": path,
                "message": f"截图已保存到 {path}，可用 vision_analyze 工具查看",
            }

        elif action == "click":
            selector = input_data.get("selector", "")
            if not selector:
                return {"success": False, "error": "click 需要 selector 参数"}
            await page.click(selector, timeout=5000)
            # 点击后等待页面稳定；SPA 中可能不触发导航，所以 best-effort
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=3000)
            except Exception:
                pass
            return {"success": True, "url": page.url, "title": await page.title()}

        elif action == "type":
            selector = input_data.get("selector", "")
            text = input_data.get("text", "")
            if not selector or not text:
                return {"success": False, "error": "type 需要 selector 和 text 参数"}
            await page.fill(selector, text, timeout=5000)
            return {"success": True}

        elif action == "evaluate":
            script = input_data.get("script", "")
            if not script:
                return {"success": False, "error": "evaluate 需要 script 参数"}
            result = await page.evaluate(script)
            # 截断过大的返回值，避免 token 爆炸
            serialized = json.dumps(result, ensure_ascii=False, default=str)
            if len(serialized) > _MAX_EVAL_RESULT_LENGTH:
                serialized = serialized[:_MAX_EVAL_RESULT_LENGTH] + f"...(已截断，原始长度 {len(serialized)})"
                return {"success": True, "result": serialized, "truncated": True}
            return {"success": True, "result": result}

        elif action == "get_elements":
            selector = input_data.get("selector", "")
            if not selector:
                return {"success": False, "error": "get_elements 需要 selector 参数"}
            max_count = input_data.get("max_count", _DEFAULT_MAX_ELEMENTS)
            elements = await page.query_selector_all(selector)
            items: list[dict] = []
            for el in elements[:max_count]:
                text = (await el.inner_text()).strip()
                href = await el.get_attribute("href")
                src = await el.get_attribute("src")
                item: dict[str, Any] = {"text": text[:200]}
                if href:
                    item["href"] = href
                if src:
                    item["src"] = src
                items.append(item)
            return {"success": True, "count": len(items), "elements": items}

        elif action == "scroll":
            direction = input_data.get("direction", "down")
            amount = input_data.get("amount", 500)
            delta = amount if direction == "down" else -amount
            await page.evaluate("([y]) => window.scrollBy(0, y)", [delta])
            return {"success": True, "direction": direction, "amount": amount}

        elif action == "wait":
            selector = input_data.get("selector", "")
            if not selector:
                return {"success": False, "error": "wait 需要 selector 参数"}
            timeout = input_data.get("timeout", 10) * 1000  # 秒→毫秒
            await page.wait_for_selector(selector, timeout=timeout)
            return {"success": True, "selector": selector}

        elif action == "save_cookies":
            path = input_data.get("cookie_path", "") or input_data.get("path", "")
            if not path:
                ws = getattr(getattr(self, "memory", None), "workspace", None)
                if ws:
                    path = str(ws / "browser_cookies.json")
                else:
                    path = "/tmp/browser_cookies.json"
            cookies = await context.cookies()
            from pathlib import Path as _Path
            _Path(path).write_text(json.dumps(cookies, ensure_ascii=False, indent=2))
            return {
                "success": True,
                "path": path,
                "count": len(cookies),
                "domains": list({c.get("domain", "") for c in cookies}),
            }

        elif action == "load_cookies":
            path = input_data.get("cookie_path", "") or input_data.get("path", "")
            if not path:
                ws = getattr(getattr(self, "memory", None), "workspace", None)
                if ws:
                    path = str(ws / "browser_cookies.json")
                else:
                    path = "/tmp/browser_cookies.json"
            from pathlib import Path as _Path
            cookie_file = _Path(path)
            if not cookie_file.exists():
                return {"success": False, "error": f"Cookie 文件不存在: {path}"}
            cookies = json.loads(cookie_file.read_text())
            await context.add_cookies(cookies)
            return {
                "success": True,
                "path": path,
                "count": len(cookies),
                "message": "Cookies 已加载到浏览器",
            }

        else:
            return {"success": False, "error": f"未知的 browser action: {action}"}

    async def _browser_status(self) -> dict:
        """检测浏览器连接状态"""
        from playwright.async_api import async_playwright

        cdp_url = self._cdp_url
        try:
            async with async_playwright() as p:
                browser = await p.chromium.connect_over_cdp(cdp_url)
                contexts = len(browser.contexts)
                pages = sum(len(ctx.pages) for ctx in browser.contexts)
                browser.close()
                return {
                    "success": True,
                    "connected": True,
                    "cdp_url": cdp_url,
                    "contexts": contexts,
                    "pages": pages,
                }
        except Exception as e:
            return {"success": True, "connected": False, "cdp_url": cdp_url, "error": str(e)}
