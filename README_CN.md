# 灵雀 LingQue

基于**平台无关内核**的个人 AI 助理框架，核心引擎（对话路由、工具执行、记忆管理、群聊智能）不依赖任何特定聊天平台，通过可插拔适配器接入不同平台。已内置飞书（Lark）适配器和本地终端适配器，新增平台（Discord、Telegram、Slack 等）只需编写一个适配器文件。支持私聊/群聊智能回复、日历管理、长期记忆，并能在运行时自主创建新工具扩展自身能力。

## 特性

- **平台无关内核** — `PlatformAdapter` 抽象基类将整个引擎与具体聊天平台彻底解耦，路由、记忆、会话、工具系统均无任何平台特定导入。新增平台（Discord、Telegram、Slack 等）只需一个适配器文件
- **可插拔适配器** — 内置飞书适配器（WebSocket 长连接，私聊即时回复，群聊三层智能介入）和本地终端适配器，两者走完全相同的统一事件管线
- **本地聊天模式** — `lq chat @name` 在终端启动交互式对话，支持完整工具链，无需任何外部聊天平台凭证
- **长期记忆** — SOUL.md 人格定义 + MEMORY.md 全局记忆 + per-chat 对话记忆 + 每日日志
- **多轮会话** — per-chat 独立会话文件、自动压缩、重启恢复
- **日历集成** — 通过适配器查询/创建日程，每日晨报（已内置飞书日历支持）
- **卡片消息** — 结构化信息展示（日程卡、任务卡、信息卡），由各适配器原生渲染
- **自我认知** — 助理了解自己的架构，可读写自身配置文件
- **自主工具创建** — 助理可在对话中编写、验证、加载新工具插件
- **通用 Agent 能力** — 21 个内置工具，覆盖记忆、日历、消息、联网搜索、代码执行、文件读写、Claude Code 委托
- **多实例群聊协作** — 多个独立 bot 共存同一群聊，自动感知邻居、避免抢答，通过消息信号自主推断彼此身份并持久记忆
- **群聊智能** — @at 消息防抖合并连续消息，ReplyGate 串行化并发回复并带冷却窗口
- **社交互动** — 入群自我介绍、新成员欢迎消息、每日早安问候（deterministic jitter 防重发）
- **API 消耗追踪** — 按日/月统计 Token 用量与费用
- **多实例** — 同时运行多个独立助理，各自隔离，无共享状态
- **拼音路径** — 中文名自动转拼音 slug，避免文件系统问题
- **测试框架** — 5 级 LLM 能力测试套件（基础 → 推理 → 编程 → 综合 → 工程），自动化测试基座

## 快速开始

### 前置条件

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)
- 任意 Anthropic 兼容 API（如各大云厂商提供的 Claude API 转发服务）
- *（可选）* 飞书自建应用（需开通 IM + Calendar 权限）— 仅飞书适配器模式需要

### 安装

```bash
cd <your-path>/lingque
uv sync
```

### 准备 `.env`

项目根目录需要 `.env` 文件：（有多个 agents 的话准备多个 .env 即可，比如 .env.agent1 .env.agent2）

```
# 必填 — LLM API
ANTHROPIC_BASE_URL=https://your-provider.com/api/anthropic
ANTHROPIC_AUTH_TOKEN=xxxxx

# 可选 — 仅飞书适配器模式需要
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
```

### 初始化实例

```bash
# 从 .env 读取凭证（开发模式）
uv run lq init --name 奶油 --from-env .env
```

中文名会自动转成拼音 slug 作为目录名：

```
~/.lq-naiyou/
├── config.json          # 运行配置
├── SOUL.md              # 人格定义 ← 编辑这个
├── MEMORY.md            # 长期记忆
├── HEARTBEAT.md         # 心跳任务定义
├── bot_identities.json  # 自主推断的其他 bot 身份映射
├── groups.json          # 已知群聊 ID（用于早安问候等）
├── memory/              # 每日日志
├── sessions/            # 会话持久化
├── groups/              # 群聊上下文
├── tools/               # 自定义工具插件
├── logs/                # 运行日志
└── stats.jsonl          # API 消耗记录
```

### 编辑人格

启动前先编辑 SOUL.md，定义你的助理性格：

```bash
uv run lq edit @奶油 soul
```

### 启动

```bash
# 飞书模式（默认）
uv run lq start @奶油

# 纯本地模式（无需飞书凭证）
uv run lq start @奶油 --adapter local

# 多平台模式（飞书 + 本地同时连接）
uv run lq start @奶油 --adapter feishu,local

# 后台运行
nohup uv run lq start @奶油 &
uv run lq logs @奶油            # tail -f 日志
uv run lq status @奶油          # 查看状态 + API 消耗
uv run lq stop @奶油            # 停止
```

> 实例名支持中文或拼音：`@奶油` 和 `@naiyou` 等价。

## 架构

### 平台抽象

代码分为**平台无关内核**和**平台适配器**两层：

```
platform/
├── types.py     — 标准数据类型（IncomingMessage, OutgoingMessage, Reaction 等）
├── adapter.py   — PlatformAdapter ABC（9 个抽象 + 4 个可选方法）
└── multi.py     — MultiAdapter（多平台复合适配器）

feishu/adapter.py  — FeishuAdapter（内部封装 sender + listener）
conversation.py    — LocalAdapter（终端模式，双模式：gateway 模式含 stdin/inbox 事件源，chat 模式被动连接）
```

内核（router / gateway / memory）仅依赖 `PlatformAdapter` 和标准类型，不直接引用飞书 SDK。

### 事件流

所有适配器通过统一路径产生标准事件：

```
事件源（按适配器）：
  FeishuAdapter:  飞书 WS → _event_converter → queue.put()
  LocalAdapter:   stdin → _read_stdin → queue.put()
                  inbox.txt → _watch_inbox → queue.put()

统一管线：
  asyncio.Queue → _consume_messages → router.handle(标准事件)
    标准事件 = {"event_type": "message"|"reaction"|"interaction"|"member_change"|"eval_timeout", ...}
    ├── "message"       → IncomingMessage → _dispatch_message → _handle_private / _handle_group
    ├── "interaction"   → CardAction → _handle_card_action
    ├── "reaction"      → Reaction → _handle_reaction_event
    ├── "member_change" → _handle_member_change
    └── "eval_timeout"  → _evaluate_buffer

输出侧：
  router → adapter.start_thinking() → adapter.send(OutgoingMessage) → adapter.stop_thinking()
    FeishuAdapter:  OnIt 表情 → REST API 发送 → 移除表情
    LocalAdapter:   ⏳ 思考指示器 → 终端卡片/文本 → 清除指示器
```

## 内置工具

助理在对话中可使用以下 21 个内置工具：

**记忆与自我管理**

| 工具 | 说明 |
|------|------|
| `write_memory` | 将信息写入 MEMORY.md 全局长期记忆 |
| `write_chat_memory` | 写入当前对话专属的 per-chat 记忆 |
| `read_self_file` | 读取自身配置（SOUL.md / MEMORY.md / HEARTBEAT.md） |
| `write_self_file` | 修改自身配置 |

**日历与消息**

| 工具 | 说明 |
|------|------|
| `calendar_create_event` | 创建日程 |
| `calendar_list_events` | 查询日历事件 |
| `send_card` | 发送结构化卡片消息 |
| `send_message` | 向任意会话发送文本消息 |
| `schedule_message` | 定时发送消息 |

**联网与信息获取**

| 工具 | 说明 |
|------|------|
| `web_search` | 搜索互联网获取实时信息 |
| `web_fetch` | 抓取并提取网页文本内容 |

**代码与文件执行**

| 工具 | 说明 |
|------|------|
| `run_python` | 执行 Python 代码片段，用于计算和数据处理 |
| `run_bash` | 执行 Shell 命令 |
| `run_claude_code` | 委托复杂任务给 Claude Code 子进程 |
| `read_file` | 读取文件系统中的文件 |
| `write_file` | 创建或写入文件 |

**自定义工具管理**

| 工具 | 说明 |
|------|------|
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
    context 包含: adapter, memory, calendar
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

在任意已连接的聊天平台（或本地终端）中对助理说：

> "帮我创建一个工具，可以把文本翻译成英文"

助理会自动编写代码、验证、加载，之后就可以在对话中使用新工具。

## CLI 命令速查

| 命令 | 说明 |
|------|------|
| `uv run lq init --name NAME [--from-env .env]` | 初始化实例 |
| `uv run lq start @NAME [--adapter TYPE]` | 启动（TYPE: `feishu`, `local`, 或逗号分隔如 `feishu,local`） |
| `uv run lq stop @NAME` | 停止 |
| `uv run lq restart @NAME [--adapter TYPE]` | 重启 |
| `uv run lq list` | 列出所有实例 |
| `uv run lq status @NAME` | 运行状态 + API 消耗统计 |
| `uv run lq logs @NAME [--since 1h]` | 查看日志 |
| `uv run lq edit @NAME soul/memory/heartbeat/config` | 编辑配置文件 |
| `uv run lq chat @NAME` | 交互式本地聊天（终端） |
| `uv run lq chat @NAME "消息"` | 单条消息模式 |
| `uv run lq say @NAME "消息"` | `chat` 的别名 |
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
├── gateway.py          # 主编排器（创建适配器，运行异步任务）
├── router/             # 消息路由包（按职责拆分）
│   ├── defs.py        # LLM 工具定义（JSON Schema）
│   ├── core.py        # MessageRouter 类 + 事件分发 + 回复锁
│   ├── private.py     # 私聊处理 + 自我反思 + 好奇心信号
│   ├── group.py       # 群聊三层介入 + 协作记录
│   ├── tool_loop.py   # Agent 工具调用循环 + 审批机制
│   ├── tool_exec.py   # 工具执行分发 + 多模态内容
│   └── web_tools.py   # 联网搜索/抓取 + 代码执行 + 文件操作
├── prompts.py          # 集中管理所有 prompt、工具描述、约束块
├── conversation.py     # 本地交互式聊天（lq chat / lq say）+ LocalAdapter
├── tools.py            # 自定义工具插件系统
├── buffer.py           # 群聊消息缓冲区
├── session.py          # per-chat 会话管理 + compaction
├── memory.py           # SOUL/MEMORY/per-chat 记忆/日志 + 自我认知
├── heartbeat.py        # 定时心跳
├── intent.py           # 后处理意图检测
├── subagent.py         # 轻量 LLM 参数提取
├── stats.py            # API 消耗统计
├── templates.py        # 模板生成
├── timeparse.py        # 时间表达式解析
├── platform/
│   ├── types.py        # 平台无关数据类型（IncomingMessage, OutgoingMessage 等）
│   ├── adapter.py      # PlatformAdapter ABC（所有适配器的抽象接口）
│   └── multi.py        # MultiAdapter（多平台复合适配器）
├── executor/
│   ├── api.py          # Anthropic API（含重试 + tool use）
│   └── claude_code.py  # Claude Code 子进程
└── feishu/
    ├── adapter.py      # FeishuAdapter（PlatformAdapter 实现，封装 sender + listener）
    ├── listener.py     # WebSocket 事件接收（适配器内部）
    ├── sender.py       # REST API 调用（适配器内部）
    ├── calendar.py     # 日历 API
    └── cards.py        # 卡片构建

tests/
├── test_platform.py        # 平台抽象层单元测试（pytest）
├── harness.py              # 测试基座（调用 lq say，校验响应）
├── run_all.py              # 多级测试运行器
├── test_infrastructure.py  # 基础设施与会话测试
├── test_level1_basic.py    # 第 1 级：基本工具调用
├── test_level2_reasoning.py # 第 2 级：数学与逻辑推理
├── test_level3_coding.py   # 第 3 级：代码生成与调试
├── test_level4_complex.py  # 第 4 级：联网 + Agent 循环
└── test_level5_project.py  # 第 5 级：大型项目构建与部署
```
