# LingQue

[中文文档](README_CN.md)

A personal AI assistant framework deeply integrated with [Feishu (Lark)](https://www.feishu.cn/). Supports private/group chat with intelligent replies, calendar management, long-term memory, and runtime self-extension through tool creation.

## Features

- **Feishu-native** — WebSocket persistent connection, instant DM replies, three-layer group chat intervention
- **Long-term memory** — SOUL.md persona + MEMORY.md persistent memory + daily journals
- **Multi-turn sessions** — Context management, auto-compaction, restart recovery
- **Calendar integration** — Query/create Feishu calendar events, daily briefings
- **Card messages** — Structured information display (schedule cards, task cards, info cards)
- **Self-awareness** — The assistant understands its own architecture, can read/write its config files
- **Runtime tool creation** — The assistant can write, validate, and load new tool plugins during conversations
- **API usage tracking** — Daily/monthly token usage and cost statistics
- **Multi-instance** — Run multiple independent assistants simultaneously, fully isolated
- **Pinyin paths** — Chinese names auto-convert to pinyin slugs for filesystem compatibility

## Quick Start

### Prerequisites

- Python >= 3.11
- [uv](https://docs.astral.sh/uv/)
- A Feishu custom app (with IM + Calendar permissions)
- Any Anthropic-compatible API endpoint (e.g. cloud provider proxies, self-hosted gateways)

### Install

```bash
cd <your-path>/lingque
uv sync
```

### Prepare `.env`

Create a `.env` file in the project root (for multiple agents, use `.env.agent1`, `.env.agent2`, etc.):

```
FEISHU_APP_ID=cli_xxxxx
FEISHU_APP_SECRET=xxxxx
ANTHROPIC_BASE_URL=https://your-provider.com/api/anthropic
ANTHROPIC_AUTH_TOKEN=xxxxx
```

### Initialize an Instance

```bash
# Read credentials from .env (dev mode)
uv run lq init --name 奶油 --from-env .env
```

Chinese names are auto-converted to pinyin slugs for directory names:

```
~/.lq-naiyou/
├── config.json      # Runtime config
├── SOUL.md          # Persona definition ← edit this
├── MEMORY.md        # Long-term memory
├── HEARTBEAT.md     # Heartbeat task definitions
├── memory/          # Daily journals
├── sessions/        # Session persistence
├── groups/          # Group chat context
├── tools/           # Custom tool plugins
├── logs/            # Runtime logs
└── stats.jsonl      # API usage records
```

### Edit Persona

Before starting, edit SOUL.md to define your assistant's personality:

```bash
uv run lq edit @奶油 soul
```

### Start

```bash
# Foreground (for debugging)
uv run lq start @奶油

# Background
nohup uv run lq start @奶油 &
uv run lq logs @奶油            # tail -f logs
uv run lq status @奶油          # status + API usage
uv run lq stop @奶油            # stop
```

> Instance names work in both Chinese and pinyin: `@奶油` and `@naiyou` are equivalent.

## Architecture

```
Main thread: asyncio.run(gateway.run())
├── message_consumer  — asyncio.Queue → router.handle()
└── heartbeat         — periodic tasks (daily briefing, cost alerts)

Feishu thread (daemon): ws_client.start()
└── on_message() → loop.call_soon_threadsafe(queue.put_nowait, data)
```

Message flow:

```
Feishu message → listener (WS thread) → Queue → router
  ├── DM → session mgmt → LLM (tool use loop) → send reply
  └── Group → rule filter → buffer → LLM eval → maybe reply
```

## Built-in Tools

The assistant has access to 11 built-in tools during conversations:

| Tool | Description |
|------|-------------|
| `write_memory` | Write information to MEMORY.md long-term memory |
| `calendar_create_event` | Create a Feishu calendar event |
| `calendar_list_events` | Query calendar events |
| `send_card` | Send a Feishu card message |
| `read_self_file` | Read own config (SOUL.md / MEMORY.md / HEARTBEAT.md) |
| `write_self_file` | Modify own config |
| `create_custom_tool` | Create a new custom tool plugin |
| `list_custom_tools` | List installed custom tools |
| `test_custom_tool` | Validate tool code (without creating) |
| `delete_custom_tool` | Delete a custom tool |
| `toggle_custom_tool` | Enable/disable a custom tool |

## Custom Tool System

The assistant can autonomously create new tools to extend its capabilities during conversations. Tools are stored as Python files in the `tools/` directory.

### Tool File Format

```python
"""Get current time"""

TOOL_DEFINITION = {
    "name": "get_time",
    "description": "Get current date and time",
    "input_schema": {
        "type": "object",
        "properties": {
            "timezone": {
                "type": "string",
                "description": "Timezone, e.g. Asia/Shanghai",
                "default": "Asia/Shanghai",
            },
        },
    },
}

async def execute(input_data: dict, context: dict) -> dict:
    """
    context includes: sender, memory, calendar
    """
    from datetime import datetime
    import zoneinfo
    tz = zoneinfo.ZoneInfo(input_data.get("timezone", "Asia/Shanghai"))
    now = datetime.now(tz)
    return {"success": True, "time": now.isoformat()}
```

### Security Restrictions

Tool code is statically analyzed via AST. The following modules are blocked:
`os`, `subprocess`, `shutil`, `sys`, `socket`, `ctypes`, `signal`, `multiprocessing`, `threading`

### Usage

Simply tell the assistant in Feishu:

> "Create a tool that translates text to English"

The assistant will automatically write the code, validate it, and load it. The new tool is then available in conversations.

## CLI Reference

| Command | Description |
|---------|-------------|
| `uv run lq init --name NAME [--from-env .env]` | Initialize instance |
| `uv run lq start @NAME` | Start |
| `uv run lq stop @NAME` | Stop |
| `uv run lq restart @NAME` | Restart |
| `uv run lq list` | List all instances |
| `uv run lq status @NAME` | Status + API usage stats |
| `uv run lq logs @NAME [--since 1h]` | View logs |
| `uv run lq edit @NAME soul/memory/heartbeat/config` | Edit config files |
| `uv run lq say @NAME "message"` | Send a message to instance |
| `uv run lq upgrade @NAME` | Upgrade framework |

## Configuration

Edit `~/.lq-{slug}/config.json`:

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
      "note": "Tech discussion group",
      "eval_threshold": 5
    }
  ]
}
```

| Field | Description |
|-------|-------------|
| `name` | Display name (supports Chinese) |
| `slug` | Directory name (auto-generated pinyin) |
| `model` | LLM model name |
| `heartbeat_interval` | Heartbeat interval (seconds) |
| `active_hours` | Active hours `[start, end)` |
| `cost_alert_daily` | Daily cost alert threshold (USD) |
| `groups[].note` | Group description, helps LLM decide whether to intervene |
| `groups[].eval_threshold` | Message count to trigger group evaluation |

## Project Structure

```
src/lq/
├── cli.py              # CLI entry point
├── config.py           # Config loader (with pinyin slug)
├── gateway.py          # Main orchestrator (dual-thread architecture)
├── router.py           # Message routing + three-layer intervention + tool calls
├── tools.py            # Custom tool plugin system
├── buffer.py           # Group chat message buffer
├── session.py          # Session management + compaction
├── memory.py           # SOUL/MEMORY/journals + self-awareness
├── heartbeat.py        # Periodic heartbeat
├── stats.py            # API usage statistics
├── templates.py        # Template generation
├── executor/
│   ├── api.py          # Anthropic API (with retry + tool use)
│   └── claude_code.py  # Claude Code subprocess
└── feishu/
    ├── listener.py     # WebSocket event receiver
    ├── sender.py       # Message sender (text + cards)
    ├── calendar.py     # Calendar API
    └── cards.py        # Card builder
```
