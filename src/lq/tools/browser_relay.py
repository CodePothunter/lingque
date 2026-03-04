"""
Browser Relay Tool - 灵雀自定义工具

通过 Browser Relay Server 控制真实浏览器，绕过 headless 检测。
需要先启动 relay_server.py 并安装 Chrome 扩展。
"""

import asyncio
import base64
from typing import Optional

TOOL_DEFINITION = {
    "name": "browser_relay",
    "description": "通过 Relay 控制真实浏览器。支持: navigate(打开URL), screenshot(截图), click(点击), type(输入), evaluate(执行JS), get_content(获取页面文本), status(检查连接状态)",
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["navigate", "screenshot", "click", "type", "evaluate", "get_content", "status"],
                "description": "操作类型"
            },
            "url": {"type": "string", "description": "要打开的URL (navigate用)"},
            "selector": {"type": "string", "description": "CSS选择器 (click/type用)"},
            "text": {"type": "string", "description": "要输入的文字 (type用)"},
            "script": {"type": "string", "description": "要执行的JS代码 (evaluate用)"},
        },
        "required": ["action"]
    }
}

RELAY_URL = "http://127.0.0.1:50518/cdp"


async def _execute_cdp(method: str, params: dict = None) -> dict:
    """发送 CDP 命令到 relay server"""
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(RELAY_URL, json={"method": method, "params": params or {}})
        return resp.json()


async def execute(input_data: dict, context: dict) -> dict:
    """执行浏览器操作"""
    action = input_data.get("action")

    if action == "status":
        try:
            result = await _execute_cdp("Browser.getVersion")
            return {"connected": True, "browser": result.get("result", {}).get("product", "unknown")}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    elif action == "navigate":
        url = input_data.get("url")
        if not url:
            return {"error": "缺少 url 参数"}
        await _execute_cdp("Page.navigate", {"url": url})
        await asyncio.sleep(1)  # 等待页面加载
        return {"success": True, "url": url}

    elif action == "screenshot":
        result = await _execute_cdp("Page.captureScreenshot", {"format": "png"})
        if "result" in result and "data" in result["result"]:
            return {"success": True, "image_base64": result["result"]["data"]}
        return {"error": result.get("error", "截图失败")}

    elif action == "click":
        selector = input_data.get("selector")
        if not selector:
            return {"error": "缺少 selector 参数"}
        script = f"document.querySelector('{selector}')?.click()"
        await _execute_cdp("Runtime.evaluate", {"expression": script})
        return {"success": True}

    elif action == "type":
        selector = input_data.get("selector")
        text = input_data.get("text", "")
        if not selector:
            return {"error": "缺少 selector 参数"}
        script = f"""
        (function() {{
            const el = document.querySelector('{selector}');
            if (el) {{
                el.focus();
                el.value = '{text}';
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                return true;
            }}
            return false;
        }})()
        """
        result = await _execute_cdp("Runtime.evaluate", {"expression": script})
        return {"success": result.get("result", {}).get("result", {}).get("value", False)}

    elif action == "evaluate":
        script = input_data.get("script")
        if not script:
            return {"error": "缺少 script 参数"}
        result = await _execute_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return {"success": True, "result": result.get("result", {}).get("result", {}).get("value")}

    elif action == "get_content":
        script = "document.body.innerText"
        result = await _execute_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return {"success": True, "content": result.get("result", {}).get("result", {}).get("value", "")}

    else:
        return {"error": f"未知操作: {action}"}
