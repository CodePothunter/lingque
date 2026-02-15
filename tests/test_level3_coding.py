"""Lv3 困难测试 — 代码生成、算法实现、调试纠错"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import (
    TestSuite, say, clear_session,
    check_contains, check_code_block, check_python_syntax,
)


def _extract_python_code(reply: str) -> str:
    """从回复中提取 Python 代码"""
    pattern = r'```(?:python)?\s*\n(.*?)```'
    matches = re.findall(pattern, reply, re.DOTALL)
    if matches:
        return matches[0].strip()
    # 如果没有代码块，尝试找缩进的代码
    lines = reply.split("\n")
    code_lines = [l for l in lines if l.startswith("    ") or l.startswith("def ") or l.startswith("class ")]
    if code_lines:
        return "\n".join(code_lines)
    return ""


def _run_python_code(code: str, test_code: str = "", timeout: int = 10) -> tuple[bool, str]:
    """运行 Python 代码，返回 (成功, 输出)"""
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
    suite = TestSuite("代码生成与算法", level=3)
    clear_session()

    # ── 3.1 基础算法实现 ──
    print("\n\033[1;33m[3.1] 基础算法实现\033[0m")

    reply = say(
        "请写一个Python函数 is_palindrome(s)，判断字符串是否是回文。"
        "要求忽略大小写和非字母数字字符。"
        "只给代码，不要解释。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert is_palindrome("racecar") == True
assert is_palindrome("A man, a plan, a canal: Panama") == True
assert is_palindrome("hello") == False
assert is_palindrome("") == True
assert is_palindrome("Was it a car or a cat I saw?") == True
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("回文判断函数", "所有测试通过")
        else:
            suite.fail("回文判断函数", f"测试失败: {output}")
    else:
        suite.fail("回文判断函数", "未找到代码")
    clear_session()

    reply = say(
        "写一个Python函数 binary_search(arr, target)，实现二分查找。"
        "找到返回索引，找不到返回-1。输入数组已排序。只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert binary_search([1, 3, 5, 7, 9, 11], 7) == 3
assert binary_search([1, 3, 5, 7, 9, 11], 1) == 0
assert binary_search([1, 3, 5, 7, 9, 11], 11) == 5
assert binary_search([1, 3, 5, 7, 9, 11], 6) == -1
assert binary_search([], 5) == -1
assert binary_search([42], 42) == 0
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("二分查找", "所有测试通过")
        else:
            suite.fail("二分查找", f"测试失败: {output}")
    else:
        suite.fail("二分查找", "未找到代码")
    clear_session()

    # ── 3.2 中等难度算法 ──
    print("\n\033[1;33m[3.2] 中等难度算法\033[0m")

    reply = say(
        "写一个Python函数 flatten(nested_list)，将任意深度的嵌套列表展平为一维列表。"
        "例如 flatten([1, [2, [3, 4], 5], [6, 7]]) 返回 [1, 2, 3, 4, 5, 6, 7]。"
        "只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert flatten([1, [2, [3, 4], 5], [6, 7]]) == [1, 2, 3, 4, 5, 6, 7]
assert flatten([]) == []
assert flatten([1, 2, 3]) == [1, 2, 3]
assert flatten([[[[1]]]]) == [1]
assert flatten([1, [2], [[3]], [[[4]]]]) == [1, 2, 3, 4]
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("嵌套列表展平", "所有测试通过")
        else:
            suite.fail("嵌套列表展平", f"测试失败: {output}")
    else:
        suite.fail("嵌套列表展平", "未找到代码")
    clear_session()

    reply = say(
        "写一个Python函数 lru_cache_dict(capacity)，返回一个具有LRU淘汰策略的字典。"
        "这个字典应该支持 get(key) 和 put(key, value) 操作。"
        "当容量满时，删除最久未使用的键值对。"
        "请实现为一个类 LRUCache，构造函数接受 capacity 参数。"
        "只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
cache = LRUCache(2)
cache.put(1, 1)
cache.put(2, 2)
assert cache.get(1) == 1
cache.put(3, 3)
assert cache.get(2) == -1  # 被淘汰
assert cache.get(3) == 3
cache.put(4, 4)
assert cache.get(1) == -1  # 被淘汰
assert cache.get(3) == 3
assert cache.get(4) == 4
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("LRU Cache 实现", "所有测试通过")
        else:
            suite.fail("LRU Cache 实现", f"测试失败: {output}")
    else:
        suite.fail("LRU Cache 实现", "未找到代码")
    clear_session()

    # ── 3.3 高难度算法 ──
    print("\n\033[1;33m[3.3] 高难度算法\033[0m")

    reply = say(
        "写一个Python函数 eval_expr(expression)，"
        "解析并计算包含 +、-、*、/、括号 的数学表达式字符串。"
        "支持整数和浮点数，遵循运算符优先级。不使用 eval()。"
        "例如 eval_expr('3 + 4 * 2 / (1 - 5)') 应返回 1.0。"
        "只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert abs(eval_expr("3 + 4 * 2 / (1 - 5)") - 1.0) < 0.001
assert abs(eval_expr("2 + 3") - 5.0) < 0.001
assert abs(eval_expr("10 - 2 * 3") - 4.0) < 0.001
assert abs(eval_expr("(2 + 3) * 4") - 20.0) < 0.001
assert abs(eval_expr("100 / 4 / 5") - 5.0) < 0.001
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("表达式求值器", "所有测试通过")
        else:
            # 即使测试失败，语法正确也给部分分
            syn_ok, syn_detail = check_python_syntax(reply)
            if syn_ok:
                suite.fail("表达式求值器", f"语法正确但测试失败: {output[:200]}")
            else:
                suite.fail("表达式求值器", f"测试失败: {output[:200]}")
    else:
        suite.fail("表达式求值器", "未找到代码")
    clear_session()

    reply = say(
        "写一个Python函数 longest_common_subsequence(s1, s2)，"
        "使用动态规划求两个字符串的最长公共子序列的长度。"
        "只给代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert longest_common_subsequence("abcde", "ace") == 3
assert longest_common_subsequence("abc", "abc") == 3
assert longest_common_subsequence("abc", "def") == 0
assert longest_common_subsequence("", "abc") == 0
assert longest_common_subsequence("AGGTAB", "GXTXAYB") == 4
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("最长公共子序列 (DP)", "所有测试通过")
        else:
            suite.fail("最长公共子序列 (DP)", f"测试失败: {output}")
    else:
        suite.fail("最长公共子序列 (DP)", "未找到代码")
    clear_session()

    # ── 3.4 代码纠错 ──
    print("\n\033[1;33m[3.4] 代码纠错\033[0m")

    reply = say(
        "以下Python代码有bug，请修复并返回完整的正确代码：\n\n"
        "```python\n"
        "def merge_sort(arr):\n"
        "    if len(arr) <= 1:\n"
        "        return arr\n"
        "    mid = len(arr) // 2\n"
        "    left = merge_sort(arr[:mid])\n"
        "    right = merge_sort(arr[mid:])\n"
        "    return merge(left, right)\n"
        "\n"
        "def merge(left, right):\n"
        "    result = []\n"
        "    i = j = 0\n"
        "    while i < len(left) and j < len(right):\n"
        "        if left[i] <= right[j]:\n"
        "            result.append(left[i])\n"
        "            i += 1\n"
        "        else:\n"
        "            result.append(right[j])\n"
        "            j += 1\n"
        "    # Bug: 缺少处理剩余元素\n"
        "    return result\n"
        "```\n"
        "只给修复后的完整代码。"
    )
    code = _extract_python_code(reply)
    if code:
        test = """
assert merge_sort([3, 1, 4, 1, 5, 9, 2, 6]) == [1, 1, 2, 3, 4, 5, 6, 9]
assert merge_sort([]) == []
assert merge_sort([1]) == [1]
assert merge_sort([5, 4, 3, 2, 1]) == [1, 2, 3, 4, 5]
print("PASS")
"""
        ok, output = _run_python_code(code, test)
        if ok and "PASS" in output:
            suite.ok("修复 merge_sort", "所有测试通过")
        else:
            suite.fail("修复 merge_sort", f"测试失败: {output}")
    else:
        suite.fail("修复 merge_sort", "未找到代码")
    clear_session()

    return suite


if __name__ == "__main__":
    result = run()
    print(f"\n{result.summary()}")
    sys.exit(result.failed)
