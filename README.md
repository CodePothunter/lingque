# LingQue

[中文文档](README_CN.md)

A personal AI assistant framework with a platform-agnostic core and pluggable chat platform adapters. Currently supports [Feishu (Lark)](https://www.feishu.cn/) and local terminal mode. Features include private/group chat with intelligent replies, calendar management, long-term memory, and runtime self-extension through tool creation.

## Features

- **Platform-agnostic core** — `PlatformAdapter` ABC decouples the core from any specific chat platform; adding a new platform requires only one adapter file
- **Feishu adapter** — WebSocket persistent connection, instant DM replies, three-layer group chat intervention
- **Local dev mode** — `lq say @name` launches an interactive terminal conversation with full tool support, no Feishu dependency required
- **Long-term memory** — SOUL.md persona + MEMORY.md global memory + per-chat memory + daily journals
- **Multi-turn sessions** — Per-chat session files, auto-compaction, restart recovery
- **Calendar integration** — Query/create Feishu calendar events, daily briefings
- **Card messages** — Structured information display (schedule cards, task cards, info cards)
- **Self-awareness** — The assistant understands its own architecture, can read/write its config files
- **Runtime tool creation** — The assistant can write, validate, and load new tool plugins during conversations
- **Generalized agent** — 21 built-in tools covering memory, calendar, messaging, web search, code execution, file I/O, and Claude Code delegation
- **Multi-bot group collaboration** — Multiple independent bots coexist in the same group chat, auto-detect neighbors, avoid answering when not addressed, and autonomously infer each other's identities from message context
- **Group chat intelligence** — @at message debounce merges rapid-fire messages, ReplyGate serializes concurrent replies with cooldown
- **Social interactions** — Self-introduction on joining a group, welcome messages for new members, daily morning greetings with deterministic jitter to prevent duplicates
- **API usage tracking** — Daily/monthly token usage and cost statistics
- **Multi-instance** — Run multiple independent assistants simultaneously, fully isolated with no shared state
- **Pinyin paths** — Chinese names auto-convert to pinyin slugs for filesystem compatibility
- **Test framework** — 5-level LLM capability test suite (basic → reasoning → coding → complex → project) with automated harness

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
├── config.json          # Runtime config
├── SOUL.md              # Persona definition ← edit this
├── MEMORY.md            # Long-term memory
├── HEARTBEAT.md         # Heartbeat task definitions
├── bot_identities.json  # Auto-inferred identities of other bots
├── groups.json          # Known group chat IDs (for morning greetings, etc.)
├── memory/              # Daily journals
├── sessions/            # Session persistence
├── groups/              # Group chat context
├── tools/               # Custom tool plugins
├── logs/                # Runtime logs
└── stats.jsonl          # API usage records
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

### Platform Abstraction

The codebase is split into a **platform-agnostic core** and **platform-specific adapters**:

```
platform/
├── types.py     — Standard data types (IncomingMessage, OutgoingMessage, Reaction, etc.)
└── adapter.py   — PlatformAdapter ABC (9 abstract + 4 optional methods)

feishu/adapter.py  — FeishuAdapter (wraps sender + listener internally)
conversation.py    — LocalAdapter (terminal mode)
```

The core (router, gateway, memory) only depends on `PlatformAdapter` and standard types — never on the Feishu SDK directly.

### Event Flow

```
Platform WS → adapter (internal conversion) → asyncio.Queue → router.handle(standard_event)
  standard_event = {"event_type": "message"|"reaction"|"interaction"|"member_change"|"eval_timeout", ...}
  ├── "message"       → IncomingMessage → _dispatch_message → _handle_private / _handle_group
  ├── "interaction"   → CardAction → _handle_card_action
  ├── "reaction"      → Reaction → _handle_reaction_event
  ├── "member_change" → _handle_member_change
  └── "eval_timeout"  → _evaluate_buffer
```

## Built-in Tools

The assistant has access to 21 built-in tools during conversations:

**Memory & Self-Management**

| Tool | Description |
|------|-------------|
| `write_memory` | Write information to MEMORY.md global long-term memory |
| `write_chat_memory` | Write per-chat memory specific to the current conversation |
| `read_self_file` | Read own config (SOUL.md / MEMORY.md / HEARTBEAT.md) |
| `write_self_file` | Modify own config |

**Calendar & Messaging**

| Tool | Description |
|------|-------------|
| `calendar_create_event` | Create a Feishu calendar event |
| `calendar_list_events` | Query calendar events |
| `send_card` | Send a Feishu card message |
| `send_message` | Send a text message to any chat |
| `schedule_message` | Schedule a message to be sent at a future time |

**Web & Information**

| Tool | Description |
|------|-------------|
| `web_search` | Search the internet for real-time information |
| `web_fetch` | Fetch and extract text content from a URL |

**Code & File Execution**

| Tool | Description |
|------|-------------|
| `run_python` | Execute Python code snippets for calculations and data processing |
| `run_bash` | Execute shell commands |
| `run_claude_code` | Delegate complex tasks to Claude Code subprocess |
| `read_file` | Read files from the filesystem |
| `write_file` | Write/create files on the filesystem |

**Custom Tool Management**

| Tool | Description |
|------|-------------|
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
    context includes: adapter, memory, calendar
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
├── gateway.py          # Main orchestrator (creates adapter, runs async tasks)
├── router.py           # Message routing + three-layer intervention + 21 built-in tools + multi-bot coordination
├── prompts.py          # Centralized prompts, tool descriptions, and constraint blocks
├── conversation.py     # Local interactive conversation (lq say) + LocalAdapter
├── tools.py            # Custom tool plugin system
├── buffer.py           # Group chat message buffer
├── session.py          # Per-chat session management + compaction
├── memory.py           # SOUL/MEMORY/per-chat memory/journals + self-awareness
├── heartbeat.py        # Periodic heartbeat
├── intent.py           # Post-processing intent detection
├── subagent.py         # Lightweight LLM parameter extraction
├── stats.py            # API usage statistics
├── templates.py        # Template generation
├── timeparse.py        # Time expression parsing
├── platform/
│   ├── types.py        # Platform-neutral data types (IncomingMessage, OutgoingMessage, etc.)
│   └── adapter.py      # PlatformAdapter ABC (abstract interface for all adapters)
├── executor/
│   ├── api.py          # Anthropic API (with retry + tool use)
│   └── claude_code.py  # Claude Code subprocess
└── feishu/
    ├── adapter.py      # FeishuAdapter (PlatformAdapter impl, wraps sender + listener)
    ├── listener.py     # WebSocket event receiver (internal to adapter)
    ├── sender.py       # REST API calls (internal to adapter)
    ├── calendar.py     # Calendar API
    └── cards.py        # Card builder

tests/
├── test_platform.py        # Platform abstraction unit tests (pytest)
├── harness.py              # Test harness (calls lq say, validates responses)
├── run_all.py              # Multi-level test runner
├── test_infrastructure.py  # Infrastructure & session tests
├── test_level1_basic.py    # Level 1: Basic tool calls
├── test_level2_reasoning.py # Level 2: Math & logic reasoning
├── test_level3_coding.py   # Level 3: Code generation & debugging
├── test_level4_complex.py  # Level 4: Web + agent loops
└── test_level5_project.py  # Level 5: Large-scale build & deploy
```
