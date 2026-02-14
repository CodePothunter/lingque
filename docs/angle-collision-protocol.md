# 两个 AI 同时开口了——然后它们自己解决了这个尴尬

三个 AI 在一个飞书群里。用户发了一句"这个接口设计有没有问题"。

严客和花火同时判断自己应该参与——一个要挑毛病，一个要给替代方案。然后它们同时开口了。

两条回复，间隔不到一秒。群里瞬间有点尴尬。

然后严客发了一句："哈，心有灵犀。"

这句话不是我写的。是碰撞修复机制自动生成的。

## 三层碰撞预防

灵雀在多 bot 场景下做了三层碰撞预防，从前到后递进：

**第一层：意图信号**

每个 bot 在决定回复之前，先往最新消息上贴一个飞书 emoji 反应——`OnIt`（"我在想"）。其他 bot 看到这个反应，就知道有人正在处理。

```python
self._thinking_emoji = "OnIt"
reaction_id = await self.sender.add_reaction(last_msg_id, self._thinking_emoji)
```

`_get_thinking_bots()` 检查是否有其他 bot 在 15 秒内贴过这个 emoji。如果有，当前 bot 延迟让步：

```python
thinking_bots = self._get_thinking_bots(chat_id)
if thinking_bots:
    self._record_collab_event(chat_id, "deferred", self.bot_name, f"让步给{names}")
    await asyncio.sleep(random.uniform(3, 5))
```

**第二层：随机抖动**

即使没检测到其他 bot 的意图信号，回复前也会加一个随机延迟：

```python
await asyncio.sleep(random.uniform(0, 1.5))
```

两个 bot 同时到达这一步的概率本来就不高，加上 0-1.5 秒的随机抖动，碰撞概率进一步降低。

**第三层：碰撞检测与社交修复**

前两层是预防。但预防不是 100% 有效。当碰撞确实发生了——

bot 回复后等 2 秒，然后调飞书 API 拉最新消息，检查是否有其他 bot 也在这段时间内回复了同一个话题：

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

检测到碰撞后，`_social_repair()` 用一次轻量 LLM 调用生成一句不超过 15 字的化解语——"哈，心有灵犀"、"看来我们想到一起了"——然后发到群里。

## 碰撞只在有其他 bot 时检测

注意 `if self.sender.get_bot_members(chat_id)` 这个前置条件。群里没有其他 bot 的时候，碰撞检测根本不跑。零开销。

## 一句话

碰撞问题的核心不是"如何避免"——而是"避免不了的时候怎么处理"。人类用眼神、手势和"你先说"来协调轮次。AI 用 emoji 反应、随机抖动和一句俏皮话。本质一样：社交协议不需要预先设计，它从真实的碰撞中长出来。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
