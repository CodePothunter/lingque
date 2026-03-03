"""Claude Code 子进程执行器 — 支持复杂任务委托"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from lq.config import APIConfig

logger = logging.getLogger(__name__)


def _is_nested_claude_session() -> bool:
    """检测是否在 Claude Code 会话中运行（嵌套场景）"""
    import os
    return os.environ.get('CLAUDECODE') == '1'


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
        # 检测嵌套 Claude Code 会话
        if _is_nested_claude_session():
            logger.warning("检测到嵌套 Claude Code 会话，无法启动子进程")
            return {
                "success": False,
                "output": "",
                "error": (
                    "当前运行在 Claude Code 会话中，无法启动嵌套的 Claude Code 子进程。"
                    "建议：使用 run_bash 工具执行命令，或直接使用 read_file/write_file 操作文件。"
                ),
            }

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
                logger.info("CC 执行成功：%s...", output[:200])
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
            return {
                "success": False,
                "output": "",
                "error": (
                    f"Claude Code 执行超时 ({timeout}s)，"
                    f"如需更长执行时间，可在调用时指定更大的 timeout 参数"
                    f"（如 timeout={timeout * 2}）"
                ),
            }
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
        # 检测嵌套 Claude Code 会话
        if _is_nested_claude_session():
            logger.warning("检测到嵌套 Claude Code 会话，无法启动子进程")
            return {
                "success": False,
                "output": "",
                "error": (
                    "当前运行在 Claude Code 会话中，无法启动嵌套的 Claude Code 子进程。"
                    "建议：使用 run_bash 工具执行命令，或直接使用 read_file/write_file 操作文件。"
                ),
            }

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
            return {
                "success": False,
                "output": "",
                "error": (
                    f"Claude Code 执行超时 ({timeout}s)，"
                    f"如需更长执行时间，可在调用时指定更大的 timeout 参数"
                    f"（如 timeout={timeout * 2}）"
                ),
            }
        except FileNotFoundError:
            logger.error("claude CLI 未找到")
            return {"success": False, "output": "", "error": "claude CLI 未安装"}

    def _build_env(self) -> dict[str, str]:
        """构建子进程环境变量。
        
        不强制覆盖 ANTHROPIC_* 环境变量，让 claude CLI 使用其原生配置。
        如果环境中已存在这些变量，保持不变；否则从 api_config 注入作为后备。
        """
        env = os.environ.copy()
        # 只在环境变量不存在时才注入，优先使用环境配置
        if "ANTHROPIC_API_KEY" not in env and self.api_config.api_key:
            env["ANTHROPIC_API_KEY"] = self.api_config.api_key
        if "ANTHROPIC_BASE_URL" not in env and self.api_config.base_url:
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
        timeout: int = 600,
        idle_timeout: int = 300,
    ) -> dict:
        """执行 bash 命令，返回 {success, output, error, exit_code}。

        Args:
            command: 要执行的 shell 命令。
            working_dir: 工作目录（默认使用工作区目录）。
            timeout: 整体任务最大执行时间（秒），默认 600 秒（10 分钟）。
            idle_timeout: 无输出超时（秒），默认 300 秒（5 分钟）。如果超过此时间无任何输出则中断。
        """
        # 安全检查
        safety_check = self._check_safety(command)
        if safety_check:
            return {
                "success": False,
                "output": "",
                "error": f"安全限制：{safety_check}",
                "exit_code": -1,
            }

        cwd = working_dir or str(self.workspace)
        logger.info("Bash 执行：%s (dir=%s, timeout=%ds, idle_timeout=%ds)", command[:100], cwd, timeout, idle_timeout)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=os.environ.copy(),
            )

            # 双超时机制：整体超时 + 无输出超时
            start_time = asyncio.get_event_loop().time()
            last_output_time = start_time
            
            output_chunks = []
            error_chunks = []
            
            # 创建读取任务
            async def read_stream(stream, chunks, is_stderr=False):
                nonlocal last_output_time
                try:
                    while True:
                        line = await stream.readline()
                        if not line:
                            break
                        chunk = line.decode("utf-8", errors="replace")
                        chunks.append(chunk)
                        last_output_time = asyncio.get_event_loop().time()
                        
                        # 检查整体超时
                        elapsed = last_output_time - start_time
                        if elapsed > timeout:
                            logger.warning("Bash 执行整体超时 (%ds)", timeout)
                            proc.kill()
                            break
                        
                        # 检查无输出超时（仅在读取过程中检查）
                        idle = last_output_time - start_time
                        if idle > idle_timeout:
                            logger.warning("Bash 执行无输出超时 (%ds)", idle_timeout)
                            proc.kill()
                            break
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    logger.error("读取流时出错：%s", e)

            # 并行读取 stdout 和 stderr
            read_stdout = asyncio.create_task(read_stream(proc.stdout, output_chunks))
            read_stderr = asyncio.create_task(read_stream(proc.stderr, error_chunks, is_stderr=True))
            
            # 等待进程结束或超时
            try:
                await asyncio.wait_for(proc.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.error("Bash 执行整体超时 (%ds)", timeout)
                proc.kill()
            
            # 取消读取任务
            read_stdout.cancel()
            read_stderr.cancel()
            
            try:
                await asyncio.gather(read_stdout, read_stderr, return_exceptions=True)
            except Exception:
                pass

            output = "".join(output_chunks).strip()
            error = "".join(error_chunks).strip()

            # 截断过长的输出
            if len(output) > _MAX_BASH_OUTPUT:
                output = output[:_MAX_BASH_OUTPUT] + f"\n... (输出已截断，共 {len(output)} 字节)"
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

        except Exception as e:
            logger.error("Bash 执行异常：%s", e)
            return {
                "success": False,
                "output": "",
                "error": f"执行异常：{str(e)}",
                "exit_code": -1,
            }

    @staticmethod
    def _check_safety(command: str) -> str:
        """检查命令安全性，返回空字符串表示安全，否则返回拒绝原因"""
        cmd_lower = command.strip().lower()

        # 检查完全匹配的危险命令
        for blocked in _BLOCKED_COMMANDS:
            if blocked in cmd_lower:
                return f"命令包含危险操作：{blocked}"

        # 检查前缀匹配
        for prefix in _BLOCKED_PREFIXES:
            if cmd_lower.startswith(prefix):
                return f"命令以危险前缀开头：{prefix}"

        return ""
