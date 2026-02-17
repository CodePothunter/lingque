# 聊天平台抽象层接口规范

> LingQue 平台无关通信协议 v1.4
>
> 本文档定义了一个拟人化 AI Agent 在任意通讯平台上所需的**最小完备动作集**。
> 任何新平台（Discord、Telegram、Slack、微信等）只需实现本文档定义的接口，即可接入 LingQue。

---

## 目录

1. [设计原则](#1-设计原则)
2. [抽象需求 vs 平台补偿](#2-抽象需求-vs-平台补偿)
3. [最小完备动作集总览](#3-最小完备动作集总览)
4. [标准化数据类型](#4-标准化数据类型)
5. [身份 — 我是谁](#5-身份--我是谁)
6. [感知 — 我看到了什么](#6-感知--我看到了什么)
7. [表达 — 我说了什么](#7-表达--我说了什么)
8. [存在感 — 我在处理](#8-存在感--我在处理)
9. [感官 — 我看到的图片和文件](#9-感官--我看到的图片和文件)
10. [认知 — 我知道谁是谁](#10-认知--我知道谁是谁)
11. [可选行为](#11-可选行为)
12. [能力声明](#12-能力声明)
13. [平台配置](#13-平台配置)
14. [外部服务层（非平台抽象）](#14-外部服务层非平台抽象)
15. [标准卡片结构](#15-标准卡片结构)
16. [飞书适配指南](#16-飞书适配指南)
17. [Discord 适配指南](#17-discord-适配指南)
18. [附录 A：内核改造清单](#附录-a内核改造清单)
19. [附录 B：v1.3 → v1.4 变更记录](#附录-bv13--v14-变更记录)
20. [附录 C：历史变更摘要](#附录-c历史变更摘要)

---

## 1. 设计原则

- **描述人的行为，不描述 API 的形状**：接口按"一个人在聊天中做什么"来组织，不按平台 API 的技术结构。
- **最小完备**：每个接口都不可再拆，也不可移除。如果去掉它，Agent 就不再像一个完整的对话参与者。
- **一个意图，一个抽象**：如果两种机制服务于同一个目的（如飞书 reaction("OnIt") 和 Discord typing 都是"我在处理"），它们是同一个抽象行为的不同实现，不应拆成两个接口。
- **补偿对内核透明**：平台特有的补偿行为（轮询、身份推断、格式转换）封装在适配器内部。
- **能力声明制**：适配器声明自身能力，内核据此降级。
- **异步优先**：所有 I/O 均为 `async def`。

---

## 2. 抽象需求 vs 平台补偿

抽象接口只定义**Agent 需要什么**，不定义**平台怎么满足**。

```
Agent 说: "给我所有消息"
  飞书适配器: WS 收一半 + REST 轮询补另一半 → 统一投入事件队列
  Discord 适配器: Gateway 直接全收 → 投入事件队列
  Agent 看到的: 事件队列里源源不断的 IncomingMessage，一视同仁

Agent 说: "告诉他们我在想"
  飞书适配器: add_reaction("OnIt")
  Discord 适配器: trigger_typing() + add_reaction("⏳")
  Telegram 适配器: send_chat_action("typing")
  Agent 看到的: 调了一个方法，拿到 handle，处理完后清掉
```

以下行为是飞书补偿，**不在抽象接口中出现**：

| 飞书限制 | 补偿行为 | 其他平台不需要的原因 |
|---------|---------|-------------------|
| WS 不推 bot 消息 | REST 轮询补漏 | Discord/Telegram 事件流天然包含所有消息 |
| bot 返回 app_id (cli_xxx) | 时序/排除法推断身份 | 其他平台 bot 有统一 ID |
| 群成员 API 不含 bot 信息 | 消息信号逐步注册 | Guild.members 直接完整 |
| 无明确退群事件 | HTTP 400 副作用推断 | on_guild_remove / my_chat_member |
| 文本消息不渲染 Markdown | 检测 → 自动切卡片 | Discord/Telegram 原生 Markdown |
| @用 占位符 @_user_N | 查 mentions 数组替换 | `<@id>` 格式更直接 |
| Token 2 小时过期 | 自动刷新 | Bot Token 长期有效 |

---

## 3. 最小完备动作集总览

一个人在聊天中做的所有事情，归纳为 6 层：

```
┌─────────────────────────────────────────────┐
│  身份 (Identity)     我是谁                   │  1 方法
├─────────────────────────────────────────────┤
│  感知 (Perception)   我看到了什么              │  1 事件流
├─────────────────────────────────────────────┤
│  表达 (Expression)   我说了什么               │  1 方法
├─────────────────────────────────────────────┤
│  存在感 (Presence)   我在处理                 │  2 方法
├─────────────────────────────────────────────┤
│  感官 (Senses)       我看到的图片和文件         │  1 方法
├─────────────────────────────────────────────┤
│  认知 (Cognition)    我知道谁是谁              │  2 方法
└─────────────────────────────────────────────┘
  核心: 8 个抽象动作 + 1 个事件流
```

| 层 | 抽象动作 | 方法签名 | 为什么不可去掉 |
|----|---------|---------|-------------|
| 身份 | 我是谁 | `get_identity() → BotIdentity` | 不知道自己是谁，无法过滤自己的消息 |
| 感知 | 我看到了什么 | 事件流 → `asyncio.Queue` | 不感知外界就无法存在 |
| 表达 | 我说了什么 | `send(OutgoingMessage) → str?` | 不能说话的 Agent 没有意义 |
| 存在感 | 我在想 | `start_thinking(message_id) → handle` | 人收到消息后会显示"正在处理" |
| 存在感 | 我想完了 | `stop_thinking(message_id, handle)` | 处理完成，清除处理中信号 |
| 感官 | 我看到了图片 | `fetch_media(msg_id, key) → (data, mime)` | 多模态理解能力 |
| 认知 | 这是谁 | `resolve_name(user_id) → str` | 对话中必须知道对方叫什么 |
| 认知 | 群里有谁 | `list_members(chat_id) → [Member]` | 群聊需要知道参与者 |

### 可选行为（有则更好，无则降级）

| 行为 | 方法 | 降级策略 |
|------|------|---------|
| 表情回应 | `react(message_id, emoji) → handle` | 跳过 |
| 撤销表情 | `unreact(message_id, handle)` | 跳过 |
| 改口 | `edit(message_id, new_content) → bool` | 发新消息更正 |
| 撤回 | `unsend(message_id) → bool` | 不撤回 |
| 按钮交互 | `card.action` 事件 | 文字确认 |

### v1.2 → v1.3 关键变更

**react/unreact 为什么从核心降为可选？**

v1.2 将 `react/unreact` 列为核心，同时将 `show_typing` 列为可选，但实际代码中 reaction 的**主要用途**就是"正在处理"指示器（飞书的 `add_reaction("OnIt")`）。这和 Discord 的 `trigger_typing()` 是**同一个意图的不同实现**。

v1.3 的修正：
- 提取"正在处理"这个**意图**为核心抽象 → `start_thinking` / `stop_thinking`
- 适配器自行选择实现机制（reaction、typing indicator、chat action...）
- 一般性的 emoji 表情回应（👍、❤️）降为可选 — 缺少它 Agent 仍能完整对话

---

## 4. 标准化数据类型

### 4.1 枚举

```python
class ChatType(str, Enum):
    PRIVATE = "private"       # 一对一私聊
    GROUP = "group"           # 多人群聊

class SenderType(str, Enum):
    USER = "user"             # 人类
    BOT = "bot"               # 机器人

class MessageType(str, Enum):
    TEXT = "text"             # 纯文本
    IMAGE = "image"           # 图片
    RICH_TEXT = "rich_text"   # 富文本
    FILE = "file"             # 文件
    AUDIO = "audio"           # 语音
    VIDEO = "video"           # 视频
    STICKER = "sticker"       # 贴纸
    SHARE = "share"           # 分享（链接/群/名片）
    UNKNOWN = "unknown"       # 未识别
```

### 4.2 Mention — @提及

```python
@dataclass
class Mention:
    user_id: str
    name: str
    is_bot_self: bool         # 是否 @了本 bot
```

### 4.3 IncomingMessage — 感知到的消息

适配器必须将平台原始消息**完整转换**为此格式。内核不接触任何平台原始对象。

```python
@dataclass
class IncomingMessage:
    message_id: str
    chat_id: str
    chat_type: ChatType
    sender_id: str
    sender_type: SenderType
    sender_name: str              # 适配器必须填充真名
    message_type: MessageType
    text: str                     # 已完成占位符替换的最终文本（Markdown）
    mentions: list[Mention]       # 已解析的 @列表
    is_mention_bot: bool
    image_keys: list[str]         # 媒体资源标识
    reply_to_id: str = ""         # 此消息引用回复的目标消息 ID（空 = 非回复）
    timestamp: int                # Unix 毫秒
    raw: Any = None               # 内核不访问
```

**适配器硬性要求：**
1. `text` — 所有占位符已替换为 `@真名`，本 bot 的 @ 已移除
2. `sender_name` — 真名，不允许返回 `cli_xxx`、`ou_xxx` 等原始 ID
3. `sender_type` — 正确区分 USER / BOT
4. 消息去重 — 适配器的责任

### 4.4 OutgoingMessage — 要说的话

一个人说话时不会想"我调哪个 API"。他就是**说了一句话**，可能回复某条，可能带格式。

```python
@dataclass
class OutgoingMessage:
    chat_id: str
    text: str = ""                       # Markdown 文本（始终填充，作为内容和降级后备）
    reply_to: str = ""                   # 引用回复的消息 ID（空 = 不引用）
    mentions: list[Mention] = field(default_factory=list)  # 需要 @的人
    card: dict | None = None             # 结构化卡片（可选，见 §15）
```

**内容分发规则（适配器执行）：**

```
OutgoingMessage 到达适配器
│
├─ card 非空？
│   ├─ 是 + has_rich_cards → 渲染为平台原生卡片（飞书 interactive / Discord Embed）
│   └─ 是 + !has_rich_cards → 忽略 card，使用 text 作为降级
│
├─ card 为空
│   ├─ has_markdown → 发送 text，保留 Markdown 格式
│   └─ !has_markdown → strip Markdown 后发纯文本
│       （飞书补偿：检测到代码块等复杂格式时自动切卡片）
│
├─ reply_to 非空？
│   ├─ has_reply → 引用回复
│   └─ !has_reply → 降级为普通发送
│
└─ mentions 非空？
    └─ 将 @name 转换为平台原生格式（飞书 <at> / Discord <@id>）
```

**核心约束：`text` 始终有意义。** 无论是否附带 card，`text` 都应包含完整的文字内容。`card` 是同一信息的**结构化增强呈现**，不是独立于 `text` 的另一条消息。当平台不支持卡片时，`text` 就是全部内容，不会丢失信息。

**与 v1.1 的 5 个方法的对应关系：**

| v1.1 方法 | v1.3 等价 |
|-----------|----------|
| `send_text(chat_id, text)` | `send(OutgoingMessage(chat_id, text))` |
| `reply_text(msg_id, text)` | `send(OutgoingMessage(chat_id, text, reply_to=msg_id))` |
| `send_card(chat_id, card)` | `send(OutgoingMessage(chat_id, text, card=card))` |
| `reply_card(msg_id, card)` | `send(OutgoingMessage(chat_id, text, reply_to=msg_id, card=card))` |
| `format_mention(user_id)` | `OutgoingMessage.mentions` 字段 |

### 4.5 BotIdentity — 我是谁

```python
@dataclass
class BotIdentity:
    bot_id: str
    bot_name: str
```

### 4.6 ChatMember — 群里的人

```python
@dataclass
class ChatMember:
    user_id: str
    name: str
    is_bot: bool
```

### 4.7 Reaction — 表情回应

```python
@dataclass
class Reaction:
    reaction_id: str
    chat_id: str              # 所属会话（适配器必须填充，内核不应自行反查）
    message_id: str
    emoji: str
    operator_id: str
    operator_type: SenderType
```

> **v1.4 变更**：新增 `chat_id` 字段。内核需要 `chat_id` 来更新 bot 协作的 thinking_signals 映射表。
> v1.3 中 Reaction 只有 `message_id`，内核被迫遍历消息缓冲区反查所属群聊（O(n)，且消息不在缓冲区时直接丢弃）。
> 适配器离平台更近，填充 `chat_id` 的成本更低。

### 4.8 CardAction — 按钮交互

```python
@dataclass
class CardAction:
    action_type: str          # "confirm" / "cancel" / "button_click"
    value: dict
    operator_id: str
    message_id: str = ""
```

---

## 5. 身份 — 我是谁

```python
class PlatformAdapter(ABC):

    @property
    @abstractmethod
    def capabilities(self) -> PlatformCapabilities:
        """我能做什么。

        内核通过此属性获取适配器的能力声明，据此决定降级策略。
        返回值应为常量，不随运行时变化。
        """
        ...

    @abstractmethod
    async def get_identity(self) -> BotIdentity:
        """我是谁。

        启动时调用。内核用 bot_id 过滤自己的消息。
        """
        ...
```

---

## 6. 感知 — 我看到了什么

### 6.1 连接

```python
    @abstractmethod
    async def connect(self, queue: asyncio.Queue) -> None:
        """开始感知世界。

        适配器建立与平台的连接，将所有事件转换为标准格式后投入 queue。

        核心契约：
        - queue 中必须包含会话中**所有参与者的消息**（含其他 bot）
        - 如平台原生不推 bot 消息，适配器内部补偿，对内核透明
        - 消息去重、token 管理等全部在适配器内部完成

        连接稳定性契约：
        - 适配器负责维护连接的存活性
        - 连接断开时适配器必须自动重连（指数退避），对内核透明
        - 重连期间不向 queue 投递任何事件（内核无感知），重连成功后恢复
        - 如果重连持续失败（达到上限），向 queue 投递一个特殊事件通知内核
        - 内核只调用一次 connect()，不负责监控或重启连接
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """停止感知，释放资源。优雅关闭底层连接。"""
        ...
```

### 6.2 标准事件类型

适配器将一切外部发生的事情归一化为以下事件投入队列：

```python
# ── 有人说话了 ──
{
    "event_type": "message",
    "message": IncomingMessage,
}

# ── 有人对消息做出反应 ──
{
    "event_type": "reaction",
    "reaction": Reaction,
}

# ── 群组成员变动 ──
{
    "event_type": "member_change",
    "chat_id": str,
    "change_type": "bot_joined"      # bot_joined / bot_left / user_joined / user_left
    "users": [{"user_id": str, "name": str}],  # 变动涉及的用户（bot_joined/left 时为空列表）
}

# ── 有人点了按钮（可选能力）──
{
    "event_type": "interaction",
    "action": CardAction,
}

# ── 内核内部定时事件（非平台产生）──
{
    "event_type": "internal_timer",
    "chat_id": str,
    "timer_type": str,               # "eval_timeout" / "debounce" / etc.
}
```

**关于 reaction 事件与 bot 协作：**

内核通过监听 `reaction` 事件来感知其他 bot 的处理状态。当适配器的 `start_thinking` 实现使用 reaction 机制时（如飞书），该 reaction 会自然产生 `reaction` 事件，被其他 bot 实例接收。内核据此判断"已有人在处理"，避免重复回答。这是现有 `_thinking_signals` 机制的自然延续，无需额外事件类型。

### 6.3 消息完整性契约

> **适配器必须保证事件队列中收到会话中的全部消息，无论发送者是人类还是 bot。**

| 平台 | 如何满足 |
|------|---------|
| 飞书 | WS 推人类消息 + 后台轮询 REST 补 bot 消息 |
| Discord | Gateway 直接全推 |
| Telegram | Bot API 直接全推 |
| Slack | Events API 直接全推 |

### 6.4 成员变动检测

> **适配器必须在检测到成员变动时投递 `member_change` 事件。**

| 平台 | 如何检测 |
|------|---------|
| 飞书 | WS 事件 + HTTP 400 副作用推断退群 |
| Discord | `on_member_join` / `on_member_remove` / `on_guild_remove` |
| Telegram | `chat_member_updated` |

---

## 7. 表达 — 我说了什么

```python
    @abstractmethod
    async def send(self, message: OutgoingMessage) -> str | None:
        """说话。

        统一的消息发送接口。适配器根据 OutgoingMessage 的字段决定最终形式。
        详细的内容分发规则见 §4.4。

        消息超长处理（适配器职责）：
        - 当 text 超过 max_message_length 时，适配器自行决定策略：
          a) 自动分条发送（推荐，在段落/换行处分割）
          b) 截断 + 尾部追加截断提示
          c) 转为卡片/Embed（如果 has_rich_cards 且卡片容量更大）
        - 内核不关心具体策略，只要信息不丢失
        - 分条发送时返回最后一条的 message_id

        Returns:
            发送成功返回 message_id，失败返回 None。
        """
        ...
```

---

## 8. 存在感 — 我在处理

人类在聊天中收到一条需要时间处理的消息时，会发出"我在看了"的信号。
这个信号的**机制**因平台而异，但**意图**完全相同：

| 平台 | "我在处理"的机制 |
|------|---------------|
| 飞书 | `add_reaction("OnIt")` — 没有 typing 指示器，用 reaction 替代 |
| Discord | `trigger_typing()` + 可选 `add_reaction("⏳")` |
| Telegram | `send_chat_action("typing")` |
| Slack | 无原生 typing for bots — 可 reaction 或跳过 |

v1.2 将这些拆成了两个独立概念：核心的 `react/unreact` 和可选的 `show_typing`。
但它们服务于**同一个意图**。v1.3 将其统一为：

```python
    @abstractmethod
    async def start_thinking(self, message_id: str) -> str | None:
        """表达"我收到了，正在处理"。

        收到消息后、开始长时间处理前调用。
        适配器选择平台上最合适的机制来表达这个意图。

        契约（MUST）：
        - 信号必须对会话中其他参与者可见（含其他 bot）
        - 如果同一个群聊中部署了多个 bot，信号必须使用能产生
          `reaction` 事件的机制（如 add_reaction），以便其他 bot
          的适配器接收到该事件，内核据此判断"已有人在处理"

        为什么必须用 reaction 而非 typing：
        - typing indicator 是单向的（只有人类能看到），bot 无法监听
        - reaction 是双向的（产生事件，其他 bot 能通过 WS 收到）
        - 即使平台支持 typing，也应**同时**添加 reaction 用于 bot 协作

        实现参考：
        - 飞书: add_reaction("OnIt") — 无 typing，reaction 是唯一选择
        - Discord: trigger_typing() + add_reaction("⏳") — 双重信号
        - Telegram: add_reaction("⏳") + send_chat_action("typing")
        - 单 bot 场景 / 本地终端: 无需 reaction，空操作即可

        Returns:
            handle（传给 stop_thinking 用于清除），失败返回 None。
        """
        ...

    @abstractmethod
    async def stop_thinking(self, message_id: str, handle: str) -> None:
        """清除"正在处理"信号。

        回复完成后调用。如果 start_thinking 使用了 reaction，
        则此方法移除该 reaction；如果用的是 typing，则自然消失，此方法为空操作。
        """
        ...
```

**典型使用模式（与现有代码完全一致）：**

```python
# 当前 router.py:
reaction_id = await self.sender.add_reaction(message_id, self._thinking_emoji)
try:
    reply = await self._reply_with_tool_loop(...)
finally:
    if reaction_id:
        await self.sender.remove_reaction(message_id, reaction_id)

# 抽象后:
handle = await adapter.start_thinking(message_id)
try:
    reply = await self._reply_with_tool_loop(...)
finally:
    if handle:
        await adapter.stop_thinking(message_id, handle)
```

内核代码**零逻辑变更**，只换了方法名。但适配器获得了自由 — 不再被迫使用 reaction 机制。

---

## 9. 感官 — 我看到的图片和文件

```python
    @abstractmethod
    async def fetch_media(
        self, message_id: str, resource_key: str,
    ) -> tuple[str, str] | None:
        """获取消息中的媒体内容。

        Args:
            resource_key: 来自 IncomingMessage.image_keys

        Returns:
            (base64_data, mime_type) 或 None

        适配器职责：
        - 鉴权下载（飞书需 token，Discord 直接 GET）
        - 大文件自动压缩（建议阈值 10MB）
        - 格式归一化为 base64 + MIME type
        """
        ...
```

---

## 10. 认知 — 我知道谁是谁

```python
    @abstractmethod
    async def resolve_name(self, user_id: str) -> str:
        """这个 ID 是谁？

        适配器内部用任何手段解决（缓存、API、推断），
        内核只关心结果。查不到返回 ID 尾部截断。
        """
        ...

    @abstractmethod
    async def list_members(self, chat_id: str) -> list[ChatMember]:
        """这个群里有谁？

        返回完整成员列表（含 bot），is_bot 正确标记。
        适配器内部缓存 + 按需刷新。
        """
        ...
```

---

## 11. 可选行为

以下行为**不在核心 8 个动作中**，但能让 Agent 更像人类。适配器通过能力声明来标识是否支持。

### 11.1 react — 表情回应

```python
    async def react(self, message_id: str, emoji: str) -> str | None:
        """对一条消息做出表情反应。

        Args:
            emoji: 平台无关标识（如 "thumbsup", "heart", "eyes"）
                   适配器内部映射到平台原生 emoji

        Returns:
            reaction_handle（用于 unreact），失败返回 None。

        用途：表达情绪（"我喜欢这条消息"、"收到"等）。
        注意：处理中指示使用核心的 start_thinking/stop_thinking，不用 react。
        """
        ...

    async def unreact(self, message_id: str, handle: str) -> bool:
        """撤销之前的表情反应。"""
        ...
```

### 11.2 edit — 改口

```python
    async def edit(self, message_id: str, new_content: OutgoingMessage) -> bool:
        """修改已发的消息。

        用途：
        - 更新正在执行的任务状态（"正在搜索..." → "搜索完成，共 3 条结果"）
        - 修正 LLM 的错误回复

        不支持时返回 False，内核降级为发新消息。

        - Discord: message.edit()
        - Telegram: edit_message_text()
        - 飞书: 不支持编辑已发消息
        """
        ...
```

### 11.3 unsend — 撤回

```python
    async def unsend(self, message_id: str) -> bool:
        """撤回已发的消息。

        用途：
        - 清理临时的处理状态消息
        - 撤回误发内容

        不支持时返回 False。

        - Discord: message.delete()
        - Telegram: delete_message()
        - 飞书: 不支持撤回自己发的消息
        """
        ...
```

---

## 12. 能力声明

```python
@dataclass
class PlatformCapabilities:
    # ── 表达 ──
    has_reply: bool = True               # 支持引用回复
    has_markdown: bool = True            # 文本消息渲染 Markdown
    has_rich_cards: bool = False         # 支持卡片/Embed
    has_card_actions: bool = False       # 卡片支持交互按钮
    max_message_length: int = 4000      # 单条消息字符上限

    # ── 感官 ──
    has_media_download: bool = False     # 支持下载图片/文件

    # ── 认知 ──
    has_group_members: bool = False      # 支持查询群成员
    has_mentions: bool = True            # 支持 @提及

    # ── 可选行为 ──
    has_reactions: bool = False          # 支持 react/unreact
    has_edit: bool = False               # 支持 edit
    has_unsend: bool = False             # 支持 unsend
```

> **注意：`start_thinking`/`stop_thinking` 没有能力标志。**
> 它们是核心方法，适配器必须实现。但实现可以是空操作 —
> 如果平台既不支持 reaction 也不支持 typing indicator，
> `start_thinking` 返回 `None`，`stop_thinking` 为空操作即可。
> 内核代码无需任何改变。

### 降级逻辑

| 能力缺失 | 内核行为 |
|---------|---------|
| `has_reply = False` | `reply_to` 被忽略，降级为普通发送 |
| `has_rich_cards = False` | `card` 被忽略，使用 `text` 作为降级 |
| `has_media_download = False` | 图片消息降级为 `[图片]` 文字描述 |
| `has_group_members = False` | 跳过群成员相关上下文 |
| `has_card_actions = False` | 审批降级为文字确认 |
| `has_reactions = False` | 跳过一般性 emoji 回应 |
| `has_edit = False` | 状态更新改为发新消息 |
| `has_unsend = False` | 不撤回 |

---

## 13. 平台配置

```python
@dataclass
class PlatformConfig(ABC):
    platform_type: str               # "feishu" / "discord" / "telegram"
    owner_chat_id: str = ""          # 主人的会话 ID（用于主动消息）

    @abstractmethod
    def validate(self) -> list[str]:
        """校验完整性，返回错误列表。"""
        ...
```

---

## 14. 外部服务层（非平台抽象）

日历不是聊天平台的本质能力。飞书恰好内建了日历，但 Discord/Telegram 没有。
日历（以及未来的邮件、TODO、文档等）属于**外部服务层**，与平台适配器平行：

```
┌─────────────┐   ┌──────────────┐   ┌──────────────┐
│ 飞书适配器    │   │ Discord适配器 │   │ Telegram适配器│   ← 平台层
└──────┬──────┘   └──────┬───────┘   └──────┬───────┘
       │                 │                   │
       └────────────┬────┘───────────────────┘
                    │
             ┌──────▼──────┐
             │  LingQue 内核 │
             └──────┬──────┘
                    │
       ┌────────────┼────────────┐
       │            │            │
  ┌────▼────┐  ┌────▼────┐  ┌───▼────┐
  │飞书日历   │  │Google日历│  │Outlook │     ← 服务层
  └─────────┘  └─────────┘  └────────┘
```

### CalendarService 接口（独立于平台）

```python
class CalendarService(ABC):
    @abstractmethod
    async def create_event(
        self, summary: str, start_time: str, end_time: str, description: str = "",
    ) -> dict: ...

    @abstractmethod
    async def list_events(self, start_time: str, end_time: str) -> list[CalendarEvent]: ...

@dataclass
class CalendarEvent:
    event_id: str
    summary: str
    description: str = ""
    start_time: str = ""
    end_time: str = ""
```

飞书的 `FeishuCalendar` 实现此接口，但不属于平台适配器。
Discord 接入时，配置一个 `GoogleCalendar` 实现同一接口即可。

---

## 15. 标准卡片结构

`OutgoingMessage.card` 使用以下平台无关结构，适配器负责转换：

```python
# 信息卡片
{"type": "info", "title": "标题", "content": "Markdown", "color": "blue",
 "fields": [{"key": "字段名", "value": "值", "short": True}]}

# 日程卡片
{"type": "schedule",
 "events": [{"start_time": "09:00", "end_time": "10:00", "summary": "会议"}]}

# 任务卡片
{"type": "task_list",
 "tasks": [{"title": "任务名", "done": True}]}

# 错误卡片
{"type": "error", "title": "错误标题", "message": "详情"}

# 确认卡片
{"type": "confirm", "title": "操作审批", "content": "描述",
 "confirm_text": "确认", "cancel_text": "取消",
 "callback_data": {"type": "approval", "id": "xxx"}}
```

降级：`has_rich_cards = False` 时，适配器提取 title + content 拼为纯文本。

---

## 16. 飞书适配指南

### 能力声明

```python
FEISHU_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=False,          # 文本消息不渲染 Markdown
    has_rich_cards=True,
    has_card_actions=True,
    has_media_download=True,
    has_group_members=True,
    has_mentions=True,
    has_reactions=True,          # 支持一般性 emoji 回应
    has_edit=False,              # 飞书不支持编辑已发消息
    has_unsend=False,            # 飞书不支持撤回已发消息
    max_message_length=10000,
)
```

### 适配器核心方法映射

| 抽象方法 | 飞书实现 |
|---------|---------|
| `get_identity()` | `GET /bot/v3/info` → `BotIdentity` |
| `connect(queue)` | `lark_oapi.ws.Client` (daemon thread) + `_poll_bot_messages` (后台) |
| `send(msg)` | `reply_to` 判断 → `CreateMessage` / `ReplyMessage`；`card` 判断 → `msg_type="interactive"` / `"text"`；`mentions` → `<at>` 标签；Markdown → strip 或自动切卡片 |
| `start_thinking(msg_id)` | `POST /messages/{id}/reactions` body=`{"emoji_type":"OnIt"}` → 返回 reaction_id 作为 handle |
| `stop_thinking(msg_id, handle)` | `DELETE /messages/{id}/reactions/{handle}` |
| `fetch_media(msg_id, key)` | `GET /messages/{id}/resources/{key}` + 压缩 |
| `resolve_name(user_id)` | 缓存 → 群成员 API → 联系人 API → bot 推断 |
| `list_members(chat_id)` | `GET /chats/{id}/members` + bot 信号注册 |

### 适配器内部补偿行为（对内核不可见）

1. **Bot 消息补漏轮询** — 后台 3-5 秒轮询 REST API，补充 WS 收不到的 bot 消息
2. **Bot 身份推断** — 排除法/时序法将 cli_xxx → 真名，持久化到 bot_identities.json
3. **群组退出检测** — HTTP 400 / 连续 3 次轮询失败 → 投递 member_change(bot_left)
4. **Token 自动刷新** — tenant_access_token 2 小时有效，提前 5 分钟刷新
5. **Markdown 发送策略** — 代码块检测 → 自动切卡片；纯文本 strip Markdown
6. **receive_id_type 推断** — 根据 oc_/ou_/on_ 前缀推断 API 参数
7. **@提及处理** — 入站：@_user_N → @真名；出站：@名字 → `<at>` 标签
8. **消息去重** — WS + REST 双重去重，滑动窗口 200 条

---

## 17. Discord 适配指南

### 能力声明

```python
DISCORD_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=True,
    has_rich_cards=True,             # Embed
    has_card_actions=True,           # Button components
    has_media_download=True,
    has_group_members=True,
    has_mentions=True,
    has_reactions=True,
    has_edit=True,                   # message.edit()
    has_unsend=True,                 # message.delete()
    max_message_length=2000,
)
```

### 核心映射

| 抽象方法 | Discord 实现 |
|---------|-------------|
| `get_identity()` | `client.user` |
| `connect(queue)` | `discord.Client` + on_message / on_raw_reaction_add / etc. |
| `send(msg)` | `channel.send()` / `message.reply()`；card → `Embed`；mentions → `<@id>` |
| `start_thinking(msg_id)` | `channel.trigger_typing()` + `message.add_reaction("⏳")` → reaction_id |
| `stop_thinking(msg_id, handle)` | `reaction.remove()` (typing 自然消失) |
| `fetch_media` | `attachment.url` 直接 HTTP GET |
| `resolve_name` | `guild.get_member()` / `client.fetch_user()` |
| `list_members` | `guild.members` |
| `react` / `unreact` | `message.add_reaction()` / `reaction.remove()` |
| `edit` | `message.edit()` |
| `unsend` | `message.delete()` |

### 不需要的飞书补偿

| 飞书补偿 | 为什么 Discord 不需要 |
|---------|-------------------|
| Bot 消息轮询 | Gateway 全推 |
| Bot 身份推断 | bot.user 有 id 和 name |
| 群成员补丁注册 | Guild.members 完整 |
| HTTP 400 退群检测 | on_guild_remove |
| Markdown 降级 | 原生支持 |
| Token 刷新 | Bot Token 长期有效 |

---

## 附录 A：内核改造清单

### router.py

| 当前 | 改为 |
|------|------|
| `sender.send_text()` / `reply_text()` / `send_card()` / `reply_card()` | `adapter.send(OutgoingMessage(...))` |
| `sender.add_reaction(msg_id, self._thinking_emoji)` | `adapter.start_thinking(msg_id)` |
| `sender.remove_reaction(msg_id, reaction_id)` | `adapter.stop_thinking(msg_id, handle)` |
| `self._thinking_emoji` 硬编码 | 删除，适配器内部决定机制 |
| `sender._user_name_cache` 直接访问 | `adapter.resolve_name()` |
| `self._replace_at_mentions()` 生成飞书 `<at>` 标签 | `OutgoingMessage.mentions` 字段，适配器处理 |
| `_extract_text()` / `_extract_image_keys()` / `_resolve_at_mentions()` | 移入飞书适配器 |
| `_handle_card_action()` 访问飞书 SDK 属性 | 接收标准 `CardAction` |
| `from lq.feishu.cards import` | 使用标准卡片 dict |
| `sender.is_chat_left()` / `register_bot_member()` / `fetch_chat_messages()` | 删除，改为监听 `member_change` 事件 |
| `_dispatch_message(event)` 访问飞书 SDK 属性 | 接收 `IncomingMessage` |

### gateway.py

| 当前 | 改为 |
|------|------|
| 硬编码 `FeishuSender` + `FeishuListener` | 通过配置创建 `PlatformAdapter` |
| `_poll_active_groups()` | 删除，移入飞书适配器 |
| `from lq.feishu.cards import` | 使用标准卡片 dict |
| 构造飞书 fake event | 构造 `IncomingMessage` |

### config.py

| 当前 | 改为 |
|------|------|
| `LQConfig.feishu: FeishuConfig` | `LQConfig.platform: PlatformConfig` |
| 日历耦合在飞书配置中 | `LQConfig.calendar: CalendarConfig`（独立） |

---

## 附录 B：v1.3 → v1.4 变更记录

### 修复 6 个设计缺陷

| # | v1.3 问题 | v1.4 修复 | 严重度 |
|---|----------|----------|--------|
| 1 | `PlatformAdapter` 缺少 `capabilities` 属性，内核无法获知适配器能力 | §5 新增 `capabilities` 抽象属性 | **结构性** |
| 2 | `Reaction` 缺少 `chat_id`，内核被迫 O(n) 遍历 buffer 反查群聊 | §4.7 新增 `chat_id` 字段 | 重要 |
| 3 | `connect()` 未声明重连责任，连接断开后行为未定义 | §6.1 新增连接稳定性契约 | 重要 |
| 4 | `start_thinking` 的 bot 协作契约用"应"而非"必须"，Telegram typing 不产生 reaction 事件 | §8 强化为 MUST + 解释 reaction vs typing 的区别 | 中等 |
| 5 | `send()` 对超过 `max_message_length` 的消息无处理规定 | §7 新增消息超长处理策略 | 中等 |
| 6 | `IncomingMessage` 缺少 `reply_to_id`，不知道消息在回复谁 | §4.3 新增 `reply_to_id` 字段 | 次要 |

### 核心变更不变

v1.4 没有改变核心架构。仍然是 **8 核心 + 4 可选 + 1 事件流**。所有变更都是补全 v1.3 遗漏的细节。

---

## 附录 C：历史变更摘要

| 版本 | 核心变更 |
|------|---------|
| v1.0 | 初始设计：25 个抽象动作，全量映射飞书交互 |
| v1.1 | 分离"抽象需求 vs 平台补偿"，移除 6 个飞书补偿行为 |
| v1.2 | send 五合一、日历抽离为外部服务层、事件归并、新增 show_typing/edit/unsend |
| v1.3 | "一个意图一个抽象" — react/typing 统一为 start/stop_thinking；OutgoingMessage 内容分发规则明确化 |
| v1.4 | 补全结构缺陷 — adapter.capabilities 属性、Reaction.chat_id、connect 重连契约、start_thinking 强制 reaction、send 超长处理、IncomingMessage.reply_to_id |
