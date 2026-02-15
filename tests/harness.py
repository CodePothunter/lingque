"""测试基础设施 — 共用的断言、LLM 调用、结果验证框架"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

INSTANCE = "@test"

# ── 测试结果 ──

@dataclass
class TestResult:
    name: str
    passed: bool
    detail: str = ""
    elapsed: float = 0.0


@dataclass
class TestSuite:
    name: str
    level: int  # 1=简单, 2=中等, 3=困难, 4=专家
    results: list[TestResult] = field(default_factory=list)

    def ok(self, name: str, detail: str = "", elapsed: float = 0.0) -> None:
        self.results.append(TestResult(name, True, detail, elapsed))
        print(f"  \033[1;32m✓\033[0m {name}" + (f"  ({detail})" if detail else ""))

    def fail(self, name: str, detail: str = "", elapsed: float = 0.0) -> None:
        self.results.append(TestResult(name, False, detail, elapsed))
        print(f"  \033[1;31m✗\033[0m {name}" + (f"  ({detail})" if detail else ""))

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def total(self) -> int:
        return len(self.results)

    def summary(self) -> str:
        level_labels = {1: "简单", 2: "中等", 3: "困难", 4: "专家"}
        label = level_labels.get(self.level, "?")
        if self.failed == 0:
            return f"\033[1;32m[Lv{self.level} {label}] {self.name}: 全部通过 {self.passed}/{self.total}\033[0m"
        return f"\033[1;31m[Lv{self.level} {label}] {self.name}: 通过 {self.passed}/{self.total}，失败 {self.failed}\033[0m"


# ── LLM 调用 ──

def say(message: str, timeout: int = 120) -> str:
    """调用 lq say @test 发送消息，返回 bot 回复文本"""
    t0 = time.time()
    result = subprocess.run(
        ["uv", "run", "lq", "say", INSTANCE, message],
        capture_output=True, text=True, timeout=timeout,
        cwd=str(Path(__file__).parent.parent),
    )
    elapsed = time.time() - t0
    output = result.stdout + result.stderr
    # 提取 bot 回复：匹配 ANSI 颜色码后的内容
    # 格式: \033[1;36mtest:\033[0m 回复内容
    lines = output.strip().split("\n")
    reply_lines = []
    capture = False
    for line in lines:
        # 去掉 ANSI 转义序列
        clean = re.sub(r'\033\[[0-9;]*m', '', line)
        if clean.startswith("test:"):
            capture = True
            reply_lines.append(clean[len("test:"):].strip())
        elif capture:
            reply_lines.append(clean)
    reply = "\n".join(reply_lines).strip()
    if not reply:
        # fallback: 返回所有非空行（去 ANSI）
        reply = re.sub(r'\033\[[0-9;]*m', '', output).strip()
    return reply


def clear_session() -> None:
    """清空测试实例的对话历史，避免上下文污染"""
    session_dir = Path.home() / ".lq-test" / "sessions"
    if session_dir.exists():
        for f in session_dir.glob("*.json"):
            f.unlink()


# ── 验证辅助 ──

def check_contains(reply: str, keywords: list[str]) -> tuple[bool, str]:
    """检查回复是否包含指定关键词（任一匹配即通过）"""
    for kw in keywords:
        if kw.lower() in reply.lower():
            return True, f"匹配到: {kw}"
    return False, f"未找到任何关键词: {keywords}，回复: {reply[:200]}"


def check_contains_all(reply: str, keywords: list[str]) -> tuple[bool, str]:
    """检查回复是否包含所有指定关键词"""
    missing = [kw for kw in keywords if kw.lower() not in reply.lower()]
    if not missing:
        return True, "全部匹配"
    return False, f"缺失: {missing}，回复: {reply[:200]}"


def check_number_in_range(reply: str, low: float, high: float) -> tuple[bool, str]:
    """从回复中提取数字，检查是否在指定范围内"""
    numbers = re.findall(r'-?[\d,]+\.?\d*', reply.replace(",", ""))
    for n_str in numbers:
        try:
            n = float(n_str)
            if low <= n <= high:
                return True, f"找到数字 {n} 在 [{low}, {high}] 范围内"
        except ValueError:
            continue
    return False, f"未找到 [{low}, {high}] 范围内的数字，回复: {reply[:200]}"


def check_exact_number(reply: str, expected: float, tolerance: float = 0.01) -> tuple[bool, str]:
    """检查回复中是否包含精确数字（允许误差）"""
    numbers = re.findall(r'-?[\d,]+\.?\d*', reply.replace(",", ""))
    for n_str in numbers:
        try:
            n = float(n_str)
            if abs(n - expected) <= tolerance:
                return True, f"找到精确数字 {n}（期望 {expected}）"
        except ValueError:
            continue
    return False, f"未找到数字 {expected}（容差 {tolerance}），回复: {reply[:200]}"


def check_code_block(reply: str) -> tuple[bool, str]:
    """检查回复是否包含代码块"""
    if "```" in reply:
        return True, "包含代码块"
    return False, f"未找到代码块，回复: {reply[:200]}"


def check_python_syntax(reply: str) -> tuple[bool, str]:
    """从回复中提取 Python 代码并检查语法"""
    # 提取 ```python ... ``` 或 ``` ... ``` 中的代码
    pattern = r'```(?:python)?\s*\n(.*?)```'
    matches = re.findall(pattern, reply, re.DOTALL)
    if not matches:
        # 也接受回复本身就是代码的情况
        return False, "未找到代码块"
    for code in matches:
        try:
            compile(code.strip(), "<test>", "exec")
        except SyntaxError as e:
            return False, f"语法错误: {e}"
    return True, f"语法正确（{len(matches)} 个代码块）"
