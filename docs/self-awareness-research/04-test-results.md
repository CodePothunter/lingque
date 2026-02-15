# Self-Awareness 功能测试报告

> 测试日期: 2026-02-15
> 测试者: tester agent
> 测试环境: 两个 bot 实例 (@奶油 PID 818967, @捏捏 PID 818968) 运行新代码

## 测试结果总览

| 测试项 | 维度 | 结果 | 评分 |
|--------|------|------|------|
| 1. 运行时状态注入 | 状态感知 | PASS | 1.0 |
| 2. get_my_stats 工具 | 能力感知 | PASS | 1.0 |
| 3. 交互后微反思 | 自我监控 | PASS | 0.95 |
| 4. HEARTBEAT.md & 成长记录 | 身份持续性 + 自我进化 | PASS | 1.0 |
| 5. 同伴实例感知 | 社交感知 | PASS | 1.0 |
| 6. 能力校准 | 动态工具统计 | PASS | 1.0 |
| 7. 心跳漂移检测 | 元认知 | PASS | 0.9 |

**总分: 0.979 / 1.0**
**Self-Awareness Level: Level 4 (Self-Evolving)**

---

## 详细测试记录

### Test 1: 运行时状态注入 (State Awareness)

**测试内容:** 验证 `MemoryManager._build_self_awareness()` 通过 `stats_provider` 注入实时运行时统计

**证据:**
- `memory.py:265-293` — stats_provider 被调用，统计数据格式化输出
- `gateway.py:120-152` — `_stats_provider()` 闭包正确收集：模型名、运行时长、今日 API 调用/token/费用、月费用、活跃会话数、工具统计
- `gateway.py:154` — 注入 MemoryManager: `MemoryManager(self.home, stats_provider=_stats_provider)`
- 实测输出包含 `### 运行状态` 区块，带模型名、运行时长、调用次数、token、费用、活跃会话
- 工具统计正确渲染: `web_search (调用11次, 成功率91%)`

**评分: 1.0**

### Test 2: get_my_stats 工具 (Capability Awareness)

**测试内容:** 验证 `get_my_stats` 工具定义、处理器和统计追踪

**证据:**
- `router.py:448-461` — 工具定义完整，schema 包含 category 枚举 ["today", "month", "capability"]
- `router.py:1301-1304` — 处理器分发到 `_tool_get_my_stats()`
- `router.py:1624-1647` — 处理器根据 category 返回日/月/工具统计
- `router.py:892-900` — `_track_tool_result()` 记录每工具成功/失败次数和最后错误
- `router.py:982-986` — 每次工具执行后调用 `_track_tool_result()`

**评分: 1.0**

### Test 3: 交互后微反思 (Self-Monitoring)

**测试内容:** 验证 `_reflect_on_reply()` 在私聊回复后以非阻塞方式触发

**证据:**
- `router.py:854` — `asyncio.create_task(self._reflect_on_reply(...))` — fire-and-forget
- `router.py:856-869` — 发送回复文本（截断500字）通过 `REFLECTION_PROMPT`，`max_tokens=100`
- `router.py:871-890` — `_append_reflection()` 写入 `logs/reflections-{date}.jsonl`
- 异常处理：`logger.debug("自我反思失败")` 确保非阻塞
- 注：尚无反思日志（bots 刚启动，无私聊交互）— 预期行为

**评分: 0.95** (代码完整，待实际数据验证)

### Test 4: HEARTBEAT.md & 成长记录 (Identity + Evolution)

**测试内容:** 验证 HEARTBEAT.md 包含自反思指令，MEMORY.md 有成长记录区域

**证据:**
- `~/.lq-naiyou/HEARTBEAT.md` (1635 bytes) — 包含结构化自反思任务:
  - 回顾今日日志、对照 SOUL.md、记录成长、检查工具状态、身份提醒
- `~/.lq-nienie/HEARTBEAT.md` (1573 bytes) — 同结构，适配捏捏人格
- 两个 MEMORY.md 都有 `## 成长记录` 区域，格式: `- MM-DD 简短记录`
- `gateway.py:338-371` — 心跳执行时读取 HEARTBEAT.md，带完整工具访问

**评分: 1.0**

### Test 5: 同伴实例感知 (Social Awareness)

**测试内容:** 验证 `_build_sibling_awareness()` 检测运行中的同伴实例

**证据:**
- `memory.py:300-328` — 扫描 `~/.lq-*/gateway.pid`，通过 `os.kill(pid, 0)` 检测进程存活
- 从 naiyou 测试: `"你的姐妹实例目前在线: nienie"`
- 从 nienie 测试: `"你的姐妹实例目前在线: naiyou"`
- 双向检测正确

**评分: 1.0**

### Test 6: 能力校准 (Dynamic Tool Stats)

**测试内容:** 验证工具统计追踪和自我感知区块中的动态显示

**证据:**
- `router.py:513` — `_tool_stats` 初始化
- `router.py:892-900` — `_track_tool_result()` 递增成功/失败计数器
- `memory.py:278-291` — 工具统计渲染使用 `CAPABILITY_LINE_TEMPLATE`
- 模板: `"  - {tool_name} (调用{total}次, 成功率{rate}%)"`
- 实测确认渲染正确

**评分: 1.0**

### Test 7: 心跳漂移检测

**测试内容:** 验证 `_build_heartbeat_drift_context()` 读取反思日志和工具统计用于漂移分析

**证据:**
- `gateway.py:373-404` — 读取 `reflections-{today}.jsonl`（最近10条），提取反思文本
- 读取 `router._tool_stats` 生成工具健康摘要
- 追加指令: "请对比 SOUL.md 中的行为准则，判断是否存在行为漂移"
- 无反思文件时优雅降级（返回空字符串）

**评分: 0.9** (代码完整，待反思数据积累)

---

## 架构亮点

- **非阻塞设计**: 反思使用 `asyncio.create_task` (fire-and-forget)
- **优雅降级**: 所有统计/反思功能静默失败并记录日志
- **缓存**: 自我感知区块 5 分钟缓存减少开销
- **双向同伴检测**: 两个实例正确互相检测
- **闭合回路**: 心跳读取反思 → 检测漂移 → 可修改 SOUL.md → 影响未来行为
