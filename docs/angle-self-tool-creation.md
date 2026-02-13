# 它不说"做不到"——它给自己写了段代码，5 秒后就会了

我跟 AI 说"帮我查个汇率"。

它沉默了几秒。然后告诉我人民币兑美元的实时汇率。

我去翻日志，发现这几秒钟里它做了三件事：

1. 用 `create_custom_tool` 写了一段 Python 代码，调用汇率 API
2. 通过 AST 静态分析做了安全审计
3. 把新工具热加载到运行时，然后立刻调用

**它给自己造了一个新器官，然后马上用上了。**

## 不是 plugin store

市面上的 AI 扩展能力大多是预装插件。你有的功能是开发者预想好的，没有的就没有。

灵雀不是这样。它在对话中发现自己缺某个能力时，当场写代码创建。用户感知到的是"AI 回答了问题"，不知道背后刚刚发生了一次自我进化。

工具以 Python 文件形式存在 `tools/` 目录下，每个文件暴露一个 `TOOL_DEFINITION` 字典和一个 `execute` 异步函数：

```python
TOOL_DEFINITION = {
    "name": "exchange_rate",
    "description": "查询实时汇率",
    "input_schema": {
        "type": "object",
        "properties": {
            "from_currency": {"type": "string"},
            "to_currency": {"type": "string"},
        },
    },
}

async def execute(input_data: dict, context: dict) -> dict:
    resp = await context["http"].get(f"https://api.example.com/rate?...")
    return {"success": True, "rate": resp.json()["rate"]}
```

创建后 `_build_all_tools()` 在下一次工具调用循环中刷新可用列表。不需要重启。

## 安全：不是字符串匹配

LLM 生成的代码不能为所欲为。`validate_code()` 用 AST 静态分析做硬约束：

```python
BLOCKED_IMPORTS = frozenset({
    "os", "subprocess", "shutil", "sys", "socket",
    "ctypes", "signal", "multiprocessing", "threading",
})
```

这是 AST 级检查。`import os` 和 `from os.path import join` 都会被拦截。不是在源码里搜字符串——是解析语法树逐节点检查。

除了 import 限制，还验证结构：必须有 `TOOL_DEFINITION` 和 `execute()`。结构不对直接拒绝。不存在"代码能跑但行为不可预期"的灰色地带。

## 多实例下的独立演化

这件事在多 AI 场景下更有意思。

灵雀支持多实例——每个 AI 有独立的 `tools/` 目录。跑了一段时间之后：

```
~/.lq-naiyou/tools/exchange_rate.py    # 奶油的工具
~/.lq-yanke/tools/grammar_check.py     # 严客的工具
~/.lq-huahuo/tools/random_spark.py     # 花火的工具
```

奶油帮人查汇率，所以它造了汇率工具。严客审文档，所以它造了语法检查工具。花火做头脑风暴，所以它造了灵感生成器。

**没有人分配它们造什么。** 它们根据各自遇到的需求，自主长出了不同的能力。就像同一个团队里，每个人慢慢发展出自己的专业工具集。

## 一句话

灵雀的能力边界不是在开发时决定的，是在运行时生长的。你不需要预判所有需求——漏掉的那些，它自己会补上。

它不说「我做不到」。它说「等我几秒，我造一个」。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
