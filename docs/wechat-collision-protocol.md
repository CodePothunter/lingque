# 当两个 AI 同时抢话，它们发明了一套社交礼仪

## 一

三个 AI 在一个飞书群里各自运行。大部分时候配合默契——日程问题奶油接，技术问题严客接，创意讨论花火接。

但偶尔，一个话题的边界是模糊的。

"这个接口设计合理吗？需要改吗？"——这算技术审查（严客的领域）还是产品决策（可能需要花火提供替代方案）？

两个 AI 同时判断自己应该参与。同时开始准备回复。同时发送。

群里突然出现两条回复，间隔不到一秒。

这就是碰撞。

## 二

碰撞在人类会议中也常见。两个人同时开口，都停下来，尴尬地说"你先说"。然后可能又同时开口。最终某个人用肢体语言——一个手势、一个眼神——表示让步。

AI 没有肢体语言。但它们有飞书的 emoji 反应。

灵雀的碰撞预防第一层就建在这上面。每个 bot 在准备回复之前，会先往最新消息上贴一个 `OnIt` 的 emoji 反应——相当于举手示意"我在处理了"。

```python
self._thinking_emoji = "OnIt"
reaction_id = await self.sender.add_reaction(last_msg_id, self._thinking_emoji)
```

其他 bot 在做自己的判断之前，先检查：有没有人已经在"思考"了？

```python
def _get_thinking_bots(self, chat_id):
    signals = self._thinking_signals.get(chat_id, {})
    now = time.time()
    active = []
    for bot_id, ts in signals.items():
        if now - ts > 15:
            continue  # 超过 15 秒的信号视为过期
        elif bot_id != self.bot_open_id:
            name = self.sender._user_name_cache.get(bot_id, bot_id[-6:])
            active.append(name)
    return active
```

15 秒 TTL。超过 15 秒没有更新，信号过期——可能那个 bot 想了半天决定不回复了，不能让它永远占着"我在想"的状态。

如果检测到其他 bot 正在思考，当前 bot 延迟 3-5 秒让步，并在协作记忆中记录一条"让步给 XX"。

## 三

第一层靠信号。第二层靠随机。

```python
await asyncio.sleep(random.uniform(0, 1.5))
```

即使没有检测到任何 thinking 信号，回复前也加一个 0-1.5 秒的随机延迟。

为什么？因为两个 bot 可能在极短时间内同时到达"检测 thinking 信号"这一步，同时发现没人在想，然后同时决定回复。

随机抖动打破了这种同步性。一个等了 0.3 秒，另一个等了 1.1 秒——在这 0.8 秒的差距里，先行动的那个已经贴上了 thinking 信号，后行动的就能检测到了。

这个技巧在分布式系统中很常见。以太网的 CSMA/CD 协议用类似的退避机制解决信号碰撞。灵雀用它解决对话碰撞。

## 四

但预防不是 100% 有效的。

信号检测有延迟，随机抖动有概率重叠，网络波动可能导致 emoji 反应到达得太晚。碰撞仍然会发生。

灵雀的第三层应对的是已经发生的碰撞。

bot 发送回复后，等 2 秒，然后调飞书 API 拉最近 5 条消息，检查有没有其他 bot 也在这段时间内回复了：

```python
if self.sender.get_bot_members(chat_id):
    await asyncio.sleep(2)
    api_msgs = await self.sender.fetch_chat_messages(chat_id, 5)
    other_bot_replies = [
        m for m in api_msgs
        if m.get("sender_type") == "app"
        and m.get("sender_id") != self.bot_open_id
        and m["message_id"] not in known_ids
    ]
```

注意 `if self.sender.get_bot_members(chat_id)` 这个前置条件——只有群里确实存在其他 bot 时才执行碰撞检测。单 bot 场景零开销。

## 五

检测到碰撞之后怎么办？

`_social_repair()` 用一次轻量 LLM 调用生成一句化解语：

```python
repair_prompt = (
    f"你和{other_name}不小心同时回复了群聊里的同一个话题（撞车了）。"
    "请用一句简短（<15字）、自然、轻松的话化解这个小尴尬。"
    "不需要道歉，可以调侃。只输出这句话本身，不要加引号。"
)
```

生成的文本经过验证：不超过 50 字、不含换行、不含 JSON/XML 残片。通过验证的才发送。

"哈，心有灵犀。"

"看来我们想到一起了。"

"英雄所见略同。"

每次碰撞，生成的话都不一样。这不是预设的回复模板——是 LLM 即时创作的社交语言。

同时，碰撞事件被记录到协作记忆中。下次遇到类似话题，这段历史会影响 bot 的判断——"上次我和严客在这类话题上碰撞了，这次我先让让"。

## 六

回顾这三层机制——信号、抖动、修复——你会发现它们不是一套从上到下设计好的"碰撞避免协议"。

它们更像是从真实问题中逐层长出来的应对措施。

先有碰撞（两个 bot 同时回复），然后才有信号（"我在想了"）。信号不够可靠，于是加了抖动。抖动也不能完全避免，于是加了事后修复。修复本身也是一种信号——它告诉所有人"碰撞发生了，但我们处理了"。

这就是协议涌现的过程。不是一个架构师画好流程图然后实现，而是一个系统在运行中遇到问题、解决问题、积累经验。

人类的社交礼仪也是这样来的。没有人设计"两个人同时说话时应该怎么办"的规则。这些规则从无数次尴尬中自然长了出来。

只不过人类花了几千年。AI 花了几天。

项目地址：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
