"""Claude Code 子进程执行器 — 支持复杂任务委托"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from lq.config import APIConfig

logger = logging.getLogger(__name__)

# Bash 命令安全限制
_BLOCKED_COMMANDS = frozenset({
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
    ":(){:|:&};:", "fork bomb",
    "> /dev/sda", "chmod -R 777 /",
    "shutdown", "reboot", "halt", "poweroff",
})

_BLOCKED_PREFIXES = (
    "sudo rm -rf /",
    "sudo mkfs",
    "sudo dd ",
    "sudo shutdown",
    "sudo reboot",
    "sudo halt",
)

# Bash 输出截断限制
_MAX_BASH_OUTPUT = 10_000  # 字符


class ClaudeCodeExecutor:
    """通过 claude CLI 子进程执行复杂任务"""

    def __init__(self, workspace: Path, api_config: APIConfig) -> None:
        self.workspace = workspace
        self.api_config = api_config

    async def execute(self, prompt: str, timeout: int = 300) -> dict:
        """非阻塞执行 claude 命令，返回 {success, output, error}。

        Args:
            prompt: 发送给 Claude Code 的指令。
            timeout: 最大执行时间（秒），默认 5 分钟。
        """
        env = self._build_env()

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(self.workspace),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode("utf-8")),
                timeout=timeout,
            )

            output = stdout.decode("utf-8").strip()
            error = stderr.decode("utf-8").strip()

            # 截断过长的输出
            if len(output) > _MAX_BASH_OUTPUT:
                output = output[:_MAX_BASH_OUTPUT] + f"\n... (输出已截断，共 {len(stdout)} 字节)"

            if proc.returncode == 0:
                logger.info("CC 执行成功: %s...", output[:200])
                return {"success": True, "output": output, "error": ""}
            else:
                logger.warning("CC 执行失败 (code=%d): %s", proc.returncode, error[:200])
                return {"success": False, "output": output, "error": error}

        except asyncio.TimeoutError:
            logger.error("CC 执行超时 (%ds)", timeout)
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "", "error": f"执行超时 ({timeout}s)"}
        except FileNotFoundError:
            logger.error("claude CLI 未找到")
            return {"success": False, "output": "", "error": "claude CLI 未安装，请先安装 Claude Code CLI"}

    async def execute_with_context(
        self,
        prompt: str,
        context: str = "",
        working_dir: str = "",
        timeout: int = 300,
    ) -> dict:
        """带上下文的 Claude Code 执行。

        Args:
            prompt: 用户的具体指令。
            context: 额外的上下文信息（如当前对话背景）。
            working_dir: 工作目录（默认使用工作区目录）。
            timeout: 最大执行时间（秒）。
        """
        full_prompt = prompt
        if context:
            full_prompt = f"背景信息：{context}\n\n任务：{prompt}"

        env = self._build_env()
        cwd = working_dir or str(self.workspace)

        try:
            proc = await asyncio.create_subprocess_exec(
                "claude",
                "--print",
                "--dangerously-skip-permissions",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=full_prompt.encode("utf-8")),
                timeout=timeout,
            )

            output = stdout.decode("utf-8").strip()
            error = stderr.decode("utf-8").strip()

            if len(output) > _MAX_BASH_OUTPUT:
                output = output[:_MAX_BASH_OUTPUT] + f"\n... (输出已截断，共 {len(stdout)} 字节)"

            if proc.returncode == 0:
                logger.info("CC 执行成功 (dir=%s): %s...", cwd, output[:200])
                return {"success": True, "output": output, "error": ""}
            else:
                logger.warning("CC 执行失败 (dir=%s, code=%d): %s", cwd, proc.returncode, error[:200])
                return {"success": False, "output": output, "error": error}

        except asyncio.TimeoutError:
            logger.error("CC 执行超时 (%ds, dir=%s)", timeout, cwd)
            try:
                proc.kill()
            except Exception:
                pass
            return {"success": False, "output": "", "error": f"执行超时 ({timeout}s)"}
        except FileNotFoundError:
            logger.error("claude CLI 未找到")
            return {"success": False, "output": "", "error": "claude CLI 未安装"}

    def _build_env(self) -> dict[str, str]:
        env = os.environ.copy()
        env["ANTHROPIC_API_KEY"] = self.api_config.api_key
        if self.api_config.base_url:
            env["ANTHROPIC_BASE_URL"] = self.api_config.base_url
        return env


class BashExecutor:
    """安全的 Bash 命令执行器"""

    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    async def execute(
        self,
        command: str,
        working_dir: str = "",
        timeout: int = 60,
    ) -> dict:
        """执行 bash 命令，返回 {success, output, error, exit_code}。

        Args:
            command: 要执行的 shell 命令。
            working_dir: 工作目录（默认使用工作区目录）。
            timeout: 最大执行时间（秒），默认 60 秒。
        """
        # 安全检查
        safety_check = self._check_safety(command)
        if safety_check:
            return {
                "success": False,
                "output": "",
                "error": f"安全限制: {safety_check}",
                "exit_code": -1,
            }

        cwd = working_dir or str(self.workspace)
        logger.info("Bash 执行: %s (dir=%s)", command[:100], cwd)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=os.environ.copy(),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            output = stdout.decode("utf-8", errors="replace").strip()
            error = stderr.decode("utf-8", errors="replace").strip()

            # 截断过长的输出
            if len(output) > _MAX_BASH_OUTPUT:
                output = output[:_MAX_BASH_OUTPUT] + f"\n... (输出已截断，共 {len(stdout)} 字节)"
            if len(error) > _MAX_BASH_OUTPUT:
                error = error[:_MAX_BASH_OUTPUT] + f"\n... (错误输出已截断)"

            exit_code = proc.returncode or 0
            success = exit_code == 0

            if success:
                logger.info("Bash 执行成功 (exit=%d): %s...", exit_code, output[:200])
            else:
                logger.warning("Bash 执行失败 (exit=%d): %s", exit_code, error[:200])

            return {
                "success": success,
                "output": output,
                "error": error,
                "exit_code": exit_code,
            }

        except asyncio.TimeoutError:
            logger.error("Bash 执行超时 (%ds): %s", timeout, command[:80])
            try:
                proc.kill()
            except Exception:
                pass
            return {
                "success": False,
                "output": "",
                "error": f"命令执行超时 ({timeout}s)",
                "exit_code": -1,
            }

    @staticmethod
    def _check_safety(command: str) -> str:
        """检查命令安全性，返回空字符串表示安全，否则返回拒绝原因"""
        cmd_lower = command.strip().lower()

        # 检查完全匹配的危险命令
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return f"命令包含危险操作: {blocked}"

        # 检查前缀匹配
        for prefix in _BLOCKED_PREFIXES:
            if cmd_lower.startswith(prefix):
                return f"命令以危险前缀开头: {prefix}"

        return ""
