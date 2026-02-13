# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

LingQue (灵雀) is a personal AI assistant framework deeply integrated with Feishu (Lark). It runs as a long-lived daemon that connects to Feishu via WebSocket, handles private/group chat messages through an LLM, and supports calendar management, long-term memory, and runtime tool creation.

## Commands

```bash
uv sync                                    # Install dependencies
uv run lq init --name NAME --from-env .env # Initialize an instance
uv run lq start @NAME                      # Start (foreground)
uv run lq stop @NAME                       # Stop
uv run lq logs @NAME                       # Tail logs
uv run lq status @NAME                     # Show status + API usage
uv run lq list                             # List all instances
uv run lq edit @NAME soul                  # Edit SOUL.md persona
```

Instance names accept both Chinese and pinyin: `@奶油` and `@naiyou` are equivalent.

No automated test suite exists. No linter or formatter is configured.

## Architecture

### Dual-Thread Model

- **Main thread**: `asyncio.run(gateway.run())` — runs message consumer, heartbeat scheduler, and inbox poller as concurrent async tasks
- **Feishu daemon thread**: Blocking WebSocket client (`listener.start_blocking()`), bridges events to main loop via `loop.call_soon_threadsafe(queue.put_nowait, data)`

### Message Flow

```
Feishu WS → listener (daemon thread) → asyncio.Queue → router.handle()
  ├── Private DM → debounce → session → executor.reply_with_tool_loop() → sender
  └── Group chat → trivial filter → buffer → LLM evaluation → maybe reply
```

### Key Modules (`src/lq/`)

- **gateway.py** — Orchestrator. Initializes all components, manages dual-thread lifecycle, handles signals for graceful shutdown.
- **router.py** — Core routing logic (~1000 lines, largest module). Dispatches by message type (private/group/card), runs the agentic tool-use loop, defines all 11 built-in tools. Three-layer group chat intervention: trivial message filter → message buffering → LLM evaluation.
- **memory.py** — Builds LLM system prompt from SOUL.md (persona), MEMORY.md (long-term memory), current time (CST/UTC+8), and self-awareness capabilities list.
- **session.py** — Per-chat-id message history with auto-compaction at 50 messages (summarize + keep last 10).
- **tools.py** — Runtime custom tool plugin system. Loads `.py` files from `tools/` directory, validates via AST (blocks dangerous modules: os, subprocess, shutil, sys, socket, ctypes, signal, multiprocessing, threading).
- **executor/api.py** — Anthropic SDK wrapper with exponential backoff retry (status 429, 500, 502, 503, 529), `<think>` tag cleanup, model pricing table for cost tracking.
- **executor/claude_code.py** — Claude Code subprocess executor (alternative to direct API).
- **intent.py + subagent.py** — Post-processing pipeline: detects missed tool calls in LLM responses, uses lightweight LLM call to extract parameters, then executes the tool.
- **buffer.py** — Group chat message accumulator with threshold/timeout triggers.
- **feishu/** — Feishu integration: `listener.py` (WebSocket events), `sender.py` (text/card messages, Markdown cleanup), `calendar.py` (event CRUD), `cards.py` (card builder).

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
