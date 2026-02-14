# AI 终于不再忘事了——token 预算如何让对话记忆变可靠

旧版灵雀用一个固定阈值管理对话记忆：50 条消息，压缩一次，保留最后 10 条。

问题在于：两条"好的"和一篇 2000 字的技术分析，在这个系统里权重一样。结果是：AI 经常在关键对话进行到一半时触发压缩，把刚刚讨论的上下文砍掉了。

新版重写了整个会话管理。核心改动：**用 token 计数替代消息条数**。

## 一个中文字不等于一个 token

`estimate_tokens()` 对中英混合文本做加权估算：

```python
def estimate_tokens(text: str) -> int:
    cjk_count = sum(1 for c in text if '\u4e00' <= c <= '\u9fff'
                    or '\u3000' <= c <= '\u303f'
                    or '\uff00' <= c <= '\uffef')
    ascii_count = len(text) - cjk_count
    return int(cjk_count * 1.5 + ascii_count * 0.3)
```

中文 1.5 token/字，英文 0.3 token/字符。误差约 ±20%，做预算控制足够了。

## 三个数字

```python
MAX_CONTEXT_TOKENS = 40_000       # 对话历史的总预算
COMPACT_THRESHOLD_TOKENS = 30_000 # 达到这个值触发压缩
COMPACT_TARGET_TOKENS = 15_000    # 压缩后保留这么多
```

不再是"保留最后 10 条"——而是从最新消息往前取，直到 15,000 token 的预算用完。一段长讨论可能只保留 3 条，一段短对话可能保留 30 条。**按信息密度保留，不按条数**。

## system prompt 也有预算

不只是对话历史——注入 system prompt 的每个部分都有独立预算：

```
SOUL.md     → 3,000 tokens（人格定义，最高优先级，完整注入）
MEMORY.md   → 4,000 tokens（长期记忆，超预算按段落截断）
日志         → 2,000 tokens（当天对话摘要）
自我认知     → 2,000 tokens（能力列表、工具清单，5 分钟缓存）
────────────
总预算       = 15,000 tokens
```

SOUL.md 永远完整。MEMORY.md 超预算时，`_truncate_memory()` 做段落级截断——保留所有 `##` 标题，优先保留最近更新的段落。不是从末尾一刀切，而是保留结构。

## 工具调用是一等公民

旧版只记录"人说了什么、AI 回了什么"。新版把工具调用也写进会话历史：

```python
session.add_tool_use("exchange_rate", {"from": "CNY", "to": "USD"}, tool_use_id)
session.add_tool_result(tool_use_id, '{"rate": 0.137}')
```

这意味着压缩时，摘要里会包含"AI 查了汇率"这个事实。下次用户问"你刚才查的汇率是多少"，AI 不会一脸茫然——因为工具调用链被保留在了上下文里。

## 一句话

记忆不是存下所有东西。是知道什么该忘、什么不该忘——然后把有限的预算分给最重要的部分。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
