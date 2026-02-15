#!/usr/bin/env python3
"""运行所有测试套件 — 从简单到专家级"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import TestSuite, clear_session


def main() -> int:
    parser = argparse.ArgumentParser(description="灵雀 LLM 能力测试")
    parser.add_argument(
        "--level", "-l", type=int, choices=[0, 1, 2, 3, 4, 5], default=0,
        help="运行指定级别的测试（0=全部, 1=简单, 2=中等, 3=困难, 4=专家, 5=大型项目）",
    )
    parser.add_argument(
        "--infra", action="store_true",
        help="运行基础设施测试（不调用 LLM）",
    )
    args = parser.parse_args()

    suites: list[TestSuite] = []
    total_start = time.time()

    # 基础设施测试
    if args.infra or args.level == 0:
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv0] 基础设施测试\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        import test_infrastructure
        import asyncio
        exit_code = asyncio.run(test_infrastructure.main())
        infra_suite = TestSuite("基础设施", level=0)
        # test_infrastructure 有自己的计数，这里只记录是否通过
        if exit_code == 0:
            infra_suite.ok("全部基础设施测试通过")
        else:
            infra_suite.fail(f"基础设施测试有 {exit_code} 个失败")
        suites.append(infra_suite)

    # Lv1 简单测试
    if args.level in (0, 1):
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv1] 简单测试 — 基础问答\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        from test_level1_basic import run as run_lv1
        suites.append(run_lv1())

    # Lv2 中等测试
    if args.level in (0, 2):
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv2] 中等测试 — 多步推理\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        from test_level2_reasoning import run as run_lv2
        suites.append(run_lv2())

    # Lv3 困难测试
    if args.level in (0, 3):
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv3] 困难测试 — 代码生成\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        from test_level3_coding import run as run_lv3
        suites.append(run_lv3())

    # Lv4 专家测试
    if args.level in (0, 4):
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv4] 专家测试 — 端到端任务\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        from test_level4_complex import run as run_lv4
        suites.append(run_lv4())

    # Lv5 大型项目测试
    if args.level in (0, 5):
        print("\n\033[1;35m" + "=" * 60 + "\033[0m")
        print("\033[1;35m  [Lv5] 大型项目测试 — 构建与部署\033[0m")
        print("\033[1;35m" + "=" * 60 + "\033[0m")
        from test_level5_project import run as run_lv5
        suites.append(run_lv5())

    # ── 总结 ──
    elapsed = time.time() - total_start
    print("\n\033[1;35m" + "=" * 60 + "\033[0m")
    print("\033[1;35m  测试总结\033[0m")
    print("\033[1;35m" + "=" * 60 + "\033[0m\n")

    total_pass = 0
    total_fail = 0
    for s in suites:
        print(f"  {s.summary()}")
        total_pass += s.passed
        total_fail += s.failed

    total = total_pass + total_fail
    print()
    if total_fail == 0:
        print(f"\033[1;32m  总计: 全部通过 {total_pass}/{total} ✓\033[0m")
    else:
        print(f"\033[1;31m  总计: 通过 {total_pass}/{total}，失败 {total_fail}\033[0m")
    print(f"\033[0;36m  耗时: {elapsed:.1f}s\033[0m\n")

    return total_fail


if __name__ == "__main__":
    sys.exit(main())
