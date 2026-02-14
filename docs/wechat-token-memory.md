# 对话的重量：一个 AI 框架如何学会按 token 分配记忆

## 一

你跟 AI 聊了一个小时。前半小时讨论了一个复杂的技术方案，后半小时在闲聊。

然后你问它："我们刚才讨论的那个方案，第三点是什么？"

它说："抱歉，我不记得了。"

不是它不想记。是它的记忆管理系统在第 50 条消息时做了一次压缩，把前 40 条全部丢弃，保留了最后 10 条——恰好都是闲聊。

**问题不在 AI 的能力，而在它衡量"什么值得记住"的方式。**

## 二

灵雀的旧版也是这样：固定 50 条消息触发压缩，保留最后 10 条。这个逻辑在大部分场景下够用。

但它有一个根本性的缺陷：**它不知道一条消息有多"重"。**

"好的" 是一条消息。一段 2000 字的需求文档也是一条消息。在旧系统里，它们的权重完全一样。

更糟的是工具调用。AI 帮你查了汇率、建了日历事件、写了一段代码——这些操作在旧版里不计入会话历史。压缩之后，AI 不知道自己之前做过什么。用户问"你刚帮我查的汇率是多少"，它一脸茫然。

## 三

新版灵雀重写了整个会话管理模块。核心变化：**用 token 预算替代消息条数。**

```python
MAX_CONTEXT_TOKENS = 40_000       # 对话历史的总预算
COMPACT_THRESHOLD_TOKENS = 30_000 # 达到此值触发压缩
COMPACT_TARGET_TOKENS = 15_000    # 压缩后保留的目标量
```

token 估算考虑了中英文的差异——中文一个字大约 1.5 个 token，英文一个字符大约 0.3 个 token。混合内容按字符类型加权：

```python
def estimate_tokens(text: str) -> int:
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3000' <= c <= '\u303f'
                    or '\uff00' <= c <= '\uffef')
    ascii_count = len(text) - cjk_count
    return int(cjk_count * 1.5 + ascii_count * 0.3)
```

误差约 ±20%。做预算控制不需要精确——需要的是量级正确。

## 四

压缩策略也变了。旧版固定保留最后 10 条。新版从最新消息往前取，直到用完 15,000 token 的预算：

```python
def compact(self, summary: str) -> None:
    kept = []
    budget = COMPACT_TARGET_TOKENS
    for msg in reversed(self.messages):
        msg_tokens = msg.get("_tokens", estimate_tokens(msg["content"]))
        if budget - msg_tokens < 0 and kept:
            break
        kept.append(msg)
        budget -= msg_tokens
    kept.reverse()
    self._summary = summary
    self.messages = kept
```

如果最近的对话是密集的技术讨论（每条消息几百 token），可能只保留 5 条。如果是简短的日常对话（每条几十 token），可能保留 50 条。

**保留的不是固定条数，而是固定信息量。** 这才对。

## 五

不只是对话历史——system prompt 的每个组成部分也有独立的 token 预算：

```
SOUL_BUDGET      = 3,000  — 人格定义（最高优先级）
MEMORY_BUDGET    = 4,000  — 长期记忆
DAILY_LOG_BUDGET = 2,000  — 当天对话日志
AWARENESS_BUDGET = 2,000  — 自我认知（能力清单）
TOTAL           = 15,000
```

SOUL.md 永远完整注入——它定义了 AI 是谁，截断不可接受。

MEMORY.md 超预算时做段落级截断。`_truncate_memory()` 保留所有 `##` 标题（维持文档结构），优先丢弃旧段落保留新段落。不是从末尾一刀切，而是按段落粒度智能取舍。

自我认知列表（AI 知道自己有哪些工具、能做什么）有 5 分钟缓存。工具清单不会频繁变化，没必要每次都重建。

## 六

最后一个关键改动：**工具调用成为会话历史的一等公民。**

```python
session.add_tool_use("exchange_rate", {"from": "CNY", "to": "USD"}, tool_use_id)
session.add_tool_result(tool_use_id, '{"rate": 0.137}')
```

每次 AI 调用工具，调用参数和返回结果都记入会话。压缩时，摘要生成器能看到"AI 曾经查过汇率、创建过日历事件"这些事实。

压缩后的摘要可能包含："用户要求查询 CNY 兑 USD 汇率，结果为 0.137"。当用户后续问起，这条信息仍然在上下文中。

工具输入超过 500 字符会被截断，工具结果超过 1000 字符也会被截断——防止一次大输出吃掉整个 token 预算。

## 七

这些改动加起来，解决的是一个根本问题：**对话的"重量"不均匀。**

50 条闲聊可能只有 5,000 token。一次深入的技术讨论可能一条消息就有 3,000 token。工具调用链可能产生大量隐含信息。

用一个固定的消息条数去管理这些内容，注定顾此失彼。

按 token 分配预算、按信息密度保留上下文、按优先级截断 system prompt——这不是更聪明，是更诚实。它承认了一个事实：不是所有的话都同样重要。

项目地址：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
