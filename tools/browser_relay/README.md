# Browser Relay System

通过本地中继服务器 + Chrome MV3 扩展，让灵雀 Agent 能控制真实浏览器，绕过 headless 检测和登录态验证。

## 架构

```
灵雀 Agent -> browser_relay 工具 -> HTTP POST /cdp -> Relay Server -> WebSocket -> Chrome Extension -> chrome.debugger API
```

## 使用步骤

### 1. 启动中继服务器

```bash
cd tools/browser_relay
python relay_server.py
# 监听 127.0.0.1:18792
```

### 2. 安装 Chrome 扩展

1. 打开 Chrome，访问 `chrome://extensions/`
2. 开启"开发者模式"
3. 点击"加载已解压的扩展程序"
4. 选择 `tools/browser_relay/extension` 目录
5. 扩展会自动连接到本地 relay server

### 3. 灵雀调用

灵雀会自动加载 `tools/browser_relay.py` 作为自定义工具，可使用以下 action：

- `status` - 检查浏览器连接状态
- `navigate` - 打开 URL
- `screenshot` - 截图（返回 base64）
- `click` - 点击元素
- `type` - 输入文字
- `evaluate` - 执行 JavaScript
- `get_content` - 获取页面文本

## 文件说明

```
tools/
├── browser_relay.py      # 灵雀自定义工具（供 Agent 调用）
└── browser_relay/
    ├── relay_server.py   # 中继服务器
    ├── relay_client.py   # 客户端库（供其他程序使用）
    ├── extension/
    │   ├── manifest.json # Chrome MV3 清单
    │   └── background.js # 扩展后台脚本
    └── README.md         # 本文档
```

## 对比 OpenClaw

| 维度 | OpenClaw | 灵雀 Browser Relay |
|------|----------|-------------------|
| 原理 | 本地中继 + Chrome 扩展 | 相同 |
| 端口 | 18792 | 18792（兼容） |
| 用途 | 云端 Agent 控制 | 灵雀 Agent 控制 |
| 集成 | 需要 OpenClaw 平台 | 直接集成到灵雀工具系统 |

## 注意事项

- 需要先启动 relay_server.py，再安装扩展
- 扩展只在浏览器打开时工作
- 使用 chrome.debugger API 会显示 Chrome 警告条（正常现象）
