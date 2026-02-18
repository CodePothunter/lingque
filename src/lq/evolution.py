"""自进化引擎 — 分析并持续改进自身框架代码

在心跳周期中被调用，负责：
- 跟踪每日改进次数（频率控制）
- 提供源代码结构摘要
- 管理 EVOLUTION.md 生命周期
- 持久化进化状态

实际的代码分析和改进由 LLM 工具循环驱动（在 gateway.py 中），
本模块只负责状态管理和辅助信息收集。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

CST = timezone(timedelta(hours=8))


def _find_source_root() -> Path | None:
    """从已安装的 lq 包反推 lingque 仓库根目录。

    仅在可编辑安装（``uv sync`` / ``pip install -e``）时有效。
    """
    try:
        import lq as _lq
        pkg = Path(_lq.__file__).resolve().parent  # src/lq/
        # 向上查找包含 pyproject.toml 或 .git 的目录
        for ancestor in (pkg.parent.parent, pkg.parent):
            if (ancestor / "pyproject.toml").exists() or (ancestor / ".git").exists():
                return ancestor
    except Exception:
        pass
    return None


class EvolutionEngine:
    """管理自进化状态和日频率限制。

    生命周期由 ``AssistantGateway`` 管理：
    1. 启动时实例化，传入 workspace 和 max_daily
    2. 每次心跳中调用 ``can_evolve()`` 检查限制
    3. 进化完成后调用 ``record_attempt()`` 计数
    """

    def __init__(self, workspace: Path, max_daily: int = 3) -> None:
        self.workspace = workspace
        self.max_daily = max_daily
        self.source_root = _find_source_root()
        self.evolution_path = workspace / "EVOLUTION.md"
        self._state_path = workspace / "evolution-state.json"
        self._today_count = 0
        self._last_date = ""
        self._load_state()

    # ── 状态持久化 ──

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._last_date = data.get("date", "")
            self._today_count = data.get("count", 0)
        except Exception:
            logger.warning("进化状态文件损坏，重置")

    def _save_state(self) -> None:
        try:
            self._state_path.write_text(
                json.dumps({"date": self._last_date, "count": self._today_count}),
                encoding="utf-8",
            )
        except Exception:
            logger.warning("进化状态保存失败")

    # ── 频率控制 ──

    def can_evolve(self) -> bool:
        """检查是否可以执行进化（每日限制未超）"""
        today = datetime.now(CST).strftime("%Y-%m-%d")
        if self._last_date != today:
            # 新的一天，重置计数
            self._last_date = today
            self._today_count = 0
            self._save_state()
        return self._today_count < self.max_daily

    def record_attempt(self) -> None:
        """记录一次进化尝试（成功才计数）"""
        today = datetime.now(CST).strftime("%Y-%m-%d")
        if self._last_date != today:
            self._last_date = today
            self._today_count = 0
        self._today_count += 1
        self._save_state()
        logger.info("进化计数: %d/%d", self._today_count, self.max_daily)

    @property
    def remaining_today(self) -> int:
        """今日剩余可进化次数"""
        today = datetime.now(CST).strftime("%Y-%m-%d")
        if self._last_date != today:
            return self.max_daily
        return max(0, self.max_daily - self._today_count)

    # ── EVOLUTION.md 管理 ──

    def ensure_evolution_file(self) -> None:
        """确保 EVOLUTION.md 存在，不存在则创建初始模板"""
        if not self.evolution_path.exists():
            from lq.prompts import EVOLUTION_INIT_TEMPLATE
            self.evolution_path.write_text(EVOLUTION_INIT_TEMPLATE, encoding="utf-8")
            logger.info("已创建 EVOLUTION.md")

    def read_evolution(self) -> str:
        """读取 EVOLUTION.md 内容"""
        self.ensure_evolution_file()
        return self.evolution_path.read_text(encoding="utf-8")

    # ── 源代码信息 ──

    def get_source_summary(self) -> str:
        """获取源代码结构摘要，供 LLM 参考。

        返回目录树，含每个 .py 文件的相对路径和大小。
        """
        if not self.source_root:
            return "（无法定位源代码目录，可能不是可编辑安装）"

        src_dir = self.source_root / "src" / "lq"
        if not src_dir.exists():
            return f"（源代码目录不存在: {src_dir}）"

        lines = [
            f"仓库根目录: {self.source_root}",
            f"包目录: {src_dir}",
            "",
            "文件结构:",
        ]

        for p in sorted(src_dir.rglob("*.py")):
            rel = p.relative_to(src_dir)
            size = p.stat().st_size
            lines.append(f"  {rel} ({size} 字节)")

        return "\n".join(lines)

    def get_recent_git_log(self, n: int = 10) -> str:
        """获取最近的 git 提交历史（同步调用，仅用于构建 prompt）"""
        if not self.source_root or not (self.source_root / ".git").exists():
            return "（非 git 仓库）"
        try:
            import subprocess
            result = subprocess.run(
                ["git", "log", f"--oneline", f"-{n}"],
                capture_output=True, text=True, cwd=str(self.source_root),
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return "（无提交历史）"
        except Exception:
            return "（git log 获取失败）"
