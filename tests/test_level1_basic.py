"""Lv1 简单测试 — 基础问答、简单工具调用、基本指令遵循"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import TestSuite, say, clear_session, check_contains, check_exact_number


def run() -> TestSuite:
    suite = TestSuite("基础问答与指令遵循", level=1)
    clear_session()

    # ── 1.1 简单事实问答 ──
    print("\n\033[1;33m[1.1] 简单事实问答\033[0m")

    reply = say("中国的首都是哪个城市？只回答城市名")
    ok, detail = check_contains(reply, ["北京", "Beijing"])
    if ok:
        suite.ok("中国首都", detail)
    else:
        suite.fail("中国首都", detail)
    clear_session()

    reply = say("水的化学式是什么？只回答化学式")
    ok, detail = check_contains(reply, ["H2O", "h2o", "H₂O"])
    if ok:
        suite.ok("水的化学式", detail)
    else:
        suite.fail("水的化学式", detail)
    clear_session()

    reply = say("光速大约是多少米每秒？只回答数字")
    ok, detail = check_contains(reply, ["3", "300000000", "299792458"])
    if ok:
        suite.ok("光速数值", detail)
    else:
        suite.fail("光速数值", detail)
    clear_session()

    # ── 1.2 简单计算 ──
    print("\n\033[1;33m[1.2] 简单计算\033[0m")

    reply = say("计算 37 × 28 = ？只回答数字")
    ok, detail = check_exact_number(reply, 1036)
    if ok:
        suite.ok("37×28", detail)
    else:
        suite.fail("37×28", detail)
    clear_session()

    reply = say("计算 1234 + 5678 = ？只回答数字")
    ok, detail = check_exact_number(reply, 6912)
    if ok:
        suite.ok("1234+5678", detail)
    else:
        suite.fail("1234+5678", detail)
    clear_session()

    # ── 1.3 格式遵循 ──
    print("\n\033[1;33m[1.3] 格式遵循\033[0m")

    reply = say("列出3种常见编程语言，用编号列表，每行一个，不要其他说明")
    lines = [l.strip() for l in reply.split("\n") if l.strip()]
    # 检查是否有编号格式
    numbered = [l for l in lines if l and (l[0].isdigit() or l.startswith("-"))]
    if len(numbered) >= 3:
        suite.ok("编号列表格式", f"{len(numbered)} 项")
    else:
        suite.fail("编号列表格式", f"只找到 {len(numbered)} 个编号项，回复: {reply[:150]}")
    clear_session()

    reply = say("用JSON格式回复，包含name和age两个字段，name是Alice，age是30。只输出JSON，不要其他文字")
    ok, detail = check_contains(reply, ['"name"', '"age"'])
    if ok:
        suite.ok("JSON 格式输出", detail)
    else:
        suite.fail("JSON 格式输出", detail)
    clear_session()

    # ── 1.4 多语言理解 ──
    print("\n\033[1;33m[1.4] 多语言理解\033[0m")

    reply = say("Translate '你好世界' to English, reply with the translation only")
    ok, detail = check_contains(reply, ["hello world", "Hello World", "Hello, World"])
    if ok:
        suite.ok("中译英", detail)
    else:
        suite.fail("中译英", detail)
    clear_session()

    reply = say("What is 'artificial intelligence' in Chinese? Reply with Chinese only")
    ok, detail = check_contains(reply, ["人工智能"])
    if ok:
        suite.ok("英译中", detail)
    else:
        suite.fail("英译中", detail)
    clear_session()

    return suite


if __name__ == "__main__":
    result = run()
    print(f"\n{result.summary()}")
    sys.exit(result.failed)
