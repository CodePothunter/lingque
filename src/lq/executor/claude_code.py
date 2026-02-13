"""Claude Code 子进程执行器"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from lq.config import APIConfig

logger = logging.getLogger(__name__)


class ClaudeCodeExecutor:
    """通过 claude CLI 子进程执行复杂任务"""

    def __init__(self, workspace: Path, api_config: APIConfig) -> None:
        self.workspace = workspace
        self.api_config = api_config

    async def execute(self, prompt: str, timeout: int = 120) -> dict:
        """非阻塞执行 claude 命令，返回 {success, output, error}"""
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

            if proc.returncode == 0:
                logger.debug("CC 执行成功: %s...", output[:100])
                return {"success": True, "output": output, "error": ""}
            else:
                logger.warning("CC 执行失败 (code=%d): %s", proc.returncode, error)
                return {"success": False, "output": output, "error": error}

        except asyncio.TimeoutError:
            logger.error("CC 执行超时 (%ds)", timeout)
            if proc:
                proc.kill()
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
