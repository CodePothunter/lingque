# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LingQue (灵雀) is a personal AI assistant framework with a platform-agnostic core and pluggable chat platform adapters. It runs as a long-lived daemon that connects to chat platforms (currently Feishu/Lark) via a `PlatformAdapter` interface, handles private/group chat messages through an LLM, and supports calendar management, long-term memory, and runtime tool creation.

## Commands

```bash
uv sync                                    # Install dependencies
uv run lq init --name NAME --from-env .env # Initialize an instance
uv run lq start @NAME                      # Start (foreground, default Feishu)
uv run lq start @NAME --adapter local     # Start in local-only mode (no Feishu)
uv run lq start @NAME --adapter feishu,local  # Multi-platform mode
uv run lq stop @NAME                       # Stop
uv run lq logs @NAME                       # Tail logs
uv run lq status @NAME                     # Show status + API usage
uv run lq list                             # List all instances
uv run lq chat @NAME                       # Interactive local chat (no Feishu)
uv run lq chat @NAME "你好"                # Single-message mode
uv run lq edit @NAME soul                  # Edit SOUL.md persona
```

Instance names accept both Chinese and pinyin: `@奶油` and `@naiyou` are equivalent.

No automated test suite exists. No linter or formatter is configured.

## Architecture

### Platform Abstraction

The codebase is split into a platform-agnostic core and platform-specific adapters:

- **`platform/`** — Abstract layer defining the adapter interface and standard data types:
  - `adapter.py`: `PlatformAdapter` ABC — 8 abstract methods (`get_identity`, `connect`, `disconnect`, `send`, `start_thinking`, `stop_thinking`, `fetch_media`, `resolve_name`, `list_members`) + 4 optional (`react`, `unreact`, `edit`, `unsend`)
  - `types.py`: Platform-neutral dataclasses — `IncomingMessage`, `OutgoingMessage`, `BotIdentity`, `ChatMember`, `Reaction`, `CardAction`, plus enums `ChatType`, `SenderType`, `MessageType`
  - `multi.py`: `MultiAdapter` — composite adapter that wraps multiple adapters for multi-platform mode; routes outgoing messages back to the originating adapter based on chat_id tracking
- **`feishu/adapter.py`** — `FeishuAdapter` implementing `PlatformAdapter`. Wraps `FeishuSender` + `FeishuListener` internally; handles event conversion, @mention resolution, bot message polling, and standard card → Feishu card conversion.
- **`conversation.py`** — `LocalAdapter` implementing `PlatformAdapter` for terminal-based local chat mode. Two modes: **gateway mode** (`home` set) starts `_read_stdin()` and `_watch_inbox()` event sources that push to queue; **chat mode** (`home=None`) uses passive connect with direct `router.handle()` calls.

The core (router, gateway, memory) only depends on `PlatformAdapter` and standard types — never on Feishu SDK directly.

### Event Flow

All adapters produce standard events through the same unified path:

```
Event sources (per adapter):
  FeishuAdapter:  Feishu WS → _event_converter → queue.put()
  LocalAdapter:   stdin → _read_stdin → queue.put()  |  inbox.txt → _watch_inbox → queue.put()

Unified pipeline:
  asyncio.Queue → _consume_messages → router.handle(standard_event)
    standard_event = {"event_type": "message"|"reaction"|"interaction"|"member_change"|"eval_timeout", ...}
    ├── "message" → IncomingMessage → _dispatch_message → _handle_private / _handle_group
    ├── "interaction" → CardAction → _handle_card_action
    ├── "reaction" → Reaction → _handle_reaction_event
    ├── "member_change" → _handle_member_change
    └── "eval_timeout" → _evaluate_buffer
```

### Key Modules (`src/lq/`)

- **gateway.py** — Orchestrator. Creates the adapter, initializes all components, runs concurrent async tasks (consumer, heartbeat, inbox, autosave), handles signals for graceful shutdown.
- **router.py** — Core routing logic (largest module). Receives standard events via `handle()`, dispatches to typed handlers. Runs the agentic tool-use loop, defines all 11 built-in tools. Three-layer group chat intervention: trivial message filter → message buffering → LLM evaluation. Depends on `PlatformAdapter` (not Feishu SDK).
- **memory.py** — Builds LLM system prompt from SOUL.md (persona), MEMORY.md (long-term memory), current time (CST/UTC+8), and self-awareness capabilities list.
- **session.py** — Per-chat-id message history with auto-compaction at 50 messages (summarize + keep last 10).
- **tools.py** — Runtime custom tool plugin system. Loads `.py` files from `tools/` directory, validates via AST (blocks dangerous modules: os, subprocess, shutil, sys, socket, ctypes, signal, multiprocessing, threading).
- **executor/api.py** — Anthropic SDK wrapper with exponential backoff retry (status 429, 500, 502, 503, 529), `<think>` tag cleanup, model pricing table for cost tracking.
- **executor/claude_code.py** — Claude Code subprocess executor (alternative to direct API).
- **intent.py + subagent.py** — Post-processing pipeline: detects missed tool calls in LLM responses, uses lightweight LLM call to extract parameters, then executes the tool.
- **buffer.py** — Group chat message accumulator with threshold/timeout triggers.
- **feishu/** — Feishu integration (internal to FeishuAdapter): `sender.py` (REST API calls), `listener.py` (WebSocket events), `calendar.py` (event CRUD), `cards.py` (card builder).

### Instance Workspace

Each assistant instance stores its state at `~/.lq-{slug}/` (slug = pinyin of Chinese name). Key files: `config.json`, `SOUL.md`, `MEMORY.md`, `HEARTBEAT.md`, plus directories for `memory/`, `sessions/`, `groups/`, `tools/`, `logs/`, `stats.jsonl`.

## Code Conventions

- Python 3.11+ required. Uses `from __future__ import annotations` throughout.
- Full type hints on all function signatures.
- Consistent `logger = logging.getLogger(__name__)` per module.
- Async-first: all I/O-bound operations use `async def` / `await`.
- Dataclasses for config objects (`APIConfig`, `FeishuConfig`, `GroupConfig`, `LQConfig`).
- Error handling: try-except with `logger.exception()` for graceful degradation — the assistant should keep running even if individual features fail.
- Timezone: hardcoded CST (UTC+8) via `timezone(timedelta(hours=8))`.
- Commit messages use emoji prefix + Chinese category format (e.g., `✨【功能】：description`).

## Dependencies

| Package | Purpose |
|---------|---------|
| `click` | CLI framework |
| `lark-oapi` | Feishu SDK (WebSocket, REST API) |
| `anthropic` | LLM API client |
| `python-dotenv` | `.env` file loading |
| `httpx[socks]` | Async HTTP with proxy support |
| `pypinyin` | Chinese → pinyin conversion for slugs |
