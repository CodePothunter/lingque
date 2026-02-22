"""实例文件夹自动增量备份"""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

CST = timezone(timedelta(hours=8))

logger = logging.getLogger(__name__)

# 备份时排除的目录和文件
EXCLUDE_NAMES = {"logs", ".tmp", "__pycache__", "gateway.pid"}

# 用于 mtime 变化检测的关键文件
KEY_FILES = [
    "config.json",
    "SOUL.md",
    "MEMORY.md",
    "HEARTBEAT.md",
    "CURIOSITY.md",
    "EVOLUTION.md",
]


class BackupManager:
    """基于内容增量的实例文件夹备份管理器。

    检测实例文件夹的实质性变化（大小增量或关键文件修改），
    达到阈值时创建完整快照备份，并自动清理旧备份。
    """

    def __init__(
        self,
        home: Path,
        max_backups: int = 10,
        size_threshold: int = 512 * 1024,
    ) -> None:
        self.home = home  # ~/.lq-{slug}
        self.backup_root = home.parent / ".lq-backups" / home.name.removeprefix(".lq-")
        self.max_backups = max_backups
        self.size_threshold = size_threshold  # bytes
        self._last_size: int = 0
        self._last_mtimes: dict[str, float] = {}
        self._last_backup_date: str = ""

    def _measure(self) -> tuple[int, dict[str, float]]:
        """计算文件夹大小（排除 logs/、.tmp/ 等）和关键文件 mtime。"""
        total_size = 0
        for p in self.home.rglob("*"):
            # 跳过排除项
            if any(part in EXCLUDE_NAMES for part in p.relative_to(self.home).parts):
                continue
            if p.is_file():
                try:
                    total_size += p.stat().st_size
                except OSError:
                    pass

        mtimes: dict[str, float] = {}
        for name in KEY_FILES:
            fp = self.home / name
            if fp.exists():
                try:
                    mtimes[name] = fp.stat().st_mtime
                except OSError:
                    pass

        return total_size, mtimes

    def should_backup(self) -> bool:
        """判断是否需要备份。

        触发条件（任一满足即备份）：
        1. 文件夹大小变化 ≥ size_threshold
        2. 关键文件 mtime 发生变化
        3. 今日尚未备份（每日保底）
        """
        current_size, current_mtimes = self._measure()
        today = datetime.now(CST).strftime("%Y-%m-%d")

        # 条件 1: 大小变化超过阈值
        size_delta = abs(current_size - self._last_size)
        if self._last_size > 0 and size_delta >= self.size_threshold:
            logger.info(
                "文件夹大小变化 %d bytes (阈值 %d)，触发备份",
                size_delta, self.size_threshold,
            )
            return True

        # 条件 2: 关键文件 mtime 变化
        if self._last_mtimes:
            for name, mtime in current_mtimes.items():
                old_mtime = self._last_mtimes.get(name)
                if old_mtime is not None and mtime != old_mtime:
                    logger.info("关键文件 %s 已修改，触发备份", name)
                    return True
            # 检查新增的关键文件
            for name in current_mtimes:
                if name not in self._last_mtimes:
                    logger.info("新增关键文件 %s，触发备份", name)
                    return True

        # 条件 3: 每日保底
        if today != self._last_backup_date:
            logger.info("今日尚未备份，触发每日保底备份")
            return True

        return False

    def create_backup(self) -> Path | None:
        """执行备份，返回备份路径。"""
        if not self.home.exists():
            logger.warning("实例文件夹不存在，跳过备份: %s", self.home)
            return None

        timestamp = datetime.now(CST).strftime("%Y%m%d-%H%M%S")
        dest = self.backup_root / timestamp

        try:
            self.backup_root.mkdir(parents=True, exist_ok=True)
            shutil.copytree(
                self.home,
                dest,
                ignore=shutil.ignore_patterns(*EXCLUDE_NAMES),
            )
        except Exception:
            logger.exception("备份失败: %s → %s", self.home, dest)
            # 清理不完整的备份
            if dest.exists():
                shutil.rmtree(dest, ignore_errors=True)
            return None

        # 更新状态
        self._last_size, self._last_mtimes = self._measure()
        self._last_backup_date = datetime.now(CST).strftime("%Y-%m-%d")

        logger.info("备份完成: %s", dest)
        self._prune()
        return dest

    def _prune(self) -> None:
        """保留最新 max_backups 个备份，删除旧的。"""
        if not self.backup_root.exists():
            return

        backups = sorted(
            [d for d in self.backup_root.iterdir() if d.is_dir()],
            key=lambda d: d.name,
        )

        while len(backups) > self.max_backups:
            oldest = backups.pop(0)
            try:
                shutil.rmtree(oldest)
                logger.info("已删除旧备份: %s", oldest.name)
            except Exception:
                logger.exception("删除旧备份失败: %s", oldest)

    async def run_forever(
        self,
        shutdown_event: asyncio.Event,
        interval: int = 60,
    ) -> None:
        """主循环：每 interval 秒检查一次，需要时创建备份。"""
        # 启动时记录初始状态并创建基线备份
        self._last_size, self._last_mtimes = self._measure()
        self.create_backup()

        while not shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    shutdown_event.wait(), timeout=interval,
                )
                break  # shutdown_event 已设置
            except asyncio.TimeoutError:
                pass  # 正常超时，执行检查
            except asyncio.CancelledError:
                break

            try:
                if self.should_backup():
                    self.create_backup()
            except Exception:
                logger.exception("备份检查失败")
