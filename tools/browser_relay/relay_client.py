"""
Browser Relay Client

提供 async def execute_cdp(method, params) 接口，
通过 HTTP POST 到本地中继服务器发送 CDP 命令。
"""

import httpx

RELAY_URL = "http://127.0.0.1:18792/cdp"
TIMEOUT = 35  # 略大于 server 侧的 30 秒超时


async def execute_cdp(method: str, params: dict | None = None) -> dict:
    """
    向 Browser Relay Server 发送一条 CDP 命令并返回结果。

    Args:
        method: CDP 方法名，例如 "Page.navigate"
        params: CDP 参数字典，例如 {"url": "https://example.com"}

    Returns:
        服务器返回的 JSON 字典。包含 result 或 error 字段。

    Raises:
        httpx.ConnectError: relay_server 未启动
        httpx.TimeoutException: 命令执行超时
    """
    payload = {"method": method, "params": params or {}}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(RELAY_URL, json=payload)
        resp.raise_for_status()
        return resp.json()
