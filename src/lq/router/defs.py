"""LLM 可调用的工具定义（JSON Schema 格式）"""

from __future__ import annotations

from lq.prompts import (
    TOOL_DESC_WRITE_MEMORY, TOOL_DESC_WRITE_CHAT_MEMORY,
    TOOL_DESC_CALENDAR_CREATE, TOOL_DESC_CALENDAR_LIST,
    TOOL_DESC_SEND_CARD, TOOL_DESC_READ_SELF_FILE, TOOL_DESC_WRITE_SELF_FILE,
    TOOL_DESC_CREATE_CUSTOM_TOOL, TOOL_DESC_LIST_CUSTOM_TOOLS,
    TOOL_DESC_TEST_CUSTOM_TOOL, TOOL_DESC_DELETE_CUSTOM_TOOL,
    TOOL_DESC_TOGGLE_CUSTOM_TOOL, TOOL_DESC_SEND_MESSAGE,
    TOOL_DESC_SCHEDULE_MESSAGE, TOOL_DESC_RUN_CLAUDE_CODE, TOOL_DESC_RUN_BASH,
    TOOL_DESC_WEB_SEARCH, TOOL_DESC_WEB_FETCH,
    TOOL_DESC_RUN_PYTHON, TOOL_DESC_READ_FILE, TOOL_DESC_WRITE_FILE,
    TOOL_DESC_GET_MY_STATS, TOOL_FIELD_STATS_CATEGORY,
    TOOL_FIELD_SECTION, TOOL_FIELD_CONTENT_MEMORY,
    TOOL_FIELD_CHAT_SECTION, TOOL_FIELD_CHAT_CONTENT,
    TOOL_FIELD_SUMMARY, TOOL_FIELD_START_TIME, TOOL_FIELD_END_TIME,
    TOOL_FIELD_EVENT_DESC, TOOL_FIELD_QUERY_START, TOOL_FIELD_QUERY_END,
    TOOL_FIELD_CARD_TITLE, TOOL_FIELD_CARD_CONTENT, TOOL_FIELD_CARD_COLOR,
    TOOL_FIELD_FILENAME_READ, TOOL_FIELD_FILENAME_WRITE, TOOL_FIELD_FILE_CONTENT,
    TOOL_FIELD_TOOL_NAME, TOOL_FIELD_TOOL_CODE,
    TOOL_FIELD_VALIDATE_CODE, TOOL_FIELD_DELETE_NAME,
    TOOL_FIELD_TOGGLE_NAME, TOOL_FIELD_TOGGLE_ENABLED,
    TOOL_FIELD_CHAT_ID, TOOL_FIELD_TEXT, TOOL_FIELD_SCHEDULE_TEXT, TOOL_FIELD_SEND_AT,
    TOOL_FIELD_CC_PROMPT, TOOL_FIELD_WORKING_DIR, TOOL_FIELD_CC_TIMEOUT,
    TOOL_FIELD_BASH_COMMAND, TOOL_FIELD_BASH_TIMEOUT,
    TOOL_FIELD_SEARCH_QUERY, TOOL_FIELD_SEARCH_MAX_RESULTS,
    TOOL_FIELD_FETCH_URL, TOOL_FIELD_FETCH_MAX_LENGTH,
    TOOL_FIELD_PYTHON_CODE, TOOL_FIELD_PYTHON_TIMEOUT,
    TOOL_FIELD_FILE_PATH, TOOL_FIELD_FILE_MAX_LINES,
    TOOL_FIELD_WRITE_PATH, TOOL_FIELD_WRITE_CONTENT,
)

TOOLS: list[dict] = [
    {
        "name": "write_memory",
        "description": TOOL_DESC_WRITE_MEMORY,
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": TOOL_FIELD_SECTION,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_CONTENT_MEMORY,
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "write_chat_memory",
        "description": TOOL_DESC_WRITE_CHAT_MEMORY,
        "input_schema": {
            "type": "object",
            "properties": {
                "section": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_SECTION,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_CONTENT,
                },
            },
            "required": ["section", "content"],
        },
    },
    {
        "name": "calendar_create_event",
        "description": TOOL_DESC_CALENDAR_CREATE,
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": TOOL_FIELD_SUMMARY},
                "start_time": {
                    "type": "string",
                    "description": TOOL_FIELD_START_TIME,
                },
                "end_time": {
                    "type": "string",
                    "description": TOOL_FIELD_END_TIME,
                },
                "description": {
                    "type": "string",
                    "description": TOOL_FIELD_EVENT_DESC,
                    "default": "",
                },
            },
            "required": ["summary", "start_time", "end_time"],
        },
    },
    {
        "name": "calendar_list_events",
        "description": TOOL_DESC_CALENDAR_LIST,
        "input_schema": {
            "type": "object",
            "properties": {
                "start_time": {
                    "type": "string",
                    "description": TOOL_FIELD_QUERY_START,
                },
                "end_time": {
                    "type": "string",
                    "description": TOOL_FIELD_QUERY_END,
                },
            },
            "required": ["start_time", "end_time"],
        },
    },
    {
        "name": "send_card",
        "description": TOOL_DESC_SEND_CARD,
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": TOOL_FIELD_CARD_TITLE},
                "content": {"type": "string", "description": TOOL_FIELD_CARD_CONTENT},
                "color": {
                    "type": "string",
                    "description": TOOL_FIELD_CARD_COLOR,
                    "enum": ["blue", "green", "orange", "red", "purple"],
                    "default": "blue",
                },
            },
            "required": ["title", "content"],
        },
    },
    {
        "name": "read_self_file",
        "description": TOOL_DESC_READ_SELF_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": TOOL_FIELD_FILENAME_READ,
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md", "CURIOSITY.md", "EVOLUTION.md"],
                },
            },
            "required": ["filename"],
        },
    },
    {
        "name": "write_self_file",
        "description": TOOL_DESC_WRITE_SELF_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "filename": {
                    "type": "string",
                    "description": TOOL_FIELD_FILENAME_WRITE,
                    "enum": ["SOUL.md", "MEMORY.md", "HEARTBEAT.md", "CURIOSITY.md", "EVOLUTION.md"],
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_FILE_CONTENT,
                },
            },
            "required": ["filename", "content"],
        },
    },
    {
        "name": "create_custom_tool",
        "description": TOOL_DESC_CREATE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_TOOL_NAME},
                "code": {
                    "type": "string",
                    "description": TOOL_FIELD_TOOL_CODE,
                },
            },
            "required": ["name", "code"],
        },
    },
    {
        "name": "list_custom_tools",
        "description": TOOL_DESC_LIST_CUSTOM_TOOLS,
        "input_schema": {
            "type": "object",
            "properties": {},
        },
    },
    {
        "name": "test_custom_tool",
        "description": TOOL_DESC_TEST_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": TOOL_FIELD_VALIDATE_CODE},
            },
            "required": ["code"],
        },
    },
    {
        "name": "delete_custom_tool",
        "description": TOOL_DESC_DELETE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_DELETE_NAME},
            },
            "required": ["name"],
        },
    },
    {
        "name": "toggle_custom_tool",
        "description": TOOL_DESC_TOGGLE_CUSTOM_TOOL,
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": TOOL_FIELD_TOGGLE_NAME},
                "enabled": {"type": "boolean", "description": TOOL_FIELD_TOGGLE_ENABLED},
            },
            "required": ["name", "enabled"],
        },
    },
    {
        "name": "send_message",
        "description": TOOL_DESC_SEND_MESSAGE,
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_ID,
                },
                "text": {
                    "type": "string",
                    "description": TOOL_FIELD_TEXT,
                },
            },
            "required": ["chat_id", "text"],
        },
    },
    {
        "name": "schedule_message",
        "description": TOOL_DESC_SCHEDULE_MESSAGE,
        "input_schema": {
            "type": "object",
            "properties": {
                "chat_id": {
                    "type": "string",
                    "description": TOOL_FIELD_CHAT_ID,
                },
                "text": {
                    "type": "string",
                    "description": TOOL_FIELD_SCHEDULE_TEXT,
                },
                "send_at": {
                    "type": "string",
                    "description": TOOL_FIELD_SEND_AT,
                },
            },
            "required": ["chat_id", "text", "send_at"],
        },
    },
    {
        "name": "run_claude_code",
        "description": TOOL_DESC_RUN_CLAUDE_CODE,
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": TOOL_FIELD_CC_PROMPT,
                },
                "working_dir": {
                    "type": "string",
                    "description": TOOL_FIELD_WORKING_DIR,
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_CC_TIMEOUT,
                    "default": 300,
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "run_bash",
        "description": TOOL_DESC_RUN_BASH,
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": TOOL_FIELD_BASH_COMMAND,
                },
                "working_dir": {
                    "type": "string",
                    "description": TOOL_FIELD_WORKING_DIR,
                    "default": "",
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_BASH_TIMEOUT,
                    "default": 60,
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "web_search",
        "description": TOOL_DESC_WEB_SEARCH,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": TOOL_FIELD_SEARCH_QUERY,
                },
                "max_results": {
                    "type": "integer",
                    "description": TOOL_FIELD_SEARCH_MAX_RESULTS,
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "web_fetch",
        "description": TOOL_DESC_WEB_FETCH,
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": TOOL_FIELD_FETCH_URL,
                },
                "max_length": {
                    "type": "integer",
                    "description": TOOL_FIELD_FETCH_MAX_LENGTH,
                    "default": 8000,
                },
            },
            "required": ["url"],
        },
    },
    {
        "name": "run_python",
        "description": TOOL_DESC_RUN_PYTHON,
        "input_schema": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": TOOL_FIELD_PYTHON_CODE,
                },
                "timeout": {
                    "type": "integer",
                    "description": TOOL_FIELD_PYTHON_TIMEOUT,
                    "default": 30,
                },
            },
            "required": ["code"],
        },
    },
    {
        "name": "read_file",
        "description": TOOL_DESC_READ_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": TOOL_FIELD_FILE_PATH,
                },
                "max_lines": {
                    "type": "integer",
                    "description": TOOL_FIELD_FILE_MAX_LINES,
                    "default": 500,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": TOOL_DESC_WRITE_FILE,
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": TOOL_FIELD_WRITE_PATH,
                },
                "content": {
                    "type": "string",
                    "description": TOOL_FIELD_WRITE_CONTENT,
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "get_my_stats",
        "description": TOOL_DESC_GET_MY_STATS,
        "input_schema": {
            "type": "object",
            "properties": {
                "category": {
                    "type": "string",
                    "description": TOOL_FIELD_STATS_CATEGORY,
                    "enum": ["today", "month", "capability"],
                    "default": "today",
                },
            },
        },
    },
]
