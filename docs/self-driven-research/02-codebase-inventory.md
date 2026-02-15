# LingQue 自驱动能力基座清单

> 分析日期: 2026-02-15
> 目标: 梳理所有可支撑自主/自驱动行为的现有机制

---

## 1. 心跳机制 (Heartbeat)

**核心文件**: `heartbeat.py` (1-84行), `gateway.py` (280-381行)

### 触发方式
- 间隔: `heartbeat_interval` (默认 3600s = 1小时)
- 活跃时段: `active_hours` (默认 8:00-23:00)
- 每日首次触发 `is_daily_first` 标志
- 每周一首次触发 `is_weekly_first` 标志

### 心跳期间能力
- 读取 HEARTBEAT.md 中的任务并执行
- **拥有全部 22 个工具的访问权限**
- 可自由搜索、创建工具、修改记忆
- 结果可发送到 owner_chat_id
- 包含漂移检测上下文（反思日志 + 工具统计）

### 自驱动潜力: ⭐⭐⭐⭐⭐
心跳是自主行为的**最佳执行窗口**——定期触发、全工具访问、无需用户指令。

---

## 2. 自我修改能力

**文件**: `memory.py` (393-410行)

### 可编辑文件白名单
```
EDITABLE_FILES = {"SOUL.md", "MEMORY.md", "HEARTBEAT.md"}
```

### API
- `read_self_file(filename)` — 读取工作区配置文件
- `write_self_file(filename, content)` — 覆写配置文件

### 边界
- ✅ 可修改: SOUL.md, MEMORY.md, HEARTBEAT.md
- ❌ 不可修改: 源代码(src/lq/*.py)、config.json、凭证

---

## 3. 工具创建系统

**文件**: `tools.py` (133-169行)

### 创建流程
`create_custom_tool(name, code)` → AST 验证 → 写入 tools/{name}.py → 加载注册

### AST 安全检查 (tools.py:88-131)
- 必须包含 `TOOL_DEFINITION` dict
- 必须包含 `async def execute()` 函数
- 禁止导入: subprocess, shutil, ctypes, signal, multiprocessing

### 自定义工具执行上下文
```python
context = {
    "sender": sender,      # 可发消息
    "memory": memory,      # 可修改 MEMORY.md
    "calendar": calendar,  # 日历 CRUD
    "http": http_client,   # 可调用外部 API
}
```

### 自驱动潜力: ⭐⭐⭐⭐⭐
bot 可以发现需求 → 创建工具 → 立即使用，形成完整的能力自扩展循环。

---

## 4. 全部 22 个内置工具

### 记忆工具
1. write_memory — 更新全局 MEMORY.md
2. write_chat_memory — 更新 per-chat 记忆

### 日历工具
3. calendar_create_event
4. calendar_list_events

### 消息工具
5. send_card — 发送富文本卡片
6. send_message — 发送消息到任意 chat_id
7. schedule_message — 定时发送

### 自我修改工具
8. read_self_file
9. write_self_file
10-14. create/list/test/delete/toggle_custom_tool

### 代码执行工具
15. run_claude_code — Claude Code CLI (几乎无限制)
16. run_bash — Bash 执行 (屏蔽危险命令)
17. run_python — Python 执行

### Web 工具
18. web_search — 智谱 MCP 搜索
19. web_fetch — 抓取并解析网页

### 文件工具
20. read_file — 读取任意文件
21. write_file — 写入任意文件

### 统计工具
22. get_my_stats — 查询自身运行统计

---

## 5. 代码执行能力

### run_bash (claude_code.py:164-263)
- 屏蔽命令: rm -rf /, mkfs, dd, shutdown, reboot 等
- **可以运行**: git commit, git push, pip install 等
- 输出截断: 10,000 字符

### run_claude_code (claude_code.py:35-155)
- 使用 `--dangerously-skip-permissions` 启动
- 超时: 300s
- 理论上可以修改源代码、执行 git 操作

---

## 6. Web 搜索与数据获取

### web_search (router.py:1365-1424)
- 通过智谱 MCP web-search-prime API
- Session 缓存、自动重试
- SSE 响应解析

### web_fetch (router.py:1536-1580)
- Chrome UA 伪装
- 代理支持
- HTML → 文本提取
- 最大 8000 字符

---

## 7. 群聊自主介入

**文件**: `router.py` (1947-2069行)

### 三层介入系统
1. **琐碎消息过滤** (buffer.py) — 零 LLM 成本
2. **消息缓冲** — deque(max=20), 5条阈值或10s超时
3. **LLM 评估** — 生成 should_intervene/reason/reply_to

### 自驱动潜力: ⭐⭐⭐⭐
bot 已经能在群聊中自主决定是否发言——这是无指令自主行为的基础模式。

---

## 8. 记忆与学习体系

### 三层记忆
1. **SOUL.md** — 核心人格 (3000 token 预算)
2. **MEMORY.md** — 全局长期记忆 (4000 token 预算)
3. **Per-Chat Memory** — 每会话记忆 (chat_memories/*.md)

### 日志系统
- `memory/{YYYY-MM-DD}.md` — 按 chat_id 标签的每日日志

### 反思管线
- `_reflect_on_reply()` — 每次私聊回复后轻量 LLM 自评
- `logs/reflections-{YYYY-MM-DD}.jsonl` — 结构化反思记录
- 心跳漂移检测读取反思日志

---

## 9. 空闲行为（无消息时）

### 现有定时行为
1. **心跳** — 每 N 小时 (活跃时段内)
2. **早安问候** — 每日一次 (可配置)
3. **会话自动保存** — 每 60 秒
4. **群聊轮询** — 每 3 秒

### 可通过 HEARTBEAT.md 扩展
```markdown
## 深夜反思
每晚 23:00，读取日志，总结今日学到的，更新 MEMORY.md。
```

---

## 10. 自我感知数据

### stats_provider 闭包提供 (gateway.py:120-162)
- 当前模型名称
- 运行时长
- 今日 API 调用/token/费用
- 月度费用
- 活跃会话数
- 每工具成功/失败率
- 飞书群聊中的姐妹实例

### 注入方式
每次 system prompt 构建时自动注入到自我感知区块。

---

## 11. 关键缺口（自驱动所需但尚未实现）

| 缺口 | 描述 | 建议方案 |
|------|------|---------|
| 好奇心信号系统 | 无机制检测"值得探索的事物" | CURIOSITY.md + 信号检测 |
| 自主目标生成 | 无机制让 bot 自己设定目标 | 心跳中生成探索计划 |
| 学习进展追踪 | 无法衡量"我在 X 上进步了多少" | 反思扩展 + 进展日志 |
| 跨实例探索共享 | 奶油和捏捏无法分享发现 | 共享 CURIOSITY.md 或消息通道 |
| 人类审批回路 | 敏感修改无审批机制 | 飞书卡片 + 确认按钮 |
| 兴趣人格化 | 好奇心方向未与人格绑定 | SOUL.md 增加兴趣偏好段 |
