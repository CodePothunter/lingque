# 飞书即总线：我用聊天平台本身替代了整个分布式基础设施

## 一

当你决定让多个 AI 实例协作时，第一个问题是：它们怎么通信？

本能反应是加基础设施。Redis 做 pub/sub，让 bot 之间实时通知。PostgreSQL 或 SQLite 做共享状态，存谁回复了什么。再来个服务发现，让新加入的 bot 能找到同伴。

三个组件，三份运维负担，三个可能挂掉的点。

灵雀选了另一条路：什么都不加。

## 二

灵雀的多实例协作架构里，没有 Redis，没有数据库，没有消息队列，没有 RPC，没有任何两个 bot 之间的直接通信。

它们通过**飞书群聊本身**来感知彼此。

核心机制是一个轮询循环：

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

每 3 秒，对每个活跃群调一次飞书的消息列表 API。过滤出其他 bot 发的消息（`sender_type == "app"` 且不是自己），注入到本地的消息缓冲区。

## 三

为什么需要这个轮询？因为飞书的 WebSocket 有一个特性：它只推送人类用户发的消息。bot 发的消息不会通过 WS 推送给同群的其他 bot。

这意味着，如果只依赖 WebSocket，三个 bot 在同一个群里是"互相看不见"的。奶油不知道严客刚刚回复了什么。严客不知道花火是否正在处理某个问题。

轮询补上了这个缺口。每 3 秒拉一次 API，其他 bot 的回复就进入了本地视野。

## 四

为什么飞书群聊能当消息总线？

因为它天然具备总线的三个核心能力：

**消息持久化。** 所有消息存在飞书服务端。任何 bot 在任何时刻都可以通过 REST API 读取历史消息。这就是存储层。

**身份标识。** 每个 bot 有唯一的 `open_id`。每条消息带 `sender_id` 和 `sender_type`。谁发的、什么类型，一目了然。这就是身份层。

**成员发现。** `_cache_chat_members()` 一次 API 调用获取群内所有成员，包括人和 bot。每个 bot 自动知道群里还有谁。这就是发现层。

存储、身份、发现——一个消息总线需要的三样东西，飞书群聊全部自带。

## 五

去重是必须解决的问题。同一条消息在连续多次轮询中都会被拉到。

`inject_polled_message()` 维护一个 `_polled_msg_ids` 集合：见过的 `message_id` 直接跳过。

```python
if msg_id in self._polled_msg_ids:
    return
self._polled_msg_ids.add(msg_id)
```

活跃群有 TTL 机制：`register_active_group()` 设 600 秒的有效期。最近 10 分钟没有消息的群不再被轮询。群安静了，API 调用也停了。

多个群之间每次轮询间隔 1 秒，避免触发飞书 API 频率限制。

## 六

这个架构最大的好处是：**跨机器部署零成本。**

三个 bot 跑在三台不同的服务器上。它们之间没有任何网络连接。不需要知道彼此的 IP，不需要共享配置，不需要打通防火墙。

它们各自独立地连接飞书服务，各自独立地读取群消息，各自独立地决定要不要回复。

如果 A 回复了，B 和 C 在下一次轮询（最多 3 秒后）就能看到 A 的回复。然后它们根据这个新信息调整自己的判断。

不需要 A 通知 B 和 C。飞书群就是那个通知渠道。

## 七

代价当然有。

3 秒轮询意味着最大 3 秒感知延迟。在实时要求高的场景（比如多 bot 协同编辑同一个文档）里，这不够用。

但在群聊场景下——人类打字需要几秒，阅读需要几秒，思考需要更长——3 秒延迟是无感的。

API 调用量：每个活跃群每分钟约 20 次请求（3 秒间隔 × 20 次/分）。三个活跃群同时在线约 60 次/分钟。飞书 API 的默认频率限制远高于此。

## 八

回过头来看，这个选择的本质是：**不要在已有平台之上再搭一层平台。**

飞书已经是一个高可用的消息系统。它的服务端做了消息持久化、用户鉴权、成员管理、权限控制。在它上面再搭一个 Redis 做同样的事情，不是"增强"——是"重复"。

最好的基础设施不是你新建的那个。是你已经在用的那个。你只需要学会读它。

项目地址：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
