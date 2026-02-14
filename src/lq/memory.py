"""记忆管理 — 带 token 预算的上下文构建"""

from __future__ import annotations

import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from lq.feishu.sender import FeishuSender

from lq.prompts import (
    TAG_SOUL, TAG_MEMORY, TAG_CHAT_MEMORY, TAG_DAILY_LOG, TAG_SELF_AWARENESS,
    wrap_tag,
    TIME_DISPLAY, TRUNCATION_BUDGET_EXCEEDED, TRUNCATION_SHORT,
    EDITABLE_FILE_EXISTS, EDITABLE_FILE_MISSING, NO_DAILY_LOGS,
    TOOL_STATUS_ENABLED, TOOL_STATUS_DISABLED,
    GLOBAL_MEMORY_INIT, CHAT_MEMORY_INIT, CHAT_MEMORY_INIT_APPEND,
    ERR_FILE_NOT_ALLOWED_READ, ERR_FILE_NOT_ALLOWED_WRITE,
    SELF_AWARENESS_TEMPLATE, CUSTOM_TOOLS_SECTION_WITH_TOOLS, CUSTOM_TOOLS_SECTION_EMPTY,
    FLUSH_BEFORE_COMPACTION, FLUSH_ROLE_TOOL_CALL, FLUSH_ROLE_TOOL_RESULT, FLUSH_ROLE_DEFAULT,
)
from lq.session import estimate_tokens

logger = logging.getLogger(__name__)

# ── Token 预算 ──
# system prompt 的各部分 token 预算
SOUL_BUDGET = 3000        # 人格定义
MEMORY_BUDGET = 4000      # 长期记忆
DAILY_LOG_BUDGET = 2000   # 日志
AWARENESS_BUDGET = 2000   # 自我认知
TOTAL_SYSTEM_BUDGET = 15000  # 总预算

# 自我认知缓存有效期（秒）
_AWARENESS_CACHE_TTL = 300  # 5 分钟


class MemoryManager:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.memory_dir = workspace / "memory"
        self.memory_dir.mkdir(parents=True, exist_ok=True)
        self.chat_memories_dir = workspace / "chat_memories"
        self.chat_memories_dir.mkdir(parents=True, exist_ok=True)

        # 自我认知缓存
        self._awareness_cache: str = ""
        self._awareness_cache_time: float = 0
        self._awareness_cache_tokens: int = 0

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

    def build_neighbor_context(self, sender: FeishuSender, chat_id: str) -> str:
        """构建群里其他 bot 的上下文信息"""
        if not sender or not chat_id:
            return ""
        try:
            bot_ids = sender.get_bot_members(chat_id)
        except Exception:
            logger.warning("构建邻居上下文失败 chat=%s", chat_id[-8:], exc_info=True)
            return ""
        if not bot_ids:
            return ""
        lines = ["<neighbors>", "群里还有以下 AI 助理："]
        for bid in bot_ids:
            name = sender.get_member_name(bid)
            lines.append(f"- {name}")
        lines.append("</neighbors>")
        return "\n".join(lines)

    def build_context(self, chat_id: str = "", include_tools_awareness: bool = True, sender: FeishuSender | None = None) -> str:
        """拼接系统 prompt，带 token 预算控制。

        各部分按优先级分配预算：
        1. 时间 + SOUL.md（最高优先级，必须完整）
        2. MEMORY.md（高优先级，超预算时截断）
        3. 日志（中优先级，按 chat_id 过滤）
        4. 自我认知（缓存复用）
        """
        parts = []
        used_tokens = 0

        # 1. 当前时间（固定，~30 tokens）
        cst = timezone(timedelta(hours=8))
        now = datetime.now(cst)
        time_str = TIME_DISPLAY.format(formatted_time=now.strftime('%Y-%m-%d %H:%M:%S'))
        parts.append(time_str)
        used_tokens += estimate_tokens(time_str)

        # 2. SOUL.md — 核心人格，完整注入
        soul = self.read_soul()
        if soul:
            soul_tokens = estimate_tokens(soul)
            if soul_tokens > SOUL_BUDGET:
                # 人格定义超预算时截断（不应该发生，但防御性处理）
                soul = self._truncate_to_budget(soul, SOUL_BUDGET)
                soul_tokens = SOUL_BUDGET
            parts.append(wrap_tag(TAG_SOUL, soul))
            used_tokens += soul_tokens

        # 3. MEMORY.md — 长期记忆，超预算时智能截断
        memory = self.read_memory()
        if memory:
            mem_tokens = estimate_tokens(memory)
            if mem_tokens > MEMORY_BUDGET:
                memory = self._truncate_memory(memory, MEMORY_BUDGET)
                mem_tokens = MEMORY_BUDGET
            parts.append(wrap_tag(TAG_MEMORY, memory))
            used_tokens += mem_tokens

        # 3.5 注入 per-chat 长期记忆（区别于全局 MEMORY.md）
        if chat_id:
            chat_mem = self.read_chat_memory(chat_id)
            if chat_mem:
                parts.append(wrap_tag(TAG_CHAT_MEMORY, chat_mem))

        # 4. 日志 — 按 chat_id 过滤，限制预算
        if chat_id:
            remaining = TOTAL_SYSTEM_BUDGET - used_tokens - AWARENESS_BUDGET
            log_budget = min(DAILY_LOG_BUDGET, max(remaining, 500))
            today = date.today()
            for d in [today - timedelta(days=1), today]:
                log = self._read_daily_for_chat(d, chat_id)
                if log:
                    log_tokens = estimate_tokens(log)
                    if log_tokens > log_budget:
                        log = self._truncate_to_budget(log, log_budget)
                    parts.append(wrap_tag(TAG_DAILY_LOG, log, date=d.isoformat()))
                    used_tokens += min(log_tokens, log_budget)

        # 5. 自我认知 — 使用缓存
        if include_tools_awareness:
            awareness = self._get_cached_awareness()
            parts.append(awareness)

        # 6. 邻居感知 — 群里的其他 bot
        if sender and chat_id:
            neighbor_ctx = self.build_neighbor_context(sender, chat_id)
            if neighbor_ctx:
                parts.append(neighbor_ctx)

        return "\n\n".join(parts)

    def _get_cached_awareness(self) -> str:
        """获取自我认知文本，带缓存"""
        now = time.time()
        if (self._awareness_cache
                and now - self._awareness_cache_time < _AWARENESS_CACHE_TTL):
            return self._awareness_cache

        awareness = self._build_self_awareness()
        self._awareness_cache = awareness
        self._awareness_cache_time = now
        self._awareness_cache_tokens = estimate_tokens(awareness)
        return awareness

    def invalidate_awareness_cache(self) -> None:
        """手动失效自我认知缓存（工具列表变化时调用）"""
        self._awareness_cache = ""
        self._awareness_cache_time = 0

    def _truncate_to_budget(self, text: str, budget: int) -> str:
        """按 token 预算截断文本，尽量在段落/行边界处截断"""
        lines = text.split("\n")
        result = []
        tokens = 0
        for line in lines:
            line_tokens = estimate_tokens(line)
            if tokens + line_tokens > budget:
                result.append(TRUNCATION_BUDGET_EXCEEDED)
                break
            result.append(line)
            tokens += line_tokens
        return "\n".join(result)

    def _truncate_memory(self, memory: str, budget: int) -> str:
        """智能截断 MEMORY.md：保留所有段落标题，截断过长的段落内容。

        优先保留靠后的段落（通常是最近更新的）。
        """
        sections = re.split(r'(?=^## )', memory, flags=re.MULTILINE)
        if not sections:
            return self._truncate_to_budget(memory, budget)

        # 计算每个段落的 token 数
        section_tokens = [(s, estimate_tokens(s)) for s in sections if s.strip()]
        total = sum(t for _, t in section_tokens)

        if total <= budget:
            return memory

        # 从前面的段落开始截断（保留后面的最新内容）
        result = []
        remaining = budget
        # 先保留所有标题行（约占总量的很小部分）
        for section, tokens in reversed(section_tokens):
            if tokens <= remaining:
                result.insert(0, section)
                remaining -= tokens
            else:
                # 截断这个段落：保留标题 + 前几行
                lines = section.split("\n")
                kept = [lines[0]]  # 标题
                line_budget = remaining - estimate_tokens(lines[0])
                for line in lines[1:]:
                    lt = estimate_tokens(line)
                    if lt > line_budget:
                        kept.append(TRUNCATION_SHORT)
                        break
                    kept.append(line)
                    line_budget -= lt
                result.insert(0, "\n".join(kept))
                break

        return "\n".join(result)

    def _build_self_awareness(self) -> str:
        """构建自我认知上下文，让助理了解自己的架构和可修改的文件"""
        ws = self.workspace
        editable_files = []
        for name in ["SOUL.md", "MEMORY.md", "HEARTBEAT.md"]:
            p = ws / name
            if p.exists():
                editable_files.append(EDITABLE_FILE_EXISTS.format(name=name, size=p.stat().st_size))
            else:
                editable_files.append(EDITABLE_FILE_MISSING.format(name=name))

        daily_logs = sorted(self.memory_dir.glob("*.md"), reverse=True)[:5]
        if daily_logs:
            log_list = "\n".join(f"  - memory/{f.name}" for f in daily_logs)
        else:
            log_list = NO_DAILY_LOGS

        awareness_content = SELF_AWARENESS_TEMPLATE.format(
            workspace=ws,
            editable_files="\n".join(editable_files),
            log_list=log_list,
            custom_tools_section=self._build_custom_tools_awareness(),
        )
        return wrap_tag(TAG_SELF_AWARENESS, awareness_content)

    def _build_custom_tools_awareness(self) -> str:
        """构建自定义工具的自我认知段落。"""
        tools_dir = self.workspace / "tools"
        if not tools_dir.exists():
            return CUSTOM_TOOLS_SECTION_EMPTY

        tool_files = sorted(f for f in tools_dir.glob("*.py") if not f.name.startswith("_"))
        if not tool_files:
            return CUSTOM_TOOLS_SECTION_EMPTY

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
            status = TOOL_STATUS_DISABLED if name in disabled else TOOL_STATUS_ENABLED
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
        return CUSTOM_TOOLS_SECTION_WITH_TOOLS.format(tool_list=tool_list)

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
            mem_path.write_text(GLOBAL_MEMORY_INIT.format(section=section, content=content), encoding="utf-8")
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
        """生成 prompt 让 LLM 提取需要持久化的信息。

        改进版：包含工具调用记录，提供更完整的上下文。
        """
        lines = []
        for m in session_messages:
            role = m.get("role", "unknown")
            content = m.get("content", "")
            if m.get("is_tool_use"):
                lines.append(FLUSH_ROLE_TOOL_CALL.format(tool_name=m.get('tool_name', '?')))
            elif m.get("is_tool_result"):
                lines.append(FLUSH_ROLE_TOOL_RESULT.format(content_preview=content[:200]))
            else:
                lines.append(FLUSH_ROLE_DEFAULT.format(role=role, content=content))

        conversation = "\n".join(lines)
        return FLUSH_BEFORE_COMPACTION.format(conversation=conversation)

    # ── 自我修改 API ──

    EDITABLE_FILES = {"SOUL.md", "MEMORY.md", "HEARTBEAT.md"}

    def read_self_file(self, filename: str) -> str:
        """读取工作区配置文件"""
        if filename not in self.EDITABLE_FILES:
            raise ValueError(ERR_FILE_NOT_ALLOWED_READ.format(filename=filename, allowed=', '.join(sorted(self.EDITABLE_FILES))))
        path = self.workspace / filename
        if not path.exists():
            return ""
        return path.read_text(encoding="utf-8")

    def write_self_file(self, filename: str, content: str) -> None:
        """写入工作区配置文件"""
        if filename not in self.EDITABLE_FILES:
            raise ValueError(ERR_FILE_NOT_ALLOWED_WRITE.format(filename=filename, allowed=', '.join(sorted(self.EDITABLE_FILES))))
        path = self.workspace / filename
        path.write_text(content, encoding="utf-8")
        logger.info("%s 已更新 (%d 字节)", filename, len(content))

    # ── Chat Memory（per-chat 长期记忆）API ──

    def _chat_memory_path(self, chat_id: str) -> Path:
        """返回指定 chat_id 的记忆文件路径"""
        # 用 chat_id 作文件名（飞书 chat_id 是 oc_ 开头的 ASCII 串，安全作文件名）
        return self.chat_memories_dir / f"{chat_id}.md"

    def read_chat_memory(self, chat_id: str) -> str:
        """读取指定聊天窗口的专属长期记忆"""
        path = self._chat_memory_path(chat_id)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def update_chat_memory(self, chat_id: str, section: str, content: str) -> None:
        """更新指定聊天窗口记忆中的特定段落（类似 update_memory 但 per-chat）"""
        path = self._chat_memory_path(chat_id)
        if not path.exists():
            path.write_text(
                CHAT_MEMORY_INIT.format(section=section, content=content), encoding="utf-8"
            )
            logger.info("创建 chat_memory [%s] section=%s", chat_id[-8:], section)
            return

        text = path.read_text(encoding="utf-8")
        pattern = rf"(## {re.escape(section)}\n)(.*?)(\n## |\Z)"
        match = re.search(pattern, text, re.DOTALL)

        if match:
            replacement = f"{match.group(1)}{content}\n{match.group(3)}"
            text = text[: match.start()] + replacement + text[match.end():]
        else:
            text = text.rstrip() + f"\n\n## {section}\n{content}\n"

        path.write_text(text, encoding="utf-8")
        logger.info("chat_memory [%s] section=%s 已更新", chat_id[-8:], section)

    def append_chat_memory(self, chat_id: str, content: str) -> None:
        """追加内容到指定聊天窗口的记忆末尾"""
        path = self._chat_memory_path(chat_id)
        if not path.exists():
            path.write_text(CHAT_MEMORY_INIT_APPEND.format(content=content.rstrip()), encoding="utf-8")
        else:
            with open(path, "a", encoding="utf-8") as f:
                f.write(f"{content.rstrip()}\n")
        logger.info("chat_memory [%s] 已追加", chat_id[-8:])

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
                lines.append(line[len(tag):])
            elif not line.startswith("[") and line.strip():
                pass
        return "\n".join(lines) if lines else ""
