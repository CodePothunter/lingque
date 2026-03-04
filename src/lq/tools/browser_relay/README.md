# Browser Relay for LingQue

通过本地中继服务器 + Chrome 扩展，让灵雀 Agent 控制真实浏览器。

## 架构

```
灵雀 Agent -> HTTP POST /cdp -> Relay Server (50518) -> WebSocket -> Chrome Extension -> chrome.debugger API
```

## 端口

- **50518** - 中继服务器监听端口（绑定 127.0.0.1）

## 使用方法

### 1. 启动中继服务器

```bash
cd src/lq/tools/browser_relay
pip install aiohttp
python relay_server.py
```

### 2. 安装 Chrome 扩展

**本地浏览器**：
1. Chrome 打开 `chrome://extensions/`
2. 开启"开发者模式"
3. 点击"加载已解压的扩展程序"
4. 选择 `extension/` 目录

**远程浏览器（SSH 隧道）**：
```bash
# 在浏览器所在机器执行
ssh -L 50518:127.0.0.1:50518 ubuntu@服务器IP
```
然后按本地浏览器步骤安装扩展。

### 3. 灵雀调用

```python
browser_relay(action="status")  # 检查连接
browser_relay(action="navigate", url="https://example.com")  # 打开网页
browser_relay(action="screenshot")  # 截图
browser_relay(action="click", selector="#button")  # 点击
browser_relay(action="type", selector="#input", text="hello")  # 输入
browser_relay(action="evaluate", script="document.title")  # 执行JS
browser_relay(action="get_content")  # 获取页面文本
```

## 文件说明

```
browser_relay/
├── relay_server.py      # 中继服务器
├── relay_client.py      # 客户端库（独立使用）
├── extension/
│   ├── manifest.json    # Chrome MV3 清单
│   └── background.js    # 扩展后台脚本
└── README.md            # 本文件

browser_relay.py         # 灵雀自定义工具（在 tools/ 目录）
```

## 注意事项

- 扩展使用 `chrome.debugger` API，Chrome 顶部会显示警告条
- 扩展会自动重连中继服务器
- 跨机器使用推荐 SSH 隧道，安全简单
