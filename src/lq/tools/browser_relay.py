"""
Browser Relay 工具 - 通过 Relay Server 控制用户本地浏览器
"""

TOOL_DEFINITION = {
    "name": "browser_relay",
    "description": (
        "通过 Browser Relay 控制用户本地 Chrome 浏览器。操作类型：\n"
        "- navigate: 打开URL，可选 wait 秒数等页面加载\n"
        "- get_content: 获取页面全部文字内容（★必须优先使用★）\n"
        "- click_text: 用文字内容匹配元素并点击（适合动态页面、卡片列表）\n"
        "- click: 用 CSS 选择器点击元素（CDP 真实鼠标事件）\n"
        "- type: 输入文字（selector 可选，先 click_text 点输入框再 type 更可靠）\n"
        "- evaluate: 执行任意 JS 代码\n"
        "- scroll: 滚动页面（默认向下 500px，可指定 y 值，负数向上）\n"
        "- screenshot: 截图（⚠️经常超时，除非用户主动要求看截图否则不要用）\n"
        "- status: 检查连接状态\n\n"
        "⚠️重要规则⚠️：\n"
        "1. 了解页面用 get_content，禁止 screenshot+vision_analyze（超时浪费30秒）\n"
        "2. 禁止用 selector='textarea'（很多网站评论框是 contenteditable 不是 textarea）\n"
        "3. 禁止用 evaluate 写复杂 JS 找元素/点击（用 click_text 代替，简单可靠）\n"
        "4. 小红书发评论的标准流程（必须严格按此执行）：\n"
        "   a. navigate 到帖子详情页 → get_content 阅读\n"
        "   b. click_text '说点什么...' （激活评论框）\n"
        "   c. type(不带selector, 只传text) （输入评论文字）\n"
        "   d. click_text '发送' （提交）\n"
        "   e. get_content 确认评论已发出\n"
        "5. 不要去通知页回复评论（DOM太复杂），直接导航到帖子页面操作"
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "操作类型：navigate、screenshot、click、click_text、type、evaluate、get_content、scroll、status"
            },
            "url": {"type": "string", "description": "要打开的URL（navigate时使用）"},
            "selector": {"type": "string", "description": "CSS选择器（click时必填，type时可选）"},
            "text": {"type": "string", "description": "要匹配/输入的文字（click_text/type时使用）。type时如果不提供selector，会输入到当前聚焦元素"},
            "script": {"type": "string", "description": "要执行的JS代码（evaluate时使用）"},
            "wait": {"type": "number", "description": "操作后等待秒数（默认0）"},
            "index": {"type": "integer", "description": "当 click_text 匹配到多个元素时，选第几个（从0开始，默认0）"},
            "y": {"type": "integer", "description": "scroll 的像素数（正=下，负=上，默认500）"},
        },
        "required": ["action"]
    }
}

import asyncio
import json
import httpx
import base64
import time
from pathlib import Path

RELAY_URL = "http://127.0.0.1:50518/cdp"
TIMEOUT = 35


async def execute_cdp(method: str, params: dict = None) -> dict:
    payload = {"method": method, "params": params or {}}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(RELAY_URL, json=payload)
        resp.raise_for_status()
        return resp.json()


async def _click_at(x: int, y: int) -> None:
    """在指定坐标发送完整鼠标事件序列：move → down → up。"""
    # mouseMoved 先触发 hover，某些框架（Vue/React）需要此事件
    await execute_cdp("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": x, "y": y,
    })
    await execute_cdp("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1,
    })
    await execute_cdp("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1,
    })


async def _find_element(selector: str) -> dict | None:
    """用 CSS 选择器查找元素并返回中心坐标。"""
    safe = json.dumps(selector)
    script = f"""
    (() => {{
        const el = document.querySelector({safe});
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return null;
        return {{
            x: Math.round(rect.x + rect.width / 2),
            y: Math.round(rect.y + rect.height / 2),
            tag: el.tagName.toLowerCase(),
            text: (el.textContent || '').slice(0, 50).trim()
        }};
    }})()
    """
    result = await execute_cdp("Runtime.evaluate", {
        "expression": script, "returnByValue": True,
    })
    return result.get("result", {}).get("result", {}).get("value")


async def _find_by_text(text: str, index: int = 0) -> dict | None:
    """用文字内容匹配可点击元素，返回第 index 个匹配的坐标。"""
    safe = json.dumps(text)
    script = f"""
    (() => {{
        const target = {safe}.toLowerCase();
        const idx = {index};
        // 宽搜索：所有可能是卡片/链接/按钮的元素
        const candidates = document.querySelectorAll(
            'a, button, [role="button"], [onclick], section, article, '
            + '[class*="card"], [class*="note"], [class*="feed"], [class*="item"], '
            + '[class*="cover"], [class*="title"]'
        );
        const matches = [];
        for (const el of candidates) {{
            const t = (el.textContent || '').toLowerCase();
            if (!t.includes(target)) continue;
            const rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) continue;
            // 跳过超大容器（整个页面级别的 div）
            if (rect.width > window.innerWidth * 0.9 && rect.height > window.innerHeight * 0.9) continue;
            matches.push({{
                x: Math.round(rect.x + rect.width / 2),
                y: Math.round(rect.y + rect.height / 2),
                w: Math.round(rect.width),
                h: Math.round(rect.height),
                tag: el.tagName.toLowerCase(),
                cls: (el.className || '').toString().slice(0, 60),
                text: (el.textContent || '').slice(0, 80).trim()
            }});
        }}
        if (matches.length === 0) return null;
        // 按面积升序排列，优先点击最小的匹配元素（更精确）
        matches.sort((a, b) => (a.w * a.h) - (b.w * b.h));
        const pick = matches[Math.min(idx, matches.length - 1)];
        pick.total = matches.length;
        return pick;
    }})()
    """
    result = await execute_cdp("Runtime.evaluate", {
        "expression": script, "returnByValue": True,
    })
    return result.get("result", {}).get("result", {}).get("value")


async def execute(input_data: dict, context: dict) -> dict:
    action = input_data.get("action")
    wait_sec = input_data.get("wait", 0)

    if action == "status":
        try:
            result = await execute_cdp("Browser.getVersion")
            return {"connected": True, "browser": result.get("result", {}).get("product", "unknown")}
        except Exception as e:
            return {"connected": False, "error": str(e)}

    elif action == "navigate":
        url = input_data.get("url")
        if not url:
            return {"error": "需要提供 url 参数"}
        result = await execute_cdp("Page.navigate", {"url": url})
        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec, 10))
        return {"success": True, "result": result}

    elif action == "screenshot":
        try:
            result = await execute_cdp("Page.captureScreenshot", {"format": "png"})
        except Exception:
            return {
                "success": False,
                "error": "截图超时，请改用 get_content 获取页面文字内容",
            }
        if "result" in result and "data" in result["result"]:
            home = Path.home() / ".lq-nienie"
            path = home / f"browser_screenshot_{int(time.time())}.png"
            path.write_bytes(base64.b64decode(result["result"]["data"]))
            return {
                "success": True,
                "path": str(path),
                "message": f"截图已保存到 {path}，可用 vision_analyze 工具查看",
            }
        return {"success": False, "error": "截图失败，请改用 get_content 获取页面文字内容"}

    elif action == "click":
        selector = input_data.get("selector")
        if not selector:
            return {"error": "需要提供 selector 参数"}
        elem = await _find_element(selector)
        if not elem:
            return {"success": False, "error": f"未找到元素: {selector}"}
        await _click_at(elem["x"], elem["y"])
        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec, 10))
        return {"success": True, "clicked": f"{elem['tag']}: {elem['text']}", "x": elem["x"], "y": elem["y"]}

    elif action == "click_text":
        text = input_data.get("text")
        if not text:
            return {"error": "需要提供 text 参数"}
        index = input_data.get("index", 0)
        elem = await _find_by_text(text, index)
        if not elem:
            return {"success": False, "error": f"未找到包含文字的元素: {text}"}
        await _click_at(elem["x"], elem["y"])
        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec, 10))
        return {
            "success": True,
            "clicked": elem["text"][:60],
            "x": elem["x"], "y": elem["y"],
            "matches": elem.get("total", 1),
        }

    elif action == "type":
        selector = input_data.get("selector")
        text = input_data.get("text")
        if text is None:
            return {"error": "需要提供 text 参数"}
        if selector:
            # 有 selector：先 focus 元素再输入
            safe = json.dumps(selector)
            focus_script = f"""(() => {{
                const el = document.querySelector({safe});
                if (!el) return 'not_found';
                el.focus();
                // contenteditable 需要把光标放到末尾
                if (el.contentEditable === 'true') {{
                    const range = document.createRange();
                    range.selectNodeContents(el);
                    range.collapse(false);
                    const sel = window.getSelection();
                    sel.removeAllRanges();
                    sel.addRange(range);
                }}
                return 'ok';
            }})()"""
            res = await execute_cdp("Runtime.evaluate", {"expression": focus_script, "returnByValue": True})
            status = res.get("result", {}).get("result", {}).get("value", "error")
            if status == "not_found":
                return {"success": False, "error": f"未找到元素: {selector}"}
        # 无 selector 则直接输入到当前聚焦元素（适合 click_text 后接 type）
        await execute_cdp("Input.insertText", {"text": text})
        return {"success": True, "typed": len(text)}

    elif action == "evaluate":
        script = input_data.get("script")
        if not script:
            return {"error": "需要提供 script 参数"}
        result = await execute_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return {"success": True, "result": result.get("result", {}).get("result", {})}

    elif action == "get_content":
        script = "document.body.innerText"
        result = await execute_cdp("Runtime.evaluate", {"expression": script, "returnByValue": True})
        return {"success": True, "content": result.get("result", {}).get("result", {}).get("value", "")}

    elif action == "scroll":
        y = input_data.get("y", 500)
        await execute_cdp("Runtime.evaluate", {"expression": f"window.scrollBy(0, {y})"})
        if wait_sec > 0:
            await asyncio.sleep(min(wait_sec, 10))
        return {"success": True, "scrolled": y}

    else:
        return {"error": f"未知操作: {action}"}
