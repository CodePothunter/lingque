# 灵雀 LingQue

深度集成飞书的个人 AI 助理框架。支持私聊/群聊智能回复、日历管理、长期记忆，并能在运行时自主创建新工具扩展自身能力。

## 特性

- **飞书原生** — WebSocket 长连接，私聊即时回复，群聊三层智能介入
- **长期记忆** — SOUL.md 人格定义 + MEMORY.md 持久记忆 + 每日日志
- **多轮会话** — 上下文管理、自动压缩、重启恢复
- **日历集成** — 查询/创建飞书日程，每日晨报
- **卡片消息** — 结构化信息展示（日程卡、任务卡、信息卡）
- **自我认知** — 助理了解自己的架构，可读写自身配置文件
- **自主工具创建** — 助理可在对话中编写、验证、加载新工具插件
- **API 消耗追踪** — 按日/月统计 Token 用量与费用
- **多实例** — 同时运行多个独立助理，各自隔离
- **拼音路径** — 中文名自动转拼音 slug，避免文件系统问题

## 快速开始

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)
- 一个飞书自建应用（需开通 IM + Calendar 权限）
- 任意 Anthropic 兼容 API（如各大云厂商提供的 Claude API 转发服务）

### 安装

```bash
cd <your-path>/lingque
uv sync
```

### 准备 `.env`

项目根目录需要 `.env` 文件：（有多个 agents 的话准备多个 .env 即可，比如 .env.agent1 .env.agent2）

```
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
ANTHROPIC_BASE_URL=https://your-provider.com/api/anthropic
ANTHROPIC_AUTH_TOKEN=xxxxx
```

### 初始化实例

```bash
# 从 .env 读取凭证（开发模式）
uv run lq init --name 奶油 --from-env .env
```

中文名会自动转成拼音 slug 作为目录名：

```
~/.lq-naiyou/
├── config.json      # 运行配置
├── SOUL.md          # 人格定义 ← 编辑这个
├── MEMORY.md        # 长期记忆
├── HEARTBEAT.md     # 心跳任务定义
├── memory/          # 每日日志
├── sessions/        # 会话持久化
├── groups/          # 群聊上下文
├── tools/           # 自定义工具插件
├── logs/            # 运行日志
└── stats.jsonl      # API 消耗记录
```

### 编辑人格

启动前先编辑 SOUL.md，定义你的助理性格：

```bash
uv run lq edit @奶油 soul
```

### 启动

```bash
# 前台运行（调试用）
uv run lq start @奶油

# 后台运行
nohup uv run lq start @奶油 &
uv run lq logs @奶油            # tail -f 日志
uv run lq status @奶油          # 查看状态 + API 消耗
uv run lq stop @奶油            # 停止
```

> 实例名支持中文或拼音：`@奶油` 和 `@naiyou` 等价。

## 架构

```
主线程: asyncio.run(gateway.run())
├── message_consumer  — asyncio.Queue → router.handle()
└── heartbeat         — 定时心跳（晨报、费用告警）

飞书线程 (daemon): ws_client.start()
└── on_message() → loop.call_soon_threadsafe(queue.put_nowait, data)
```

消息处理流程：

```
飞书消息 → listener (WS线程) → Queue → router
  ├── 私聊 → 会话管理 → LLM (tool use loop) → 发送回复
  └── 群聊 → 规则过滤 → 缓冲区 → LLM判断介入 → 可能回复
```

## 内置工具

助理在对话中可使用以下 11 个内置工具：

| 工具 | 说明 |
|------|------|
| `write_memory` | 将信息写入 MEMORY.md 长期记忆 |
| `calendar_create_event` | 在飞书日历创建日程 |
| `calendar_list_events` | 查询日历事件 |
| `send_card` | 发送飞书卡片消息 |
| `read_self_file` | 读取自身配置（SOUL.md / MEMORY.md / HEARTBEAT.md） |
| `write_self_file` | 修改自身配置 |
| `create_custom_tool` | 创建新的自定义工具插件 |
| `list_custom_tools` | 列出已安装的自定义工具 |
| `test_custom_tool` | 校验工具代码（不创建） |
| `delete_custom_tool` | 删除自定义工具 |
| `toggle_custom_tool` | 启用/禁用自定义工具 |

## 自定义工具系统

助理可以在对话中自主创建新工具来扩展能力。工具以 Python 文件形式存储在 `tools/` 目录。

### 工具文件格式

```python
"""获取当前时间"""

TOOL_DEFINITION = {
    "name": "get_time",
    "description": "获取当前日期和时间",
    "input_schema": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "时区，如 Asia/Shanghai",
                "default": "Asia/Shanghai",
            },
        },
    },
}

async def execute(input_data: dict, context: dict) -> dict:
    """
    context 包含: sender, memory, calendar
    """
    from datetime import datetime
    import zoneinfo
    tz = zoneinfo.ZoneInfo(input_data.get("timezone", "Asia/Shanghai"))
    now = datetime.now(tz)
    return {"success": True, "time": now.isoformat()}
```

### 安全限制

工具代码经过 AST 静态分析，禁止导入以下模块：
`os`, `subprocess`, `shutil`, `sys`, `socket`, `ctypes`, `signal`, `multiprocessing`, `threading`

### 使用方式

在飞书中直接对助理说：

> "帮我创建一个工具，可以把文本翻译成英文"

助理会自动编写代码、验证、加载，之后就可以在对话中使用新工具。

## CLI 命令速查

| 命令 | 说明 |
|------|------|
| `uv run lq init --name NAME [--from-env .env]` | 初始化实例 |
| `uv run lq start @NAME` | 启动 |
| `uv run lq stop @NAME` | 停止 |
| `uv run lq restart @NAME` | 重启 |
| `uv run lq list` | 列出所有实例 |
| `uv run lq status @NAME` | 运行状态 + API 消耗统计 |
| `uv run lq logs @NAME [--since 1h]` | 查看日志 |
| `uv run lq edit @NAME soul/memory/heartbeat/config` | 编辑配置文件 |
| `uv run lq say @NAME "消息"` | 给实例发消息 |
| `uv run lq upgrade @NAME` | 升级框架 |

## 配置

编辑 `~/.lq-{slug}/config.json`：

```json
{
  "name": "奶油",
  "slug": "naiyou",
  "model": "claude-opus-4-6",
  "heartbeat_interval": 3600,
  "active_hours": [8, 23],
  "cost_alert_daily": 5.0,
  "groups": [
    {
      "chat_id": "oc_xxx",
      "note": "技术讨论群",
      "eval_threshold": 5
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `name` | 显示名（可中文） |
| `slug` | 目录名（自动生成的拼音） |
| `model` | LLM 模型名 |
| `heartbeat_interval` | 心跳间隔（秒） |
| `active_hours` | 活跃时段 `[开始小时, 结束小时)` |
| `cost_alert_daily` | 日消耗告警阈值（USD） |
| `groups[].note` | 群描述，帮助 LLM 判断是否介入 |
| `groups[].eval_threshold` | 群聊触发评估的消息数 |

## 目录结构

```
src/lq/
├── cli.py              # CLI 入口
├── config.py           # 配置加载（含拼音 slug）
├── gateway.py          # 主编排器（双线程架构）
├── router.py           # 消息路由 + 三层介入 + 工具调用
├── tools.py            # 自定义工具插件系统
├── buffer.py           # 群聊消息缓冲区
├── session.py          # 会话管理 + compaction
├── memory.py           # SOUL/MEMORY/日志 + 自我认知
├── heartbeat.py        # 定时心跳
├── stats.py            # API 消耗统计
├── templates.py        # 模板生成
├── executor/
│   ├── api.py          # Anthropic API（含重试 + tool use）
│   └── claude_code.py  # Claude Code 子进程
└── feishu/
    ├── listener.py     # WebSocket 事件接收
    ├── sender.py       # 消息发送（文本 + 卡片）
    ├── calendar.py     # 日历 API
    └── cards.py        # 卡片构建
```
