# Browser Relay

灵雀 Browser Relay 系统，通过本地中继服务器 + Chrome MV3 扩展实现真实浏览器远程控制。

## 架构

```
灵雀 Agent -> HTTP POST /cdp -> Relay Server (18792) -> WebSocket -> Chrome Extension -> chrome.debugger API
```

## 使用方法

1. 启动中继服务器：
```bash
cd tools/browser_relay
pip install aiohttp
python relay_server.py
```

2. 安装 Chrome 扩展：
   - 打开 chrome://extensions/
   - 开启「开发者模式」
   - 点击「加载已解压的扩展程序」
   - 选择 `extension/` 目录

3. 灵雀调用示例：
```python
import httpx
resp = httpx.post('http://127.0.0.1:18792/cdp', json={
    'method': 'Page.navigate',
    'params': {'url': 'https://example.com'}
})
print(resp.json())
```

## 优势

- 绕过 headless 检测
- 复用已有登录态
- 真实浏览器环境
