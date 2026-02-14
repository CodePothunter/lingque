# 零依赖多机部署：我把飞书群聊当成了消息总线

三个 AI 实例跑在不同的服务器上，需要感知彼此的存在、避免重复回复、共享群聊状态。

常规做法：加个 Redis 做 pub/sub，或者用 SQLite 做共享状态，再来个服务发现组件。

灵雀的做法：什么都不加。**用飞书群聊本身当消息总线。**

## 核心逻辑：3 秒一次轮询

```python
async def _poll_active_groups(self, router, sender):
    while not self.shutdown_event.is_set():
        await asyncio.sleep(3.0)
        active = router.get_active_groups()
        for chat_id in active:
            api_msgs = await sender.fetch_chat_messages(chat_id, 10)
            for msg in api_msgs:
                if msg.get("sender_type") != "app":
                    continue
                if msg.get("sender_id") == router.bot_open_id:
                    continue
                await router.inject_polled_message(chat_id, msg)
```

每 3 秒，对每个活跃群调一次飞书的 `fetch_chat_messages` REST API，拉最近 10 条消息。过滤出**其他 bot 发的消息**（sender_type 为 app 且不是自己），注入到本地的消息缓冲区。

WebSocket 能收到人发的消息，但收不到其他 bot 发的消息。这个轮询补上了这个缺口。

## 去重

同一条消息会被重复轮询到。`inject_polled_message()` 维护一个 `_polled_msg_ids` 集合做去重——见过的 message_id 直接跳过。

## 为什么能当"总线"

飞书群聊天然具备消息总线的三个核心能力：

1. **消息存储**：所有消息持久化在飞书服务端，任何实例随时可以读取
2. **身份标识**：每个 bot 有唯一的 open_id，每条消息有 sender_type 标识来源
3. **成员发现**：`_cache_chat_members()` 一次 API 调用获取群内所有成员，包括其他 bot

三个 AI 实例跑在三台不同的服务器上。它们之间没有任何直接通信。它们各自独立地读取同一个飞书群的消息，各自独立地决定要不要回复。

如果 A 回复了，B 和 C 在下一次轮询时就能看到 A 的回复。不需要 A 主动通知 B 和 C——飞书群就是那个"通知渠道"。

## 活跃群 TTL

不是所有群都需要轮询。`register_active_group(chat_id)` 设一个 600 秒的 TTL——只有最近 10 分钟内有消息的群才会被轮询。群安静了，轮询自动停止。

多个群之间轮询间隔 1 秒，避免触发 API 频率限制。

## 代价

3 秒轮询意味着最大 3 秒延迟。这不是实时系统。但对群聊协作来说，3 秒的感知延迟完全可接受——人类打字还需要好几秒呢。

API 调用量：每个活跃群每分钟 20 次请求。三个群 = 60 次/分钟。远在飞书 API 限额之内。

## 一句话

最好的基础设施不是你新搭的，是你已经在用的。飞书群聊本身就是一个消息存储、身份验证、成员管理一体化的平台——你只需要读它。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
