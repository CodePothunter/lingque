"""
Browser Relay Server

本地中继服务器，监听 50518 端口。
- /ws        : Chrome 扩展通过 WebSocket 连接到这里
- POST /cdp  : 灵雀 Agent 通过 HTTP 发送 CDP 命令

架构：
  Agent -> HTTP POST /cdp -> Relay Server -> WebSocket -> Chrome Extension -> chrome.debugger API

启动：python relay_server.py

跨机器使用（SSH 隧道）：
  在浏览器所在机器执行: ssh -L 50518:127.0.0.1:50518 用户名@服务器IP
  扩展连接 ws://127.0.0.1:50518/ws
"""

import asyncio
import json
import logging
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BrowserRelay:
    def __init__(self):
        self.ws_client: web.WebSocketResponse | None = None
        self.pending_commands = {}
        self.command_id = 0

    async def ws_handler(self, request):
        ws = web.WebSocketResponse(heartbeat=20)
        await ws.prepare(request)

        # 新连接顶掉旧连接
        if self.ws_client is not None and not self.ws_client.closed:
            logger.info("Closing stale client, replaced by new connection")
            await self.ws_client.close()
        self.ws_client = ws
        logger.info("Chrome extension connected")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    cmd_id = data.get('id')
                    if cmd_id and cmd_id in self.pending_commands:
                        future = self.pending_commands.pop(cmd_id)
                        if not future.done():
                            future.set_result(data)
        finally:
            if self.ws_client is ws:
                self.ws_client = None
            logger.info("Chrome extension disconnected")

        return ws

    async def cdp_handler(self, request):
        ws = self.ws_client
        if ws is None or ws.closed:
            self.ws_client = None
            return web.json_response({'error': 'No browser connected'}, status=503)

        body = await request.json()
        method = body.get('method')
        params = body.get('params', {})

        self.command_id += 1
        cmd_id = self.command_id

        command = {'id': cmd_id, 'method': method, 'params': params}
        future = asyncio.get_event_loop().create_future()
        self.pending_commands[cmd_id] = future

        try:
            await ws.send_json(command)
        except ConnectionResetError:
            self.pending_commands.pop(cmd_id, None)
            self.ws_client = None
            return web.json_response({'error': 'Browser connection lost'}, status=503)

        try:
            result = await asyncio.wait_for(future, timeout=30)
            return web.json_response(result)
        except asyncio.TimeoutError:
            self.pending_commands.pop(cmd_id, None)
            return web.json_response({'error': 'Command timeout'}, status=504)

relay = BrowserRelay()
app = web.Application()

async def status_handler(request):
    connected = relay.ws_client is not None and not relay.ws_client.closed
    return web.json_response({
        'connected': connected,
        'pending': len(relay.pending_commands),
    })

app.router.add_get('/ws', relay.ws_handler)
app.router.add_post('/cdp', relay.cdp_handler)
app.router.add_get('/status', status_handler)

if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=50518)
