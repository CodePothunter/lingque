"""自进化引擎 — 分析并持续改进自身框架代码

在心跳周期中被调用，负责：
- 跟踪每日改进次数（频率控制）
- 提供源代码结构摘要
- 管理 EVOLUTION.md 生命周期
- 持久化进化状态
- 进化守护：checkpoint / 健康检查 / 自动回滚

实际的代码分析和改进由 LLM 工具循环驱动（在 gateway.py 中），
本模块只负责状态管理和辅助信息收集。
"""

from __future__ import annotations

import json
import logging
import subprocess
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
        self._checkpoint_path = workspace / "evolution-checkpoint.json"
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
            result = subprocess.run(
                ["git", "log", "--oneline", f"-{n}"],
                capture_output=True, text=True, cwd=str(self.source_root),
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
            return "（无提交历史）"
        except Exception:
            return "（git log 获取失败）"

    # ── 进化守护：checkpoint / 健康检查 / 自动回滚 ──

    def _git_head(self) -> str | None:
        """获取当前 git HEAD commit hash"""
        if not self.source_root:
            return None
        try:
            r = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                capture_output=True, text=True,
                cwd=str(self.source_root), timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except Exception:
            return None

    def save_checkpoint(self) -> None:
        """进化前保存当前 commit 为安全点。

        如果进化导致运行时崩溃，下次启动时可回滚到这个 commit。
        """
        commit = self._git_head()
        if not commit:
            logger.warning("无法获取 git HEAD，跳过 checkpoint 保存")
            return
        data = {
            "commit": commit,
            "timestamp": datetime.now(CST).isoformat(),
        }
        self._checkpoint_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8",
        )
        logger.info("进化 checkpoint 已保存: %s", commit[:8])

    def clear_checkpoint(self) -> None:
        """清除 checkpoint（进化已验证安全）"""
        if self._checkpoint_path.exists():
            self._checkpoint_path.unlink()
            logger.info("进化 checkpoint 已清除（验证通过）")

    @property
    def has_checkpoint(self) -> bool:
        return self._checkpoint_path.exists()

    def startup_check(self, was_clean_shutdown: bool) -> bool:
        """启动时检查上次进化是否导致了问题。

        流程：
        1. 无 checkpoint → 正常，返回 True
        2. 有 checkpoint + 上次正常关闭 → 进化安全，清除 checkpoint，返回 True
        3. 有 checkpoint + 上次崩溃 → 跑健康检查
           a. 健康 → 清除 checkpoint，返回 True
           b. 不健康 → 回滚 + 记录失败，返回 True（已恢复）

        始终返回 True（无论是否回滚，都让启动继续）。
        """
        if not self.has_checkpoint:
            return True

        checkpoint = self._read_checkpoint()
        if not checkpoint:
            self.clear_checkpoint()
            return True

        safe_commit = checkpoint["commit"]
        ts = checkpoint.get("timestamp", "?")

        if was_clean_shutdown:
            # 上次正常关闭 → 进化通过了生产验证
            logger.info("上次进化后正常运行并关闭，验证通过 (checkpoint: %s)", safe_commit[:8])
            self.clear_checkpoint()
            return True

        # 上次崩溃了，跑健康检查
        logger.warning("上次异常退出，且存在进化 checkpoint (%s)，执行健康检查...", safe_commit[:8])

        if self._health_check():
            logger.info("健康检查通过，进化代码正常")
            self.clear_checkpoint()
            return True

        # 健康检查失败 → 回滚
        logger.error("健康检查失败！回滚到 checkpoint: %s", safe_commit[:8])
        self._rollback(safe_commit, ts)
        return True

    def _read_checkpoint(self) -> dict | None:
        try:
            return json.loads(self._checkpoint_path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("checkpoint 文件损坏")
            return None

    def _health_check(self) -> bool:
        """验证核心模块能正常导入。

        这是最基本的健康检查——如果连 import 都过不了，
        说明进化改出了语法错误或依赖问题。
        """
        checks = [
            "from lq.gateway import AssistantGateway",
            "from lq.router.core import MessageRouter",
            "from lq.memory import MemoryManager",
            "from lq.session import SessionManager",
        ]
        for stmt in checks:
            try:
                r = subprocess.run(
                    ["python", "-c", stmt],
                    capture_output=True, text=True,
                    cwd=str(self.source_root) if self.source_root else None,
                    timeout=15,
                )
                if r.returncode != 0:
                    logger.error("健康检查失败 [%s]: %s", stmt, r.stderr.strip()[:200])
                    return False
            except Exception as e:
                logger.error("健康检查异常 [%s]: %s", stmt, e)
                return False
        return True

    def _rollback(self, safe_commit: str, checkpoint_ts: str) -> None:
        """回滚到 checkpoint commit 并记录失败经验。"""
        if not self.source_root:
            logger.error("无法回滚：source_root 未知")
            self.clear_checkpoint()
            return

        # 获取当前 HEAD 用于失败记录
        bad_commit = self._git_head() or "unknown"

        # 获取进化提交的信息（用于失败记录）
        evolution_info = ""
        try:
            r = subprocess.run(
                ["git", "log", "--oneline", f"{safe_commit}..HEAD"],
                capture_output=True, text=True,
                cwd=str(self.source_root), timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                evolution_info = r.stdout.strip()
        except Exception:
            pass

        # 执行回滚
        try:
            r = subprocess.run(
                ["git", "reset", "--hard", safe_commit],
                capture_output=True, text=True,
                cwd=str(self.source_root), timeout=10,
            )
            if r.returncode == 0:
                logger.info("已回滚到 %s", safe_commit[:8])
            else:
                logger.error("git reset 失败: %s", r.stderr.strip())
        except Exception as e:
            logger.error("回滚执行异常: %s", e)

        # 记录失败经验到 EVOLUTION.md
        self._record_rollback_failure(safe_commit, bad_commit, checkpoint_ts, evolution_info)
        self.clear_checkpoint()

    def _record_rollback_failure(
        self, safe_commit: str, bad_commit: str,
        checkpoint_ts: str, evolution_info: str,
    ) -> None:
        """将回滚事件记录到 EVOLUTION.md 的失败记录中。"""
        self.ensure_evolution_file()
        today = datetime.now(CST).strftime("%Y-%m-%d %H:%M")

        entry = (
            f"\n### {today} — 启动回滚\n"
            f"- 安全点: `{safe_commit[:8]}`\n"
            f"- 坏提交: `{bad_commit[:8]}`\n"
            f"- checkpoint 时间: {checkpoint_ts}\n"
        )
        if evolution_info:
            entry += f"- 被回滚的提交:\n"
            for line in evolution_info.splitlines():
                entry += f"  - `{line}`\n"
        entry += "- 原因: 启动健康检查失败（上次进化后崩溃）\n"
        entry += "- **教训**: 需要更仔细的验证，避免类似改动\n"

        content = self.evolution_path.read_text(encoding="utf-8")
        # 插入到「失败记录」部分
        marker = "## 失败记录"
        if marker in content:
            content = content.replace(marker, marker + entry, 1)
        else:
            content += f"\n{marker}{entry}"
        self.evolution_path.write_text(content, encoding="utf-8")
        logger.info("回滚失败经验已记录到 EVOLUTION.md")
