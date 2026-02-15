"""Lv5 大型项目测试 — 完整项目构建、部署、端到端验证

测试 LLM 的全栈工程能力：
  5.1 REST API 服务：构建 → 启动 → CRUD 验证 → 关停
  5.2 数据处理管线：生成数据 → ETL → 输出报告 → 验证正确性
  5.3 多文件 CLI 工具：构建项目 → 运行命令 → 验证输出
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from harness import TestSuite, say, clear_session


# ── 辅助函数 ──

PROJECT_BASE = Path("/tmp/lq_test_projects")


def _clean_project(name: str) -> Path:
    """清理并返回项目目录"""
    project_dir = PROJECT_BASE / name
    if project_dir.exists():
        shutil.rmtree(project_dir)
    project_dir.mkdir(parents=True, exist_ok=True)
    return project_dir


def _find_free_port() -> int:
    """找一个空闲端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def _wait_for_server(port: int, timeout: int = 15) -> bool:
    """等待服务器启动"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1)
                s.connect(("127.0.0.1", port))
                return True
        except (ConnectionRefusedError, OSError):
            time.sleep(0.5)
    return False


def _http(method: str, url: str, data: dict | None = None, timeout: int = 10) -> tuple[int, str]:
    """发送 HTTP 请求，返回 (状态码, 响应体)"""
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url, data=body, method=method,
        headers={"Content-Type": "application/json"} if body else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode() if e.fp else ""
    except Exception as e:
        return 0, str(e)


def _files_exist(base: Path, patterns: list[str]) -> tuple[list[str], list[str]]:
    """检查文件是否存在，返回 (存在的, 缺失的)"""
    found, missing = [], []
    for p in patterns:
        matches = list(base.glob(p))
        if matches:
            found.append(p)
        else:
            missing.append(p)
    return found, missing


# ═══════════════════════════════════════════════════════════
#  5.1  REST API 服务 — 完整构建 + 部署 + CRUD 验证
# ═══════════════════════════════════════════════════════════

def _test_rest_api(suite: TestSuite) -> None:
    print("\n\033[1;33m[5.1] REST API 项目 — 构建与部署\033[0m")

    port = _find_free_port()
    project_dir = _clean_project("rest_api")
    clear_session()

    # ── Step 1: 让 LLM 构建项目 ──
    reply = say(
        f"请帮我构建一个完整的 REST API 项目，要求如下：\n"
        f"\n"
        f"1. 项目目录: {project_dir}\n"
        f"2. 只使用 Python 标准库（http.server + json + sqlite3），不安装任何第三方包\n"
        f"3. 实现一个「任务管理」API，资源路径为 /api/tasks\n"
        f"4. 支持完整 CRUD：\n"
        f"   - GET /api/tasks → 列出所有任务\n"
        f"   - POST /api/tasks → 创建任务（body: {{\"title\": \"...\", \"description\": \"...\"}}）\n"
        f"   - GET /api/tasks/{{id}} → 获取单个任务\n"
        f"   - PUT /api/tasks/{{id}} → 更新任务\n"
        f"   - DELETE /api/tasks/{{id}} → 删除任务\n"
        f"5. 数据存储在 SQLite 数据库中（{project_dir}/tasks.db）\n"
        f"6. 返回 JSON 格式，状态码正确（201 创建，404 不存在，200 成功等）\n"
        f"7. 服务器监听端口 {port}\n"
        f"8. 入口文件为 {project_dir}/server.py\n"
        f"\n"
        f"请用 write_file 工具创建所有文件，确保代码完整可运行。",
        timeout=180,
    )

    # ── Step 2: 验证文件生成 ──
    server_file = project_dir / "server.py"
    if not server_file.exists():
        # 也查找子目录
        candidates = list(project_dir.rglob("server.py"))
        if candidates:
            server_file = candidates[0]

    if server_file.exists():
        suite.ok("项目文件生成", f"server.py at {server_file}")
    else:
        suite.fail("项目文件生成", f"server.py 未找到，目录内容: {list(project_dir.rglob('*'))}")
        return

    # 验证语法
    try:
        code = server_file.read_text()
        compile(code, str(server_file), "exec")
        suite.ok("server.py 语法检查")
    except SyntaxError as e:
        suite.fail("server.py 语法检查", str(e))
        return

    # ── Step 3: 启动服务器 ──
    proc = subprocess.Popen(
        [sys.executable, str(server_file)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        cwd=str(project_dir),
    )

    if _wait_for_server(port):
        suite.ok("服务器启动", f"端口 {port}")
    else:
        stderr = proc.stderr.read().decode()[:500] if proc.stderr else ""
        suite.fail("服务器启动", f"端口 {port} 无响应，stderr: {stderr}")
        proc.kill()
        return

    base_url = f"http://127.0.0.1:{port}/api/tasks"

    try:
        # ── Step 4: CRUD 测试 ──

        # CREATE
        status, body = _http("POST", base_url, {"title": "买牛奶", "description": "去超市买2升牛奶"})
        if status in (200, 201):
            try:
                data = json.loads(body)
                task_id = data.get("id") or data.get("task", {}).get("id")
                if task_id is not None:
                    suite.ok("POST 创建任务", f"id={task_id}, status={status}")
                else:
                    # 尝试从 response 提取 id
                    task_id = 1  # 假设第一个任务 id 为 1
                    suite.ok("POST 创建任务（无 id 返回）", f"status={status}")
            except json.JSONDecodeError:
                task_id = 1
                suite.ok("POST 创建任务（非JSON响应）", f"status={status}")
        else:
            suite.fail("POST 创建任务", f"status={status}, body={body[:200]}")
            task_id = None

        # 创建第二个任务
        _http("POST", base_url, {"title": "写代码", "description": "完成项目重构"})

        # LIST
        status, body = _http("GET", base_url)
        if status == 200:
            try:
                data = json.loads(body)
                items = data if isinstance(data, list) else data.get("tasks", data.get("data", []))
                if len(items) >= 2:
                    suite.ok("GET 列出任务", f"{len(items)} 个任务")
                else:
                    suite.fail("GET 列出任务", f"只有 {len(items)} 个任务，期望 >=2")
            except json.JSONDecodeError:
                suite.fail("GET 列出任务", f"JSON 解析失败: {body[:200]}")
        else:
            suite.fail("GET 列出任务", f"status={status}")

        # READ single
        if task_id is not None:
            status, body = _http("GET", f"{base_url}/{task_id}")
            if status == 200 and "买牛奶" in body:
                suite.ok("GET 单个任务", f"status={status}")
            elif status == 200:
                suite.ok("GET 单个任务（内容不完全匹配）", f"status={status}")
            else:
                suite.fail("GET 单个任务", f"status={status}, body={body[:200]}")

        # UPDATE
        if task_id is not None:
            status, body = _http("PUT", f"{base_url}/{task_id}",
                                 {"title": "买有机牛奶", "description": "去全食超市买"})
            if status == 200:
                suite.ok("PUT 更新任务", f"status={status}")
            else:
                suite.fail("PUT 更新任务", f"status={status}, body={body[:200]}")

            # 验证更新
            status, body = _http("GET", f"{base_url}/{task_id}")
            if status == 200 and "有机" in body:
                suite.ok("更新验证（回读）")
            elif status == 200:
                suite.ok("更新验证（状态码正确，内容可能未反映）")
            else:
                suite.fail("更新验证", f"status={status}")

        # DELETE
        if task_id is not None:
            status, body = _http("DELETE", f"{base_url}/{task_id}")
            if status in (200, 204):
                suite.ok("DELETE 删除任务", f"status={status}")
            else:
                suite.fail("DELETE 删除任务", f"status={status}")

            # 验证删除：再次获取应该 404
            status, body = _http("GET", f"{base_url}/{task_id}")
            if status == 404:
                suite.ok("删除验证（404 确认）")
            elif status == 200 and not body.strip():
                suite.ok("删除验证（空响应）")
            else:
                suite.fail("删除验证", f"status={status}，期望 404")

        # 404 test
        status, body = _http("GET", f"{base_url}/99999")
        if status == 404:
            suite.ok("不存在资源返回 404")
        else:
            suite.fail("不存在资源返回 404", f"实际 status={status}")

    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


# ═══════════════════════════════════════════════════════════
#  5.2  数据处理管线 — 生成 → ETL → 分析 → 报告
# ═══════════════════════════════════════════════════════════

def _test_data_pipeline(suite: TestSuite) -> None:
    print("\n\033[1;33m[5.2] 数据处理管线 — 构建与执行\033[0m")

    project_dir = _clean_project("data_pipeline")
    clear_session()

    reply = say(
        f"请帮我构建一个完整的数据处理管线项目：\n"
        f"\n"
        f"项目目录: {project_dir}\n"
        f"\n"
        f"项目需求：模拟一个「电商订单分析系统」\n"
        f"\n"
        f"步骤 1 — 数据生成（{project_dir}/generate_data.py）：\n"
        f"  用 random（seed=42）生成 500 条订单数据，保存为 CSV（{project_dir}/raw_orders.csv）\n"
        f"  字段: order_id, customer_id, product, category, quantity, unit_price, order_date\n"
        f"  - customer_id 范围 1~50\n"
        f"  - category 从 ['电子产品','服装','食品','图书','家居'] 中选\n"
        f"  - quantity 1~10, unit_price 10~500\n"
        f"  - order_date 在 2024-01-01 到 2024-12-31 之间随机\n"
        f"\n"
        f"步骤 2 — ETL 清洗（{project_dir}/etl.py）：\n"
        f"  读取 raw_orders.csv → 增加 total_amount = quantity * unit_price\n"
        f"  → 增加 month 列 → 保存为 {project_dir}/clean_orders.csv\n"
        f"\n"
        f"步骤 3 — 分析报告（{project_dir}/analyze.py）：\n"
        f"  读取 clean_orders.csv，生成分析报告 {project_dir}/report.json，包含：\n"
        f"  - total_orders: 总订单数\n"
        f"  - total_revenue: 总收入\n"
        f"  - avg_order_value: 平均订单金额\n"
        f"  - top_category: 收入最高的类别\n"
        f"  - monthly_revenue: 每月收入（字典）\n"
        f"  - top_customers: 消费最高的前5个客户ID及其总消费\n"
        f"\n"
        f"步骤 4 — 主入口（{project_dir}/main.py）：\n"
        f"  依次运行以上三步，最后打印报告摘要\n"
        f"\n"
        f"只使用 Python 标准库（csv, json, random, datetime 等），不用 pandas。\n"
        f"请用 write_file 创建所有文件。",
        timeout=180,
    )

    # ── 验证文件生成 ──
    expected_files = ["main.py", "generate_data.py", "etl.py", "analyze.py"]
    found, missing = _files_exist(project_dir, expected_files)
    if not missing:
        suite.ok("管线文件全部生成", f"{len(found)} 个文件")
    elif found:
        suite.ok("管线文件部分生成", f"生成 {len(found)}/{len(expected_files)}，缺失: {missing}")
    else:
        suite.fail("管线文件生成", f"全部缺失，目录: {list(project_dir.rglob('*'))}")
        return

    # ── 运行主入口 ──
    main_file = project_dir / "main.py"
    if not main_file.exists():
        # 尝试找替代入口
        candidates = list(project_dir.rglob("main.py"))
        if candidates:
            main_file = candidates[0]
        else:
            suite.fail("主入口不存在")
            return

    result = subprocess.run(
        [sys.executable, str(main_file)],
        capture_output=True, text=True, timeout=30,
        cwd=str(project_dir),
    )

    if result.returncode == 0:
        suite.ok("管线执行成功", f"stdout: {result.stdout[:150]}")
    else:
        suite.fail("管线执行失败", f"exit={result.returncode}, stderr: {result.stderr[:300]}")
        return

    # ── 验证 CSV 生成 ──
    raw_csv = project_dir / "raw_orders.csv"
    if raw_csv.exists():
        lines = raw_csv.read_text().strip().split("\n")
        # 501 = 1 header + 500 data rows
        if len(lines) >= 100:
            suite.ok("原始数据生成", f"{len(lines) - 1} 条订单")
        else:
            suite.fail("原始数据生成", f"只有 {len(lines) - 1} 条")
    else:
        suite.fail("原始数据 CSV 未生成")

    clean_csv = project_dir / "clean_orders.csv"
    if clean_csv.exists():
        header = clean_csv.read_text().split("\n")[0].lower()
        has_total = "total" in header or "amount" in header
        has_month = "month" in header
        if has_total and has_month:
            suite.ok("ETL 清洗完成", "含 total_amount 和 month 列")
        elif has_total or has_month:
            suite.ok("ETL 清洗部分完成", f"total={has_total}, month={has_month}")
        else:
            suite.fail("ETL 清洗", f"缺少增量列，header: {header[:200]}")
    else:
        suite.fail("清洗后 CSV 未生成")

    # ── 验证分析报告 ──
    report_file = project_dir / "report.json"
    if report_file.exists():
        try:
            report = json.loads(report_file.read_text())

            # 检查必需字段
            required = ["total_orders", "total_revenue", "avg_order_value",
                        "top_category", "monthly_revenue", "top_customers"]
            present = [k for k in required if k in report]
            absent = [k for k in required if k not in report]

            if len(present) >= 5:
                suite.ok("分析报告字段完整", f"{len(present)}/{len(required)} 字段")
            elif len(present) >= 3:
                suite.ok("分析报告部分完整", f"有: {present}, 缺: {absent}")
            else:
                suite.fail("分析报告字段不足", f"只有 {present}")

            # 验证数据合理性
            total = report.get("total_orders", 0)
            if 400 <= total <= 600:
                suite.ok("订单总数合理", f"{total} 条")
            else:
                suite.fail("订单总数异常", f"{total} 条（期望 ~500）")

            revenue = report.get("total_revenue", 0)
            if revenue > 0:
                suite.ok("总收入计算", f"¥{revenue:,.0f}")
            else:
                suite.fail("总收入为零或缺失")

            category = report.get("top_category", "")
            valid_cats = ["电子产品", "服装", "食品", "图书", "家居"]
            if category in valid_cats:
                suite.ok("最高收入类别", category)
            elif category:
                suite.ok("最高收入类别（自定义名称）", category)
            else:
                suite.fail("最高收入类别缺失")

            monthly = report.get("monthly_revenue", {})
            if len(monthly) >= 10:
                suite.ok("月度收入分析", f"覆盖 {len(monthly)} 个月")
            elif monthly:
                suite.ok("月度收入分析（部分）", f"覆盖 {len(monthly)} 个月")
            else:
                suite.fail("月度收入分析缺失")

            top_cust = report.get("top_customers", [])
            if len(top_cust) >= 5:
                suite.ok("Top 客户分析", f"{len(top_cust)} 个客户")
            elif top_cust:
                suite.ok("Top 客户分析（不足5个）", f"{len(top_cust)} 个客户")
            else:
                suite.fail("Top 客户分析缺失")

        except json.JSONDecodeError as e:
            suite.fail("分析报告 JSON 解析失败", str(e))
    else:
        suite.fail("分析报告未生成")


# ═══════════════════════════════════════════════════════════
#  5.3  多文件 CLI 项目 — 构建 + 运行 + 验证
# ═══════════════════════════════════════════════════════════

def _test_cli_project(suite: TestSuite) -> None:
    print("\n\033[1;33m[5.3] CLI 项目 — Markdown 转 HTML 工具\033[0m")

    project_dir = _clean_project("md_converter")
    clear_session()

    reply = say(
        f"请帮我构建一个完整的 Markdown 转 HTML 命令行工具：\n"
        f"\n"
        f"项目目录: {project_dir}\n"
        f"\n"
        f"项目结构：\n"
        f"  {project_dir}/\n"
        f"    converter.py      — 核心转换逻辑（Markdown → HTML）\n"
        f"    cli.py            — 命令行入口，接受参数\n"
        f"    templates.py      — HTML 模板（页头页脚）\n"
        f"    test_samples/     — 测试用 Markdown 文件目录\n"
        f"      sample1.md     — 包含标题、段落、列表、代码块\n"
        f"      sample2.md     — 包含链接、加粗、斜体、引用\n"
        f"      sample3.md     — 包含表格、分割线、多级标题\n"
        f"\n"
        f"功能要求：\n"
        f"  1. converter.py 实现 Markdown 核心语法转换：\n"
        f"     - # ## ### 标题 → <h1> <h2> <h3>\n"
        f"     - **粗体** → <strong>，*斜体* → <em>\n"
        f"     - - 或 * 无序列表 → <ul><li>\n"
        f"     - 1. 有序列表 → <ol><li>\n"
        f"     - ```代码块``` → <pre><code>\n"
        f"     - > 引用 → <blockquote>\n"
        f"     - [文字](链接) → <a href>\n"
        f"     - --- → <hr>\n"
        f"     - 段落用 <p> 包裹\n"
        f"  2. cli.py 用法: python cli.py input_dir output_dir\n"
        f"     遍历 input_dir 下所有 .md 文件，转换为 .html 保存到 output_dir\n"
        f"     并生成 index.html 索引页（包含所有转换文件的链接）\n"
        f"  3. templates.py 提供完整 HTML 模板（含 <html><head><body> 等）\n"
        f"\n"
        f"只使用 Python 标准库。请用 write_file 创建所有文件。",
        timeout=180,
    )

    # ── 验证文件结构 ──
    expected = ["converter.py", "cli.py", "templates.py",
                "test_samples/*.md"]
    found, missing = _files_exist(project_dir, expected)

    # 也检查直接的 .md 文件
    md_files = list(project_dir.rglob("*.md"))
    py_files = list(project_dir.rglob("*.py"))

    if len(py_files) >= 3 and len(md_files) >= 2:
        suite.ok("项目文件生成", f"{len(py_files)} 个 .py, {len(md_files)} 个 .md")
    elif len(py_files) >= 2:
        suite.ok("项目文件部分生成", f"{len(py_files)} 个 .py, {len(md_files)} 个 .md")
    else:
        suite.fail("项目文件不足", f"py={len(py_files)}, md={len(md_files)}")
        return

    # ── 语法检查 ──
    syntax_ok = True
    for pf in py_files:
        try:
            compile(pf.read_text(), str(pf), "exec")
        except SyntaxError as e:
            suite.fail(f"语法错误: {pf.name}", str(e))
            syntax_ok = False

    if syntax_ok:
        suite.ok("所有 Python 文件语法正确")

    # ── 运行转换 ──
    cli_file = project_dir / "cli.py"
    if not cli_file.exists():
        candidates = list(project_dir.rglob("cli.py"))
        if candidates:
            cli_file = candidates[0]
        else:
            suite.fail("cli.py 未找到")
            return

    # 找 input 目录
    input_dir = project_dir / "test_samples"
    if not input_dir.exists():
        # 尝试其他可能的名称
        for name in ["samples", "input", "docs", "markdown", "test"]:
            candidate = project_dir / name
            if candidate.exists() and list(candidate.glob("*.md")):
                input_dir = candidate
                break
        else:
            # 如果没有专门的目录，用包含 .md 文件的目录
            if md_files:
                input_dir = md_files[0].parent
            else:
                suite.fail("找不到 Markdown 输入目录")
                return

    output_dir = project_dir / "html_output"
    output_dir.mkdir(exist_ok=True)

    result = subprocess.run(
        [sys.executable, str(cli_file), str(input_dir), str(output_dir)],
        capture_output=True, text=True, timeout=30,
        cwd=str(project_dir),
    )

    if result.returncode == 0:
        suite.ok("CLI 执行成功")
    else:
        suite.fail("CLI 执行失败", f"exit={result.returncode}, stderr: {result.stderr[:300]}")
        return

    # ── 验证 HTML 输出 ──
    html_files = list(output_dir.glob("*.html"))
    if len(html_files) >= 2:
        suite.ok("HTML 文件生成", f"{len(html_files)} 个文件")
    elif html_files:
        suite.ok("HTML 文件生成（较少）", f"只有 {len(html_files)} 个")
    else:
        suite.fail("HTML 文件未生成", f"output_dir 内容: {list(output_dir.iterdir())}")
        return

    # 检查 HTML 内容质量
    valid_html_count = 0
    for hf in html_files:
        content = hf.read_text()
        has_html_tag = "<html" in content.lower() or "<!doctype" in content.lower()
        has_body = "<body" in content.lower()
        has_content = len(content) > 100

        if has_content:
            valid_html_count += 1

    if valid_html_count == len(html_files):
        suite.ok("HTML 内容有效", f"全部 {valid_html_count} 个文件有效")
    elif valid_html_count > 0:
        suite.ok("HTML 内容部分有效", f"{valid_html_count}/{len(html_files)} 有效")
    else:
        suite.fail("HTML 内容无效")

    # 检查索引页
    index_file = output_dir / "index.html"
    if index_file.exists():
        index_content = index_file.read_text()
        # 检查是否包含到其他 HTML 文件的链接
        link_count = index_content.lower().count("<a ")
        if link_count >= 2:
            suite.ok("索引页包含链接", f"{link_count} 个链接")
        elif link_count >= 1:
            suite.ok("索引页有链接（较少）", f"{link_count} 个链接")
        else:
            suite.fail("索引页缺少链接")
    else:
        suite.fail("索引页 index.html 未生成")

    # 检查 Markdown 语法转换质量
    # 随机检查一个 HTML 文件的转换质量
    for hf in html_files:
        if hf.name == "index.html":
            continue
        content = hf.read_text()
        checks = {
            "标题转换": any(f"<h{i}" in content.lower() for i in range(1, 4)),
            "段落标签": "<p" in content.lower(),
            "格式标签": any(tag in content.lower() for tag in ["<strong", "<em", "<b>", "<i>"]),
        }
        passed = [k for k, v in checks.items() if v]
        if len(passed) >= 2:
            suite.ok("Markdown 转换质量", f"通过: {', '.join(passed)}")
        elif passed:
            suite.ok("Markdown 转换质量（基本）", f"通过: {', '.join(passed)}")
        else:
            suite.fail("Markdown 转换质量差", f"content 前300字: {content[:300]}")
        break  # 只检查一个文件


# ═══════════════════════════════════════════════════════════
#  入口
# ═══════════════════════════════════════════════════════════

def run() -> TestSuite:
    suite = TestSuite("大型项目构建与部署", level=5)

    _test_rest_api(suite)
    _test_data_pipeline(suite)
    _test_cli_project(suite)

    # 清理
    if PROJECT_BASE.exists():
        shutil.rmtree(PROJECT_BASE, ignore_errors=True)

    return suite


if __name__ == "__main__":
    result = run()
    print(f"\n{result.summary()}")
    sys.exit(result.failed)
