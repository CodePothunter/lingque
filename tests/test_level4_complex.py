"""Lv4 专家测试 — 多轮对话、工具链组合、端到端复杂任务、Claude Code 调用"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import (
    TestSuite, say, clear_session,
    check_contains, check_contains_all, check_number_in_range,
    check_code_block, check_python_syntax,
)


def _extract_python_code(reply: str) -> str:
    """从回复中提取 Python 代码"""
    pattern = r'```(?:python)?\s*\n(.*?)```'
    matches = re.findall(pattern, reply, re.DOTALL)
    if matches:
        return matches[0].strip()
    return ""


def _run_python_code(code: str, test_code: str = "", timeout: int = 10) -> tuple[bool, str]:
    """运行 Python 代码"""
    full_code = code + "\n" + test_code if test_code else code
    try:
        result = subprocess.run(
            [sys.executable, "-c", full_code],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0:
            return True, result.stdout.strip()
        return False, f"退出码 {result.returncode}: {result.stderr[:300]}"
    except subprocess.TimeoutExpired:
        return False, "执行超时"
    except Exception as e:
        return False, str(e)


def run() -> TestSuite:
    suite = TestSuite("复杂端到端任务", level=4)
    clear_session()

    # ── 4.1 多轮对话保持上下文 ──
    print("\n\033[1;33m[4.1] 多轮对话上下文保持\033[0m")

    reply1 = say("我叫小明，今年25岁，是一名Python工程师。记住这些信息。")
    # 不清除 session，继续对话
    reply2 = say("我刚才告诉你我叫什么名字？")
    ok, detail = check_contains(reply2, ["小明"])
    if ok:
        suite.ok("多轮记忆-姓名", detail)
    else:
        suite.fail("多轮记忆-姓名", detail)

    reply3 = say("我的职业是什么？")
    ok, detail = check_contains(reply3, ["Python", "工程师", "程序员", "开发"])
    if ok:
        suite.ok("多轮记忆-职业", detail)
    else:
        suite.fail("多轮记忆-职业", detail)

    reply4 = say("根据我的年龄，我大概是哪一年出生的？")
    ok, detail = check_contains(reply4, ["2000", "2001"])
    if ok:
        suite.ok("多轮推理-出生年份", detail)
    else:
        suite.fail("多轮推理-出生年份", detail)
    clear_session()

    # ── 4.2 工具链组合：搜索 + 分析 ──
    print("\n\033[1;33m[4.2] 工具链组合\033[0m")

    reply = say(
        "帮我做一个分析任务：\n"
        "1. 先用Python生成一个包含100个1到1000之间随机整数的列表（用seed=42确保可复现）\n"
        "2. 计算这组数据的均值、方差、最大值、最小值\n"
        "3. 找出所有大于均值的数的个数\n"
        "请使用 run_python 工具完成。"
    )
    # seed=42 的 random 结果是确定的
    # 检查是否有合理的统计数据
    ok1, _ = check_contains(reply, ["均值", "平均", "mean"])
    ok2, _ = check_contains(reply, ["方差", "variance", "var"])
    ok3, _ = check_contains(reply, ["最大", "max"])
    ok4, _ = check_contains(reply, ["最小", "min"])
    if ok1 and ok3:
        suite.ok("多步数据分析", "包含统计指标")
    elif ok1 or ok3:
        suite.ok("多步数据分析（部分）", f"均值={ok1}, 方差={ok2}, 最大={ok3}, 最小={ok4}")
    else:
        suite.fail("多步数据分析", f"回复: {reply[:300]}")
    clear_session()

    # ── 4.3 复杂算法：完整项目级代码 ──
    print("\n\033[1;33m[4.3] 复杂算法设计\033[0m")

    reply = say(
        "用Python实现一个完整的 Trie（前缀树）类，要求：\n"
        "1. insert(word): 插入一个单词\n"
        "2. search(word): 查找是否存在完整单词\n"
        "3. starts_with(prefix): 查找是否有以某前缀开头的单词\n"
        "4. count_prefix(prefix): 返回以某前缀开头的单词数量\n"
        "5. delete(word): 删除一个单词\n"
        "6. autocomplete(prefix, limit=5): 返回以某前缀开头的所有单词（最多limit个）\n"
        "类名为 Trie。只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
t = Trie()
t.insert("apple")
t.insert("app")
t.insert("application")
t.insert("apply")
t.insert("banana")

assert t.search("apple") == True
assert t.search("app") == True
assert t.search("ap") == False
assert t.starts_with("app") == True
assert t.starts_with("ban") == True
assert t.starts_with("cat") == False
assert t.count_prefix("app") == 4
assert t.count_prefix("ban") == 1
assert t.count_prefix("z") == 0

completions = t.autocomplete("app")
assert "apple" in completions
assert "app" in completions
assert "application" in completions
assert "apply" in completions
assert len(completions) == 4

t.delete("app")
assert t.search("app") == False
assert t.search("apple") == True
assert t.count_prefix("app") == 3

print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("Trie 前缀树（6个方法）", "所有测试通过")
        else:
            # 检查部分功能
            partial_test = """
t = Trie()
t.insert("apple")
t.insert("app")
assert t.search("apple") == True
assert t.search("app") == True
assert t.starts_with("app") == True
print("PARTIAL_PASS")
"""
            ok2, output2 = _run_python_code(code, partial_test)
            if ok2 and "PARTIAL_PASS" in output2:
                suite.ok("Trie 前缀树（基本功能通过）", f"完整测试失败: {output[:150]}")
            else:
                suite.fail("Trie 前缀树", f"测试失败: {output[:200]}")
    else:
        suite.fail("Trie 前缀树", "未找到代码")
    clear_session()

    # ── 4.4 设计模式与架构 ──
    print("\n\033[1;33m[4.4] 设计模式实现\033[0m")

    reply = say(
        "用Python实现一个事件系统（发布-订阅模式），要求：\n"
        "1. EventBus 类，支持 on(event, callback)、off(event, callback)、emit(event, *args)\n"
        "2. 支持一次性监听 once(event, callback)\n"
        "3. 支持通配符 '*' 监听所有事件\n"
        "4. emit 时如果回调抛出异常，不影响其他回调执行\n"
        "类名为 EventBus。只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
bus = EventBus()
results = []

def handler_a(data):
    results.append(f"a:{data}")

def handler_b(data):
    results.append(f"b:{data}")

def handler_once(data):
    results.append(f"once:{data}")

def handler_all(*args):
    results.append(f"all:{args}")

def handler_error(data):
    raise ValueError("test error")

bus.on("click", handler_a)
bus.on("click", handler_b)
bus.once("click", handler_once)
bus.on("click", handler_error)  # 不应影响其他回调
bus.on("*", handler_all)

bus.emit("click", "first")

assert "a:first" in results
assert "b:first" in results
assert "once:first" in results

# 第二次 emit，once 不应再触发
results.clear()
bus.emit("click", "second")
assert "a:second" in results
assert "b:second" in results
assert "once:second" not in results

# off 测试
bus.off("click", handler_a)
results.clear()
bus.emit("click", "third")
assert "a:third" not in results
assert "b:third" in results

print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("EventBus 发布订阅", "所有测试通过")
        else:
            # 基本功能测试
            basic_test = """
bus = EventBus()
results = []
def h(data): results.append(data)
bus.on("test", h)
bus.emit("test", "hello")
assert "hello" in results
print("BASIC_PASS")
"""
            ok2, output2 = _run_python_code(code, basic_test)
            if ok2 and "BASIC_PASS" in output2:
                suite.ok("EventBus（基本功能通过）", f"完整测试失败: {output[:150]}")
            else:
                suite.fail("EventBus 发布订阅", f"测试失败: {output[:200]}")
    else:
        suite.fail("EventBus 发布订阅", "未找到代码")
    clear_session()

    # ── 4.5 端到端：文件读写 + 代码执行组合 ──
    print("\n\033[1;33m[4.5] 端到端工具组合\033[0m")

    reply = say(
        "请完成以下多步任务：\n"
        "1. 用write_file工具创建文件 /tmp/lq_test_data.csv，内容是一个CSV表格，"
        "包含10行数据，列是 name,score,grade，每行是随机生成的学生数据\n"
        "2. 然后用read_file工具读取这个文件，确认内容\n"
        "3. 最后用run_python工具读取这个CSV文件，计算平均分并输出结果"
    )
    # 验证: 回复中应该包含文件操作结果和平均分
    ok1, _ = check_contains(reply, ["csv", "CSV", "name", "score"])
    ok2, _ = check_contains(reply, ["平均", "average", "均分", "mean"])
    if ok1 and ok2:
        suite.ok("多工具端到端", "文件创建+读取+分析全流程")
    elif ok1:
        suite.ok("多工具端到端（部分）", "文件操作成功，分析可能未完成")
    else:
        suite.fail("多工具端到端", f"回复: {reply[:300]}")
    # 清理
    tmp_file = Path("/tmp/lq_test_data.csv")
    if tmp_file.exists():
        tmp_file.unlink()
    clear_session()

    # ── 4.6 复杂数学建模 ──
    print("\n\033[1;33m[4.6] 复杂数学建模\033[0m")

    reply = say(
        "请用Python解决以下优化问题：\n"
        "一个工厂生产A和B两种产品。\n"
        "每件A产品需要2小时加工、1小时组装，利润50元。\n"
        "每件B产品需要1小时加工、3小时组装，利润40元。\n"
        "每天加工时间最多100小时，组装时间最多90小时。\n"
        "问每天生产多少件A和B可以使利润最大化？\n"
        "请列出约束条件，写代码求解（可以用穷举或线性规划），给出最优解。"
    )
    # 最优解: A=42, B=16, 利润=2740
    # 或用scipy: 2x+y<=100, x+3y<=90, x,y>=0, max 50x+40y
    # 顶点: (0,0),(50,0),(0,30),(42,16) -> 利润2740在(42,16)
    ok1, _ = check_contains(reply, ["42"])
    ok2, _ = check_contains(reply, ["16"])
    ok3, _ = check_contains(reply, ["2740"])
    if ok1 and ok2:
        suite.ok("线性规划", f"A=42, B=16 正确, 利润={ok3}")
    elif ok3:
        suite.ok("线性规划（利润正确）", "最优利润2740")
    else:
        # 也接受合理的近似解
        ok4, _ = check_number_in_range(reply, 2700, 2750)
        if ok4:
            suite.ok("线性规划（近似正确）", "利润在合理范围内")
        else:
            suite.fail("线性规划", f"回复: {reply[:300]}")
    clear_session()

    # ── 4.7 系统设计题（纯推理）──
    print("\n\033[1;33m[4.7] 系统设计推理\033[0m")

    reply = say(
        "请设计一个简化版的短链接服务（URL shortener）的架构。包括：\n"
        "1. 核心算法：如何将长URL映射为短码\n"
        "2. 数据存储方案\n"
        "3. 如何处理高并发\n"
        "4. 如何处理过期链接\n"
        "请给出技术方案，包含具体的技术选型建议。"
    )
    ok1, _ = check_contains(reply, ["hash", "base62", "base64", "编码", "哈希", "自增"])
    ok2, _ = check_contains(reply, ["redis", "Redis", "MySQL", "数据库", "database", "存储"])
    ok3, _ = check_contains(reply, ["并发", "缓存", "cache", "负载", "分布式"])
    ok4, _ = check_contains(reply, ["过期", "TTL", "清理", "expire"])
    score = sum([ok1, ok2, ok3, ok4])
    if score >= 3:
        suite.ok("系统设计", f"覆盖 {score}/4 个方面")
    elif score >= 2:
        suite.ok("系统设计（部分）", f"覆盖 {score}/4 个方面")
    else:
        suite.fail("系统设计", f"只覆盖 {score}/4 个方面，回复: {reply[:300]}")
    clear_session()

    return suite


if __name__ == "__main__":
    result = run()
    print(f"\n{result.summary()}")
    sys.exit(result.failed)
