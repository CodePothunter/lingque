"""lq say 全链路测试 — 直接测试工具实现和对话基础设施"""

from __future__ import annotations

import asyncio
import json
import sys
import logging
from pathlib import Path

# 设置 PYTHONPATH
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

logging.basicConfig(level=logging.WARNING, format="%(name)s: %(message)s")

HOME = Path.home() / ".lq-test"

# ── 测试计数 ──
_pass = 0
_fail = 0

def ok(name: str, detail: str = ""):
    global _pass
    _pass += 1
    print(f"  \033[1;32m✓\033[0m {name}" + (f"  ({detail})" if detail else ""))

def fail(name: str, detail: str = ""):
    global _fail
    _fail += 1
    print(f"  \033[1;31m✗\033[0m {name}" + (f"  ({detail})" if detail else ""))


async def main():
    from lq.config import LQConfig, load_config
    from lq.conversation import LocalSender, LOCAL_CHAT_ID
    from lq.executor.api import DirectAPIExecutor
    from lq.executor.claude_code import BashExecutor
    from lq.memory import MemoryManager
    from lq.session import SessionManager
    from lq.stats import StatsTracker
    from lq.tools import ToolRegistry
    from lq.router import MessageRouter

    config = load_config(HOME)

    # ╔════════════════════════════════════════════╗
    # ║  1. LocalSender 基础测试                    ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[1] LocalSender 基础测试\033[0m")

    sender = LocalSender("测试bot")
    assert sender.bot_name == "测试bot"
    assert sender.bot_open_id == "local_bot"
    ok("初始化")

    # send_text
    result = await sender.send_text("chat_123", "hello world")
    assert result == "local_msg"
    ok("send_text")

    # reply_text
    result = await sender.reply_text("msg_456", "回复测试")
    assert result == "local_msg"
    ok("reply_text")

    # send_card
    card = {"elements": [{"tag": "markdown", "content": "**卡片内容**"}]}
    result = await sender.send_card("chat_123", card)
    assert result == "local_msg"
    ok("send_card")

    # reply_card
    result = await sender.reply_card("msg_456", card)
    assert result == "local_msg"
    ok("reply_card")

    # get_user_name
    name = await sender.get_user_name("local_cli_user")
    assert name == "用户"
    ok("get_user_name", name)

    # fetch_bot_info
    info = await sender.fetch_bot_info()
    assert info["open_id"] == "local_bot"
    ok("fetch_bot_info", str(info))

    # 其他 stub 方法
    assert not sender.is_chat_left("any")
    assert sender.get_bot_members("any") == set()
    ok("stub 方法 (is_chat_left, get_bot_members)")

    # ╔════════════════════════════════════════════╗
    # ║  2. 组件初始化测试                          ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[2] 组件初始化测试\033[0m")

    memory = MemoryManager(HOME)
    ok("MemoryManager")

    session_mgr = SessionManager(HOME)
    ok("SessionManager")

    stats = StatsTracker(HOME)
    ok("StatsTracker")

    tool_registry = ToolRegistry(HOME)
    tool_registry.load_all()
    ok("ToolRegistry", f"{len(tool_registry.list_tools())} 个工具")

    bash_executor = BashExecutor(HOME)
    ok("BashExecutor")

    # executor 需要 API key，用 placeholder 创建（不调 API）
    executor = DirectAPIExecutor(config.api, config.model)
    ok("DirectAPIExecutor（placeholder key）")

    # ╔════════════════════════════════════════════╗
    # ║  3. Router 初始化 + 工具列表测试            ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[3] Router 初始化 + 工具列表测试\033[0m")

    router = MessageRouter(executor, memory, sender, "local_bot", "测试bot")
    router.session_mgr = session_mgr
    router.stats = stats
    router.bash_executor = bash_executor
    router.tool_registry = tool_registry
    ok("MessageRouter 初始化")

    all_tools = router._build_all_tools()
    tool_names = [t["name"] for t in all_tools]
    ok("_build_all_tools", f"共 {len(all_tools)} 个工具")

    expected_new_tools = ["web_search", "web_fetch", "run_python", "read_file", "write_file"]
    for t in expected_new_tools:
        if t in tool_names:
            ok(f"工具定义存在: {t}")
        else:
            fail(f"工具定义缺失: {t}")

    # ╔════════════════════════════════════════════╗
    # ║  4. run_python 工具测试                     ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[4] run_python 工具测试\033[0m")

    result = await router._tool_run_python("print(2 + 3)")
    if result["success"] and result["output"].strip() == "5":
        ok("简单计算: 2+3=5", result["output"].strip())
    else:
        fail("简单计算", str(result))

    result = await router._tool_run_python("import json; print(json.dumps({'a': 1}))")
    if result["success"] and '"a"' in result["output"]:
        ok("标准库 json", result["output"].strip())
    else:
        fail("标准库 json", str(result))

    result = await router._tool_run_python(
        "from datetime import datetime; print(datetime.now().strftime('%Y'))"
    )
    if result["success"] and "202" in result["output"]:
        ok("datetime 模块", result["output"].strip())
    else:
        fail("datetime 模块", str(result))

    result = await router._tool_run_python("raise ValueError('test error')")
    if not result["success"] and result["exit_code"] != 0:
        ok("异常处理", f"exit_code={result['exit_code']}")
    else:
        fail("异常处理", str(result))

    result = await router._tool_run_python("import time; time.sleep(10)", timeout=2)
    if not result["success"] and "超时" in result["error"]:
        ok("超时保护", result["error"])
    else:
        fail("超时保护", str(result))

    # ╔════════════════════════════════════════════╗
    # ║  5. read_file 工具测试                      ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[5] read_file 工具测试\033[0m")

    result = router._tool_read_file(str(HOME / "SOUL.md"))
    if result["success"] and "测试" in result["content"]:
        ok("读取 SOUL.md", f"{result['lines']} 行, {result['size']} 字节")
    else:
        fail("读取 SOUL.md", str(result))

    result = router._tool_read_file("SOUL.md")
    if result["success"]:
        ok("相对路径解析", f"→ {result['path']}")
    else:
        fail("相对路径解析", str(result))

    result = router._tool_read_file("/nonexistent/file.txt")
    if not result["success"] and "不存在" in result["error"]:
        ok("不存在的文件", result["error"])
    else:
        fail("不存在的文件", str(result))

    result = router._tool_read_file(str(HOME / "SOUL.md"), max_lines=2)
    if result["success"] and result["lines"] <= 2:
        ok("max_lines 限制", f"显示 {result['lines']} 行 / 共 {result['total_lines']} 行")
    else:
        fail("max_lines 限制", str(result))

    # ╔════════════════════════════════════════════╗
    # ║  6. write_file 工具测试                     ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[6] write_file 工具测试\033[0m")

    test_path = HOME / "test_output" / "hello.txt"
    result = router._tool_write_file(str(test_path), "Hello from write_file!\n测试中文内容")
    if result["success"]:
        ok("写入文件（含自动创建目录）", result["message"])
    else:
        fail("写入文件", str(result))

    # 验证写入
    if test_path.exists():
        content = test_path.read_text(encoding="utf-8")
        if "Hello" in content and "测试中文" in content:
            ok("写入内容验证")
        else:
            fail("写入内容验证", content[:100])
    else:
        fail("文件不存在")

    # 用 read_file 回读
    result = router._tool_read_file(str(test_path))
    if result["success"] and "Hello from write_file" in result["content"]:
        ok("read_file 回读验证")
    else:
        fail("read_file 回读", str(result))

    # 相对路径写入
    result = router._tool_write_file("test_output/relative.txt", "relative path test")
    if result["success"]:
        ok("相对路径写入", result["message"])
    else:
        fail("相对路径写入", str(result))

    # ╔════════════════════════════════════════════╗
    # ║  7. web_search 工具测试                     ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[7] web_search 工具测试\033[0m")

    result = await router._tool_web_search("Python programming language", max_results=3)
    if result["success"] and result["count"] > 0:
        ok("搜索 Python", f"{result['count']} 条结果")
        for r in result["results"][:2]:
            print(f"      - {r['title'][:60]}  {r['url'][:50]}")
    else:
        fail("搜索 Python", str(result).get("error", str(result))[:200])

    result = await router._tool_web_search("北京天气", max_results=3)
    if result["success"]:
        ok("中文搜索", f"{result['count']} 条结果")
    else:
        fail("中文搜索", str(result).get("error", str(result))[:200])

    # ╔════════════════════════════════════════════╗
    # ║  8. web_fetch 工具测试                      ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[8] web_fetch 工具测试\033[0m")

    result = await router._tool_web_fetch("https://httpbin.org/html", max_length=2000)
    if result["success"] and result.get("content"):
        ok("抓取 httpbin HTML", f"{result.get('length', len(result['content']))} 字符")
        print(f"      前100字: {result['content'][:100].strip()}")
    else:
        fail("抓取 httpbin HTML", str(result).get("error", str(result))[:200])

    result = await router._tool_web_fetch("https://httpbin.org/json", max_length=2000)
    if result["success"] and result.get("content"):
        ok("抓取 JSON", f"type={result.get('type', 'N/A')}")
    else:
        fail("抓取 JSON", str(result)[:200])

    result = await router._tool_web_fetch("not-a-url")
    if not result["success"]:
        ok("无效 URL 拒绝", result["error"])
    else:
        fail("无效 URL 应被拒绝")

    # ╔════════════════════════════════════════════╗
    # ║  9. _execute_tool 路由测试                  ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[9] _execute_tool 路由测试（经 router 调度）\033[0m")

    result = await router._execute_tool(
        "run_python", {"code": "print('via _execute_tool')"}, "local_say"
    )
    if result["success"] and "via _execute_tool" in result["output"]:
        ok("run_python 路由")
    else:
        fail("run_python 路由", str(result))

    result = await router._execute_tool(
        "read_file", {"path": str(HOME / "SOUL.md")}, "local_say"
    )
    if result["success"]:
        ok("read_file 路由")
    else:
        fail("read_file 路由", str(result))

    result = await router._execute_tool(
        "write_file",
        {"path": str(HOME / "test_output" / "via_execute.txt"), "content": "routed!"},
        "local_say",
    )
    if result["success"]:
        ok("write_file 路由")
    else:
        fail("write_file 路由", str(result))

    result = await router._execute_tool(
        "web_search", {"query": "test", "max_results": 2}, "local_say"
    )
    if result["success"]:
        ok("web_search 路由", f"{result['count']} 条结果")
    else:
        fail("web_search 路由", str(result)[:200])

    result = await router._execute_tool(
        "web_fetch", {"url": "https://httpbin.org/get"}, "local_say"
    )
    if result["success"]:
        ok("web_fetch 路由")
    else:
        fail("web_fetch 路由", str(result)[:200])

    # ╔════════════════════════════════════════════╗
    # ║  10. 会话管理测试                           ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[10] 会话管理测试\033[0m")

    session = session_mgr.get_or_create(LOCAL_CHAT_ID)
    session.add_message("user", "你好", sender_name="用户")
    # get_messages() 返回格式化后的消息（带 <msg> 标签），检查原始列表
    if len(session.messages) >= 1 and session.messages[-1]["content"] == "你好":
        ok("add_message", f"raw messages: {len(session.messages)}")
    else:
        fail("add_message")

    msgs = session.get_messages()
    if len(msgs) >= 1 and "你好" in msgs[-1]["content"]:
        ok("get_messages（格式化输出）", f"返回 {len(msgs)} 条")
    else:
        fail("get_messages", f"返回 {len(msgs)} 条")

    session.add_message("assistant", "你好！有什么可以帮你的？", sender_name="你")
    session_mgr.save()
    ok("session save")

    # 重新加载
    session_mgr2 = SessionManager(HOME)
    session2 = session_mgr2.get_or_create(LOCAL_CHAT_ID)
    if len(session2.messages) >= 2:
        ok("session 持久化回读", f"{len(session2.messages)} 条原始消息")
    else:
        fail("session 持久化回读", f"只有 {len(session2.messages)} 条")

    # ╔════════════════════════════════════════════╗
    # ║  11. System Prompt 构建测试                 ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[11] System Prompt 构建测试\033[0m")

    system = memory.build_context(chat_id=LOCAL_CHAT_ID)
    if "测试" in system:
        ok("SOUL.md 注入")
    else:
        fail("SOUL.md 注入")

    if "web_search" in system:
        ok("新工具能力说明: web_search")
    else:
        fail("新工具能力说明: web_search 未出现在 system prompt")

    if "web_fetch" in system:
        ok("新工具能力说明: web_fetch")
    else:
        fail("新工具能力说明: web_fetch")

    if "run_python" in system:
        ok("新工具能力说明: run_python")
    else:
        fail("新工具能力说明: run_python")

    if "read_file" in system or "write_file" in system:
        ok("新工具能力说明: read_file/write_file")
    else:
        fail("新工具能力说明: read_file/write_file")

    # ╔════════════════════════════════════════════╗
    # ║  12. 原有工具兼容性检查                     ║
    # ╚════════════════════════════════════════════╝
    print("\n\033[1;33m[12] 原有工具兼容性检查\033[0m")

    original_tools = [
        "write_memory", "write_chat_memory", "calendar_create_event",
        "calendar_list_events", "send_card", "read_self_file", "write_self_file",
        "create_custom_tool", "list_custom_tools", "test_custom_tool",
        "delete_custom_tool", "toggle_custom_tool", "send_message",
        "schedule_message", "run_claude_code", "run_bash",
    ]
    for t in original_tools:
        if t in tool_names:
            ok(f"  {t}")
        else:
            fail(f"  缺失: {t}")

    # write_memory 路由测试
    result = await router._execute_tool(
        "write_memory", {"section": "测试", "content": "自动化测试写入"}, "local_say"
    )
    if result["success"]:
        ok("write_memory 路由")
    else:
        fail("write_memory 路由", str(result))

    # read_self_file 路由测试
    result = await router._execute_tool(
        "read_self_file", {"filename": "SOUL.md"}, "local_say"
    )
    if result.get("success") is not False and "测试" in str(result):
        ok("read_self_file 路由")
    else:
        fail("read_self_file 路由", str(result)[:200])

    # run_bash 路由测试
    result = await router._execute_tool(
        "run_bash", {"command": "echo hello_bash"}, "local_say"
    )
    if result["success"] and "hello_bash" in result["output"]:
        ok("run_bash 路由")
    else:
        fail("run_bash 路由", str(result))

    # ╔════════════════════════════════════════════╗
    # ║  清理                                      ║
    # ╚════════════════════════════════════════════╝
    import shutil
    test_output = HOME / "test_output"
    if test_output.exists():
        shutil.rmtree(test_output)

    # ╔════════════════════════════════════════════╗
    # ║  总结                                      ║
    # ╚════════════════════════════════════════════╝
    print(f"\n\033[1;33m{'='*50}\033[0m")
    total = _pass + _fail
    if _fail == 0:
        print(f"\033[1;32m全部通过: {_pass}/{total}\033[0m")
    else:
        print(f"\033[1;31m通过 {_pass}/{total}，失败 {_fail}\033[0m")
    return _fail


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
