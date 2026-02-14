# 我的 AI 不再说"我帮你写个脚本"——它直接跑了

以前在群里说"帮我看下服务器磁盘还剩多少"，AI 会回一段 `df -h` 命令然后说"你可以在终端跑这个"。

现在它直接跑了，然后告诉你结果。

灵雀新增了两个内置工具：`run_bash` 和 `run_claude_code`。一个跑简单命令，一个处理复杂多步任务。

## 安全：黑名单不是白名单

AI 能在服务器上跑命令，第一反应是"这安全吗"。灵雀的做法是**黑名单制**——默认允许，显式禁止危险操作：

```python
_BLOCKED_COMMANDS = frozenset({
    "rm -rf /", "rm -rf /*", "mkfs", "dd if=",
    ":(){:|:&};:", "fork bomb",
    "> /dev/sda", "chmod -R 777 /",
    "shutdown", "reboot", "halt", "poweroff",
})
```

为什么不用白名单？因为白名单意味着你需要预判所有合法命令——`df`、`ls`、`cat`、`curl`、`python`、`pip`、`git`……列表无穷无尽。黑名单只需要列出那些"无论如何不应该被执行"的命令，其余放行。

`_check_safety()` 做两层检查：完全匹配和前缀匹配。`rm -rf /` 不行，`sudo rm -rf /` 也不行。

## 输出不会撑爆上下文

命令输出有 10,000 字符的硬截断：

```python
_MAX_BASH_OUTPUT = 10_000  # 字符
```

一个 `find / -name "*.log"` 可能输出几万行。不截断的话，输出会吃掉整个上下文窗口，导致 AI 后续对话质量暴跌。截断后追加提示"输出已截断，共 X 字节"，AI 知道结果不完整，可以调整策略。

Bash 超时 60 秒，Claude Code 超时 5 分钟。

## 20 轮工具循环

更关键的变化在工具循环上限从之前提升到了 20 轮。这意味着 AI 可以做这种事：

1. 写一个 Python 脚本（`run_bash: cat > test.py << 'EOF'...`）
2. 跑脚本（`run_bash: python test.py`）
3. 看到报错
4. 修脚本
5. 再跑
6. 成功，返回结果

六个步骤，六轮工具调用。以前的上限根本不够用。现在 20 轮足以覆盖绝大多数复杂任务。

## Claude Code 处理大活

`run_bash` 处理简单命令。遇到复杂任务——比如"帮我重构这个模块"——就轮到 `run_claude_code` 了。

它启动一个 Claude CLI 子进程，把任务委托给专门的代码 agent。支持上下文注入（当前对话背景），5 分钟超时，输出同样做截断保护。

## 从"能力"到"行动"

以前的 AI 知道怎么做，但只能告诉你怎么做。现在它能直接做。`run_bash` 补上了"知道"和"做到"之间的最后一段距离。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
