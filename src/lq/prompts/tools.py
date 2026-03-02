"""Tool descriptions and field descriptions for LLM tool definitions."""

from __future__ import annotations


# =====================================================================
# Tool Descriptions  (sent to the LLM as tool definitions)
# =====================================================================

TOOL_DESC_WRITE_MEMORY = (
    "将重要信息写入 MEMORY.md 的指定分区实现长期记忆持久化。"
    "用于记住用户偏好、重要事实、待办事项等。"
    "内容按 section 分区组织，相同 section 会覆盖更新。"
)

TOOL_DESC_WRITE_CHAT_MEMORY = (
    "将信息写入当前聊天窗口的专属记忆（chat_memory）。"
    "与 write_memory（全局记忆）不同，chat_memory 只在当前聊天窗口中可见。"
    "用于记住与当前对话者相关的信息，如对方的偏好、聊天中的要点和约定等。"
    "全局通用的信息请用 write_memory，仅与当前对话相关的信息请用 write_chat_memory。"
)

TOOL_DESC_CALENDAR_CREATE = (
    "在飞书日历中创建一个新事件/日程。"
    "时间必须使用 ISO 8601 格式并包含时区偏移，例如 2026-02-13T15:00:00+08:00。"
    "请根据当前时间计算用户说的相对时间（如「5分钟后」「明天下午3点」）。"
)

TOOL_DESC_CALENDAR_LIST = "查询指定时间范围内的日历事件。用于查看日程安排、检查时间冲突等。"

TOOL_DESC_SEND_CARD = "发送一张信息卡片给用户。用于展示结构化信息如日程、任务列表等。"

TOOL_DESC_READ_SELF_FILE = (
    "读取自己的配置文件。可读文件: SOUL.md（人格定义）、MEMORY.md（长期记忆）、"
    "HEARTBEAT.md（心跳任务模板）、CURIOSITY.md（好奇心日志）、EVOLUTION.md（进化日志）。"
)

TOOL_DESC_WRITE_SELF_FILE = (
    "修改自己的配置文件。可写文件: SOUL.md（人格定义）、MEMORY.md（长期记忆）、"
    "HEARTBEAT.md（心跳任务模板）、CURIOSITY.md（好奇心日志）、EVOLUTION.md（进化日志）。"
    "修改 SOUL.md 会改变核心人格，请谨慎。建议先用 read_self_file 读取当前内容再修改。"
)

TOOL_DESC_CREATE_CUSTOM_TOOL = (
    "创建一个新的自定义工具。code 参数必须是完整的 Python 源代码，"
    "包含 TOOL_DEFINITION 字典（必须有 name, description, input_schema 三个 key）"
    "和 async def execute(input_data, context) 函数。"
    "context 是 dict，包含 sender、memory、calendar、http(httpx.AsyncClient) 四个 key。"
    "注意：TOOL_DEFINITION 中描述参数的 key 必须是 input_schema（不是 parameters）。"
)

TOOL_DESC_LIST_CUSTOM_TOOLS = "列出所有已安装的自定义工具及其状态。"

TOOL_DESC_TEST_CUSTOM_TOOL = "校验工具代码（语法、安全性），不实际创建。用于在创建前检查代码是否合规。"

TOOL_DESC_DELETE_CUSTOM_TOOL = "删除一个自定义工具。"

TOOL_DESC_TOGGLE_CUSTOM_TOOL = "启用或禁用一个自定义工具。"

TOOL_DESC_SEND_MESSAGE = (
    "主动发送消息到指定会话（chat_id）。支持纯文本或图片。"
    "发送图片时设置 image_path 为本地图片文件路径（如截图路径），text 可选作为图片说明。"
)

TOOL_DESC_SCHEDULE_MESSAGE = (
    "定时任务。到达指定时间后，你将被唤醒来执行 text 中描述的任务。"
    "你届时拥有全部工具（send_message、web_search、read_file 等）来完成任务。\n"
    "示例：\n"
    "- 简单提醒：text='提醒主人该开会了'\n"
    "- 检查任务：text='检查捏捏最近的 commit，有新进展就在群里夸她'\n"
    "- 定时问候：text='给主人发一条晚安消息'\n"
    "注意：text 是你未来要执行的指令，不是要原封不动发送的文本。"
)

SCHEDULED_ACTION_PROMPT = (
    "\n\n--- 定时任务触发 ---\n"
    "你之前设置的一个定时任务现在触发了。请执行以下任务：\n\n"
    "任务指令：{instruction}\n"
    "目标会话：{chat_id}\n\n"
    "规则：\n"
    "- 使用工具完成任务（send_message、web_search、read_file 等均可用）\n"
    "- 如果需要发消息给用户，用 send_message 发到目标会话\n"
    "- 不要把任务指令原封不动发给用户——真正执行任务，发送有意义的结果\n"
    "- 如果是简单提醒，生成一条自然的提醒消息即可\n"
)

TOOL_DESC_RUN_CLAUDE_CODE = (
    "调用 Claude Code CLI 执行复杂任务。适用于：代码编写/修改、项目分析、git 操作、"
    "文件处理、多步骤推理任务等。Claude Code 会在工作区目录下执行，拥有完整的编程能力。"
    "prompt 参数是你要 Claude Code 完成的具体任务描述。"
)

TOOL_DESC_RUN_BASH = (
    "执行 shell/bash 命令。适用于：查看文件内容（cat/ls）、运行脚本、管理进程（ps/kill）、"
    "安装软件包（pip/npm/apt）、git 操作、查看系统状态等简单命令行操作。"
    "复杂的多步骤任务请使用 run_claude_code。"
)

TOOL_DESC_WEB_SEARCH = (
    "搜索互联网获取实时信息（基于智谱 WebSearch）。"
    "用于查询新闻、天气、汇率、百科知识、技术文档等任何需要联网查询的内容。"
    "返回搜索结果列表，包含标题、链接和摘要。如需详细内容，可配合 web_fetch 获取完整网页。"
)

TOOL_DESC_WEB_FETCH = (
    "抓取指定 URL 的网页内容并提取纯文本。用于读取文章、文档、API 响应等网页内容。"
    "自动处理 HTML 转文本，返回清洁的可读内容。支持任意公开可访问的 URL。"
)

TOOL_DESC_RUN_PYTHON = (
    "执行 Python 代码片段。适用于：数据计算、文本处理、JSON 解析、数学运算、"
    "日期计算、字符串操作等轻量级编程任务。代码在独立子进程中执行，可使用标准库。"
    "复杂的多步骤编程任务请使用 run_claude_code。"
)

TOOL_DESC_READ_FILE = (
    "读取文件系统中的任意文件内容。支持文本文件、配置文件、代码文件、日志等。"
    "可指定最大行数限制，避免读取超大文件时占用过多内存。"
)

TOOL_DESC_WRITE_FILE = (
    "将内容写入文件系统中的任意路径。可用于创建新文件或覆盖已有文件。"
    "自动创建不存在的父目录。适用于保存数据、生成配置、输出报告等场景。"
)

# Tool input field descriptions
TOOL_FIELD_SECTION = "记忆分区名（如：重要信息、用户偏好、备忘、待办事项）"
TOOL_FIELD_CONTENT_MEMORY = "要记住的内容，支持 Markdown 格式，建议用列表组织多条信息"
TOOL_FIELD_CHAT_SECTION = "记忆分区名（如：关于对方、聊天要点、约定事项）"
TOOL_FIELD_CHAT_CONTENT = "要记录的内容，支持 Markdown 格式"
TOOL_FIELD_SUMMARY = "事件标题"
TOOL_FIELD_START_TIME = "开始时间，必须为 ISO 8601 格式且包含时区，如 2026-02-13T15:00:00+08:00"
TOOL_FIELD_END_TIME = (
    "结束时间，必须为 ISO 8601 格式且包含时区，如 2026-02-13T16:00:00+08:00。"
    "若用户未指定结束时间，默认为开始时间后1小时"
)
TOOL_FIELD_EVENT_DESC = "事件描述（可选）"
TOOL_FIELD_QUERY_START = "查询开始时间，ISO 8601 格式且包含时区，如 2026-02-13T00:00:00+08:00"
TOOL_FIELD_QUERY_END = "查询结束时间，ISO 8601 格式且包含时区，如 2026-02-13T23:59:59+08:00"
TOOL_FIELD_CARD_TITLE = "卡片标题"
TOOL_FIELD_CARD_CONTENT = "卡片内容（支持 Markdown）"
TOOL_FIELD_CARD_COLOR = "卡片颜色主题"
TOOL_FIELD_FILENAME_READ = "要读取的文件名"
TOOL_FIELD_FILENAME_WRITE = "要写入的文件名"
TOOL_FIELD_FILE_CONTENT = "文件的完整新内容"
TOOL_FIELD_TOOL_NAME = "工具名称（字母、数字、下划线）"
TOOL_FIELD_TOOL_CODE = (
    "完整 Python 源代码。TOOL_DEFINITION 必须包含 input_schema（非 parameters）描述工具参数。"
    "execute(input_data, context) 中 context['http'] 是 httpx.AsyncClient"
)
TOOL_FIELD_VALIDATE_CODE = "要校验的 Python 源代码"
TOOL_FIELD_DELETE_NAME = "要删除的工具名称"
TOOL_FIELD_TOGGLE_NAME = "工具名称"
TOOL_FIELD_TOGGLE_ENABLED = "true=启用, false=禁用"
TOOL_FIELD_CHAT_ID = "目标会话 ID（用户私聊或群聊的 chat_id）"
TOOL_FIELD_TEXT = "要发送的文本内容（发送图片时可选，作为图片说明）"
TOOL_FIELD_IMAGE_PATH = "本地图片文件路径（可选，设置后发送图片消息）"
TOOL_FIELD_SCHEDULE_TEXT = "定时任务的指令——描述到时间后你要做什么（不会原文发送，而是由你执行）"
TOOL_FIELD_SEND_AT = "计划发送时间，ISO 8601 格式且包含时区，如 2026-02-13T15:05:00+08:00"
TOOL_FIELD_CC_PROMPT = "要执行的任务描述，尽量详细具体。Claude Code 会自主完成这个任务。"
TOOL_FIELD_WORKING_DIR = "工作目录路径（可选，默认为工作区目录）"
TOOL_FIELD_CC_TIMEOUT = "超时时间（秒），默认 300"
TOOL_FIELD_CC_RESUME_SESSION = "CC session ID，传入可恢复之前的会话继续执行（可选）"
TOOL_FIELD_CC_MAX_BUDGET = "本次执行的成本上限（美元），默认 0.5"
TOOL_FIELD_BASH_COMMAND = "要执行的 shell 命令"
TOOL_FIELD_BASH_TIMEOUT = "超时时间（秒），默认 60"
TOOL_FIELD_SEARCH_QUERY = "搜索关键词，支持中英文，建议不超过 70 字"
TOOL_FIELD_SEARCH_MAX_RESULTS = "返回结果的最大数量，默认 5"
TOOL_FIELD_FETCH_URL = "要抓取的完整 URL（含 http:// 或 https://）"
TOOL_FIELD_FETCH_MAX_LENGTH = "返回文本的最大字符数，默认 8000"
TOOL_FIELD_PYTHON_CODE = "要执行的 Python 代码（支持多行）"
TOOL_FIELD_PYTHON_TIMEOUT = "超时时间（秒），默认 30"
TOOL_FIELD_FILE_PATH = "文件的绝对路径或相对于工作区的路径"
TOOL_FIELD_FILE_MAX_LINES = "最大读取行数，默认 500"
TOOL_FIELD_WRITE_PATH = "目标文件的绝对路径或相对于工作区的路径"
TOOL_FIELD_WRITE_CONTENT = "要写入的文件内容"

# Browser Action 工具
TOOL_DESC_BROWSER_ACTION = (
    "操控浏览器浏览网页、点击、输入、截图等。通过 CDP 连接本地 Chromium（localhost:9222）。"
    "适用于需要真实浏览器交互的场景：浏览小红书等动态网站、填写表单、截图页面等。"
    "使用前需确保浏览器已启动（可用 run_bash 启动，端口见 config.json 的 browser_port，默认 9222）。\n"
    "截图工作流：screenshot 保存截图到工作区 → 用 vision_analyze 查看内容 → "
    "用 send_message（设置 image_path）将截图发送给用户，或用文字描述。\n"
    "Cookie 持久化：首次登录后用 save_cookies 保存登录态到工作区，"
    "浏览器重启后用 load_cookies 恢复，无需重新登录。"
)

TOOL_FIELD_BROWSER_ACTION = (
    "操作类型：navigate（打开URL）、get_content（获取页面文本）、screenshot（截图）、"
    "click（点击元素）、type（输入文字）、evaluate（执行JS）、get_elements（查询元素列表）、"
    "scroll（滚动页面）、wait（等待元素出现）、status（检查浏览器连接状态）、"
    "save_cookies（保存当前浏览器 cookies 到文件）、load_cookies（从文件加载 cookies 到浏览器）"
)
TOOL_FIELD_BROWSER_COOKIE_PATH = "Cookie JSON 文件路径（可选，默认保存到工作区 browser_cookies.json）"
TOOL_FIELD_BROWSER_URL = "要打开的完整 URL"
TOOL_FIELD_BROWSER_SELECTOR = "CSS 选择器，用于定位页面元素（如 'body', '#main', '.post-item'）"
TOOL_FIELD_BROWSER_TEXT = "要输入的文字内容"
TOOL_FIELD_BROWSER_SCRIPT = "要执行的 JavaScript 代码"
TOOL_FIELD_BROWSER_PATH = "截图保存路径（可选，默认保存到工作区目录）"
TOOL_FIELD_BROWSER_MAX_COUNT = "最大返回元素数量（默认 20）"
TOOL_FIELD_BROWSER_DIRECTION = "滚动方向：up 或 down"
TOOL_FIELD_BROWSER_AMOUNT = "滚动像素数（默认 500）"
TOOL_FIELD_BROWSER_TIMEOUT = "等待超时秒数（默认 10）"

# Vision MCP 工具
TOOL_DESC_VISION_ANALYZE = (
    "分析图片内容（基于 zai-mcp-server Vision）。"
    "可以理解图片中的场景、物体、文字、UI 设计、图表数据、技术架构图等。"
    "支持本地文件路径或远程 URL。需要搭配 prompt 描述你想从图片中获取什么信息。"
)
TOOL_FIELD_VISION_IMAGE = "图片来源：本地文件路径或远程 URL"
TOOL_FIELD_VISION_PROMPT = "分析指令：描述你想从图片中了解或提取什么信息"


# =====================================================================
# get_my_stats / detect_drift / list_chat_members Tool Descriptions
# =====================================================================

TOOL_DESC_GET_MY_STATS = "查看自己的运行状态和统计信息"

TOOL_DESC_DETECT_DRIFT = (
    "检测最近回复中的行为漂移（是否违反 SOUL.md 规范）。"
    "扫描最近 N 天的 session 记录，检查 assistant 回复是否存在暴露工具名、"
    "自称 AI、使用 emoji、时间戳前缀等违规行为。适合在心跳自省时使用。"
)

TOOL_FIELD_DRIFT_DAYS = "检查最近 N 天的回复（1-7），默认 1"

TOOL_FIELD_STATS_CATEGORY = (
    "要查看的统计类别：today（今日统计）、month（本月统计）、capability（工具使用统计）"
)

TOOL_DESC_LIST_CHAT_MEMBERS = "查询当前群聊的成员列表（包含每人的 user_id 和显示名）。当你需要知道群里有谁、或需要获取某人的 user_id 时使用。"
