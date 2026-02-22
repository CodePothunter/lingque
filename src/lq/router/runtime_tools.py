"""运行时工具实现：Python 代码执行、文件读写、自身统计查询"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from lq.prompts import (
    ERR_FILE_NOT_FOUND, ERR_FILE_READ_FAILED, ERR_FILE_WRITE_FAILED,
    RESULT_FILE_WRITTEN,
)

logger = logging.getLogger(__name__)


class RuntimeToolsMixin:
    """Python 执行、文件操作与自身统计。"""

    async def _tool_run_python(self, code: str, timeout: int = 30) -> dict:
        """在子进程中执行 Python 代码"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.memory.workspace),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            # 截断过长输出
            if len(output) > 10000:
                output = output[:10000] + f"\n... (输出已截断，共 {len(stdout)} 字节)"
            if len(error) > 5000:
                error = error[:5000] + f"\n... (错误输出已截断)"

            exit_code = proc.returncode or 0
            return {
                "success": exit_code == 0,
                "output": output,
                "error": error,
                "exit_code": exit_code,
            }
        except asyncio.TimeoutError:
            logger.error("run_python 超时 (%ds)", timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "", "error": f"执行超时 ({timeout}s)", "exit_code": -1}
        except Exception as e:
            logger.exception("run_python 失败")
            return {"success": False, "output": "", "error": str(e), "exit_code": -1}

    # ── 统计 ──

    def _tool_get_my_stats(self, category: str = "today") -> dict:
        """返回自身运行统计信息"""
        result: dict[str, Any] = {"success": True}
        if category == "today" and self.stats:
            daily = self.stats.get_daily_summary()
            result["today"] = daily
            result["uptime"] = self._format_uptime()
            result["model"] = getattr(self.executor, "model", "unknown")
        elif category == "month" and self.stats:
            monthly = self.stats.get_monthly_summary()
            result["month"] = monthly
        elif category == "capability":
            result["tool_stats"] = {
                name: {
                    "total": s["success"] + s["fail"],
                    "success_rate": round(s["success"] / max(s["success"] + s["fail"], 1) * 100),
                    "last_error": s.get("last_error", ""),
                }
                for name, s in self._tool_stats.items()
                if s["success"] + s["fail"] > 0
            }
        else:
            result["message"] = "统计模块未加载或类别无效"
        return result

    def _format_uptime(self) -> str:
        """格式化运行时间"""
        elapsed = int(time.time()) - self._startup_ts // 1000
        if elapsed < 60:
            return f"{elapsed}秒"
        if elapsed < 3600:
            return f"{elapsed // 60}分钟"
        hours = elapsed // 3600
        minutes = (elapsed % 3600) // 60
        if hours < 24:
            return f"{hours}小时{minutes}分钟"
        days = hours // 24
        hours = hours % 24
        return f"{days}天{hours}小时"

    # ── 漂移检测 ──

    def _tool_detect_drift(self, days: int = 1) -> dict:
        """扫描最近 N 天的回复，检测行为漂移。"""
        from lq.drift import scan_session_replies

        result = scan_session_replies(
            self.memory.workspace / "sessions",
            days=days,
        )
        return {"success": True, **result}

    # ── 文件操作 ──

    def _tool_read_file(self, path: str, max_lines: int = 500) -> dict:
        """读取文件系统中的文件"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        if not file_path.exists():
            return {"success": False, "error": ERR_FILE_NOT_FOUND.format(path=str(file_path))}

        if not file_path.is_file():
            return {"success": False, "error": f"路径不是文件: {file_path}"}

        try:
            # 检查文件大小，避免读取超大文件
            size = file_path.stat().st_size
            if size > 5_000_000:  # 5MB
                return {
                    "success": False,
                    "error": f"文件过大 ({size} 字节，上限 5MB)，请使用 run_bash 的 head/tail 命令读取部分内容",
                }

            text = file_path.read_text(encoding="utf-8", errors="replace")
            lines = text.splitlines()
            total_lines = len(lines)

            if total_lines > max_lines:
                text = "\n".join(lines[:max_lines])
                text += f"\n... (已显示前 {max_lines} 行，共 {total_lines} 行)"

            return {
                "success": True,
                "path": str(file_path),
                "content": text,
                "lines": min(total_lines, max_lines),
                "total_lines": total_lines,
                "size": size,
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_READ_FAILED.format(error=str(e))}

    def _tool_write_file(self, path: str, content: str) -> dict:
        """写入文件到文件系统"""
        from pathlib import Path as _Path

        file_path = _Path(path)
        # 相对路径基于工作区
        if not file_path.is_absolute():
            file_path = self.memory.workspace / file_path

        try:
            # 自动创建父目录
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(content, encoding="utf-8")
            size = file_path.stat().st_size
            logger.info("write_file: %s (%d 字节)", file_path, size)
            return {
                "success": True,
                "message": RESULT_FILE_WRITTEN.format(path=str(file_path), size=size),
            }
        except Exception as e:
            return {"success": False, "error": ERR_FILE_WRITE_FAILED.format(error=str(e))}
