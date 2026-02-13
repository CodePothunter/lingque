"""记忆管理"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)

    def read_soul(self) -> str:
        soul_path = self.workspace / "SOUL.md"
        if soul_path.exists():
            return soul_path.read_text(encoding="utf-8")
        return ""

    def read_memory(self) -> str:
        mem_path = self.workspace / "MEMORY.md"
        if mem_path.exists():
            return mem_path.read_text(encoding="utf-8")
        return ""

    def build_context(self, chat_id: str = "") -> str:
        """拼接 SOUL.md + MEMORY.md + 当前聊天窗口的日志 + 自我认知

        Args:
            chat_id: 当前聊天窗口 ID。传入后仅注入该窗口的日志条目。
        """
        parts = []

        # 注入当前准确时间（东八区）
        cst = timezone(timedelta(hours=8))
        now = datetime.now(cst)
        parts.append(f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S')} (CST, UTC+8)")

        soul = self.read_soul()
        if soul:
            parts.append(f"<soul>\n{soul}\n</soul>")

        memory = self.read_memory()
        if memory:
            parts.append(f"<memory>\n{memory}\n</memory>")

        # 按 chat_id 过滤日志，避免跨聊天窗口泄露
        if chat_id:
            today = date.today()
            for d in [today - timedelta(days=1), today]:
                log = self._read_daily_for_chat(d, chat_id)
                if log:
                    parts.append(f"<daily_log date=\"{d.isoformat()}\">\n{log}\n</daily_log>")

        parts.append(self._build_self_awareness())

        return "\n\n".join(parts)

    def _build_self_awareness(self) -> str:
        """构建自我认知上下文，让助理了解自己的架构和可修改的文件"""
        ws = self.workspace
        # 列出工作区中实际存在的文件
        editable_files = []
        for name in ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"]:
            p = ws / name
            if p.exists():
                editable_files.append(f"  - {name} ({p.stat().st_size} 字节)")
            else:
                editable_files.append(f"  - {name} (不存在，可创建)")

        daily_logs = sorted(self.memory_dir.glob("*.md"), reverse=True)[:5]
        if daily_logs:
            log_list = "\n".join(f"  - memory/{f.name}" for f in daily_logs)
        else:
            log_list = "  (暂无日志)"

        return (
            "<self_awareness>\n"
            "## 关于你自己\n"
            "你是由「灵雀 LingQue」框架驱动的 AI 助理，运行在飞书平台上。\n\n"
            "### 你的工作区\n"
            f"路径: {ws}\n"
            "可编辑的配置文件:\n"
            f"{chr(10).join(editable_files)}\n"
            f"最近的日志:\n{log_list}\n\n"
            "### 文件说明\n"
            "- **SOUL.md**: 定义你的身份、性格、沟通风格和介入原则。修改它会改变你的行为方式。\n"
            "- **MEMORY.md**: 长期记忆存储，按分区组织。你已有 write_memory 工具来更新它。\n"
            "- **HEARTBEAT.md**: 定义你的定时任务和主动行为模板。\n\n"
            "### 你的能力（均有对应工具可调用）\n"
            "- 使用 send_message 工具主动给任何用户或群聊发消息\n"
            "- 使用 schedule_message 工具定时发送消息（如「5分钟后提醒我」）\n"
            "- 使用 calendar_create_event / calendar_list_events 工具创建和查询日历事件\n"
            "- 使用 read_self_file / write_self_file 工具读写配置文件（SOUL.md、MEMORY.md、HEARTBEAT.md）\n"
            "- 使用 write_memory 工具将重要信息写入长期记忆\n"
            "- 使用 create_custom_tool 工具创建新的自定义工具来扩展自身能力\n"
            "- 使用 send_card 工具发送结构化卡片消息\n\n"
            "### 自主能力扩展（重要）\n"
            "当用户提出需求而你现有工具无法满足时，你应该**主动创建新工具**来获得这个能力。\n"
            "例如：用户要查天气 → 创建天气查询工具；用户要搜索网页 → 创建网络搜索工具；\n"
            "用户要查汇率 → 创建汇率工具。创建工具后立即使用它完成用户请求。\n"
            "不要说「我没有这个功能」——你可以给自己创造功能。这是你的核心能力之一。\n"
            "工具代码中可以使用 context['http']（httpx.AsyncClient）发起网络请求。\n\n"
            "### 自我修改\n"
            "你可以使用 read_self_file 和 write_self_file 工具来查看和修改上述配置文件。\n"
            "修改 SOUL.md 会改变你的核心人格，请谨慎操作，建议先读取当前内容再修改。\n\n"
            f"{self._build_custom_tools_awareness()}"
            "</self_awareness>"
        )

    def _build_custom_tools_awareness(self) -> str:
        """构建自定义工具的自我认知段落。"""
        tools_dir = self.workspace / "tools"
        if not tools_dir.exists():
            return (
                "### 自定义工具\n"
                "你可以使用 create_custom_tool 创建新的自定义工具来扩展自己的能力。\n"
                "目前没有已安装的自定义工具。\n"
            )

        tool_files = sorted(f for f in tools_dir.glob("*.py") if not f.name.startswith("_"))
        if not tool_files:
            return (
                "### 自定义工具\n"
                "你可以使用 create_custom_tool 创建新的自定义工具来扩展自己的能力。\n"
                "目前没有已安装的自定义工具。\n"
            )

        # 读取禁用列表
        import json as _json
        disabled: set[str] = set()
        registry_path = tools_dir / "__registry__.json"
        if registry_path.exists():
            try:
                data = _json.loads(registry_path.read_text(encoding="utf-8"))
                disabled = set(data.get("disabled", []))
            except Exception:
                pass

        lines = []
        for f in tool_files:
            name = f.stem
            status = "禁用" if name in disabled else "启用"
            # 尝试读取文件首行 docstring 作为描述
            desc = ""
            try:
                first_lines = f.read_text(encoding="utf-8").split("\n", 3)
                for line in first_lines:
                    stripped = line.strip().strip('"').strip("'")
                    if stripped and not stripped.startswith("#") and not stripped.startswith("import"):
                        desc = f" - {stripped}"
                        break
            except Exception:
                pass
            lines.append(f"  - {name} ({status}){desc}")

        tool_list = "\n".join(lines)
        return (
            "### 自定义工具\n"
            "你可以使用 create_custom_tool 创建新工具，list_custom_tools 查看详情，"
            "toggle_custom_tool 启用/禁用，delete_custom_tool 删除。\n"
            f"已安装的自定义工具:\n{tool_list}\n"
        )

    def append_daily(self, content: str, chat_id: str = "") -> None:
        """追加内容到今日日志，带 chat_id 标签便于过滤"""
        today_path = self.memory_dir / f"{date.today().isoformat()}.md"
        tag = f"[{chat_id}] " if chat_id else ""
        with open(today_path, "a", encoding="utf-8") as f:
            f.write(f"{tag}{content.rstrip()}\n\n")

    def update_memory(self, section: str, content: str) -> None:
        """更新 MEMORY.md 中特定段落"""
        mem_path = self.workspace / "MEMORY.md"
        if not mem_path.exists():
            mem_path.write_text(f"# 记忆\n\n## {section}\n{content}\n", encoding="utf-8")
            return

        text = mem_path.read_text(encoding="utf-8")
        pattern = rf"(## {re.escape(section)}\n)(.*?)(\n## |\Z)"
        match = re.search(pattern, text, re.DOTALL)

        if match:
            replacement = f"{match.group(1)}{content}\n{match.group(3)}"
            text = text[: match.start()] + replacement + text[match.end():]
        else:
            text = text.rstrip() + f"\n\n## {section}\n{content}\n"

        mem_path.write_text(text, encoding="utf-8")
        logger.info("MEMORY.md [%s] 已更新", section)

    def flush_before_compaction(self, session_messages: list[dict]) -> str:
        """生成 prompt 让 LLM 提取需要持久化的信息"""
        conversation = "\n".join(
            f"[{m.get('role', 'unknown')}] {m.get('content', '')}"
            for m in session_messages
        )
        return (
            "请从以下对话中提取需要长期记住的信息（如用户偏好、重要事实、待办事项）。\n"
            "仅输出需要记住的条目，每条一行，格式为 `- 内容`。如果没有需要记住的，输出「无」。\n\n"
            f"对话内容：\n{conversation}"
        )

    # ── 自我修改 API ──

    EDITABLE_FILES = {"SOUL.md", "MEMORY.md", "HEARTBEAT.md"}

    def read_self_file(self, filename: str) -> str:
        """读取工作区配置文件"""
        if filename not in self.EDITABLE_FILES:
            raise ValueError(f"不允许读取 {filename}，可读文件: {', '.join(sorted(self.EDITABLE_FILES))}")
        path = self.workspace / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_self_file(self, filename: str, content: str) -> None:
        """写入工作区配置文件"""
        if filename not in self.EDITABLE_FILES:
            raise ValueError(f"不允许写入 {filename}，可写文件: {', '.join(sorted(self.EDITABLE_FILES))}")
        path = self.workspace / filename
        path.write_text(content, encoding="utf-8")
        logger.info("%s 已更新 (%d 字节)", filename, len(content))

    def _read_daily(self, d: date) -> str:
        path = self.memory_dir / f"{d.isoformat()}.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def _read_daily_for_chat(self, d: date, chat_id: str) -> str:
        """读取日志中属于指定 chat_id 的条目"""
        raw = self._read_daily(d)
        if not raw:
            return ""
        tag = f"[{chat_id}] "
        lines = []
        for line in raw.split("\n"):
            if line.startswith(tag):
                # 去掉标签前缀，保留内容
                lines.append(line[len(tag):])
            elif not line.startswith("[") and line.strip():
                # 无标签的旧条目（兼容历史数据）— 跳过，因为无法确定归属
                pass
        return "\n".join(lines) if lines else ""
