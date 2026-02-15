"""Lv2 中等测试 — 多步推理、数学计算、逻辑分析、工具组合使用"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import (
    TestSuite, say, clear_session,
    check_contains, check_contains_all, check_exact_number,
    check_number_in_range,
)


def run() -> TestSuite:
    suite = TestSuite("多步推理与计算", level=2)
    clear_session()

    # ── 2.1 多步数学计算 ──
    print("\n\033[1;33m[2.1] 多步数学计算\033[0m")

    reply = say(
        "一个商店打折促销，原价200元的商品先打8折，再用满100减20的优惠券，最终价格是多少元？"
        "请列出计算步骤，最后给出答案。"
    )
    # 200 * 0.8 = 160, 160 - 20 = 140
    ok, detail = check_exact_number(reply, 140)
    if ok:
        suite.ok("多步折扣计算", detail)
    else:
        suite.fail("多步折扣计算", detail)
    clear_session()

    reply = say(
        "一个水池有两个进水管和一个出水管。"
        "进水管A每小时注入12升，进水管B每小时注入8升，出水管每小时排出5升。"
        "水池容量是150升，从空池开始，需要多少小时才能注满？"
        "请给出精确答案。"
    )
    # (12 + 8 - 5) = 15 升/小时, 150 / 15 = 10 小时
    ok, detail = check_exact_number(reply, 10)
    if ok:
        suite.ok("水池问题", detail)
    else:
        suite.fail("水池问题", detail)
    clear_session()

    # ── 2.2 使用 run_python 进行精确计算 ──
    print("\n\033[1;33m[2.2] 工具辅助计算（期望使用 run_python）\033[0m")

    reply = say(
        "请用Python精确计算：2的100次方是多少？直接告诉我结果数字。"
    )
    # 2^100 = 1267650600228229401496703205376
    ok, detail = check_contains(reply, ["1267650600228229401496703205376"])
    if ok:
        suite.ok("2^100 精确计算", detail)
    else:
        suite.fail("2^100 精确计算", detail)
    clear_session()

    reply = say(
        "请计算斐波那契数列的第50项是多少？要精确值，建议用Python计算。"
    )
    # fib(50) = 12586269025
    ok, detail = check_exact_number(reply, 12586269025)
    if ok:
        suite.ok("斐波那契第50项", detail)
    else:
        suite.fail("斐波那契第50项", detail)
    clear_session()

    # ── 2.3 逻辑推理 ──
    print("\n\033[1;33m[2.3] 逻辑推理\033[0m")

    reply = say(
        "有三个人：张三、李四、王五。已知：\n"
        "1. 张三比李四大2岁\n"
        "2. 王五比张三小5岁\n"
        "3. 三人年龄之和是74岁\n"
        "请问李四多少岁？只回答数字。"
    )
    # 设李四=x, 张三=x+2, 王五=x+2-5=x-3
    # x + (x+2) + (x-3) = 3x - 1 = 74 → x = 25
    # 验证: 张三=27, 王五=22, 和=25+27+22=74 ✓
    ok, detail = check_exact_number(reply, 25)
    if ok:
        suite.ok("年龄推理", detail)
    else:
        suite.fail("年龄推理", detail)
    clear_session()

    reply = say(
        "一个房间里有5盏灯，都是关着的。"
        "我依次做了以下操作：\n"
        "1. 打开第1、3、5盏灯\n"
        "2. 切换第2、3、4盏灯的状态（开→关，关→开）\n"
        "3. 关闭所有奇数编号的灯\n"
        "最终哪些灯是亮着的？只回答灯的编号。"
    )
    # 初始: [关,关,关,关,关]
    # 操作1后: [开,关,开,关,开]
    # 操作2后(切换2,3,4): [开,开,关,开,开]
    # 操作3后(关闭1,3,5): [关,开,关,开,关]
    # 亮着的: 2, 4
    ok, detail = check_contains_all(reply, ["2", "4"])
    if ok:
        suite.ok("灯开关逻辑", detail)
    else:
        suite.fail("灯开关逻辑", detail)
    clear_session()

    # ── 2.4 数据分析（使用 run_python）──
    print("\n\033[1;33m[2.4] 数据分析\033[0m")

    reply = say(
        "请用Python帮我分析以下数据，计算平均值、中位数和标准差：\n"
        "[23, 45, 67, 12, 89, 34, 56, 78, 43, 21, 65, 87, 32, 54, 76]\n"
        "给出精确结果，保留2位小数。"
    )
    # mean = 52.13, median = 54, std ≈ 23.48 (population) or 24.30 (sample)
    ok1, _ = check_number_in_range(reply, 52, 53)       # 均值
    ok2, _ = check_contains(reply, ["54"])               # 中位数
    ok3, _ = check_number_in_range(reply, 23, 25)        # 标准差
    if ok1 and ok2:
        suite.ok("数据统计分析", "均值和中位数正确")
    elif ok1 or ok2:
        suite.ok("数据统计分析（部分正确）", f"均值={ok1}, 中位数={ok2}, 标准差={ok3}")
    else:
        suite.fail("数据统计分析", f"回复: {reply[:200]}")
    clear_session()

    # ── 2.5 联网搜索能力 ──
    print("\n\033[1;33m[2.5] 联网搜索\033[0m")

    reply = say("搜索一下Python 3.12有什么新特性，简要列出3个")
    ok, detail = check_contains(reply, ["python", "3.12", "Python"])
    if ok and len(reply) > 50:
        suite.ok("联网搜索 Python 特性", f"回复长度: {len(reply)}")
    else:
        suite.fail("联网搜索 Python 特性", f"回复: {reply[:200]}")
    clear_session()

    return suite


if __name__ == "__main__":
    result = run()
    print(f"\n{result.summary()}")
    sys.exit(result.failed)
