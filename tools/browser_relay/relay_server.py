"""
Browser Relay Server

本地中继服务器，监听 18792 端口。
- /ws        : Chrome 扩展通过 WebSocket 连接到这里
- POST /cdp  : 灵雀 Agent 通过 HTTP 发送 CDP 命令

架构：
  Agent -> HTTP POST /cdp -> Relay Server -> WebSocket -> Chrome Extension -> chrome.debugger API

启动：python relay_server.py
"""

import asyncio
import json
import logging
from aiohttp import web

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class BrowserRelay:
    def __init__(self):
        self.ws_clients = set()
        self.pending_commands = {}
        self.command_id = 0

    async def ws_handler(self, request):
        ws = web.WebSocketResponse()
        await ws.prepare(request)

        self.ws_clients.add(ws)
        logger.info(f"Chrome extension connected. Total clients: {len(self.ws_clients)}")

        try:
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    cmd_id = data.get('id')
                    if cmd_id and cmd_id in self.pending_commands:
                        future = self.pending_commands.pop(cmd_id)
                        future.set_result(data)
        finally:
            self.ws_clients.discard(ws)
            logger.info(f"Chrome extension disconnected. Remaining: {len(self.ws_clients)}")

        return ws

    async def cdp_handler(self, request):
        if not self.ws_clients:
            return web.json_response({'error': 'No browser connected'}, status=503)

        body = await request.json()
        method = body.get('method')
        params = body.get('params', {})

        self.command_id += 1
        cmd_id = self.command_id

        command = {'id': cmd_id, 'method': method, 'params': params}
        future = asyncio.get_event_loop().create_future()
        self.pending_commands[cmd_id] = future

        ws = next(iter(self.ws_clients))
        await ws.send_json(command)

        try:
            result = await asyncio.wait_for(future, timeout=30)
            return web.json_response(result)
        except asyncio.TimeoutError:
            self.pending_commands.pop(cmd_id, None)
            return web.json_response({'error': 'Command timeout'}, status=504)

relay = BrowserRelay()
app = web.Application()
app.router.add_get('/ws', relay.ws_handler)
app.router.add_post('/cdp', relay.cdp_handler)

if __name__ == '__main__':
    web.run_app(app, host='127.0.0.1', port=18792)
