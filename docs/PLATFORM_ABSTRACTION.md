# 聊天平台抽象层接口规范

> LingQue 平台无关通信协议 v1.2
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
8. [情绪 — 我的即时反应](#8-情绪--我的即时反应)
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
19. [附录 B：v1.1 → v1.2 变更记录](#附录-bv11--v12-变更记录)

---

## 1. 设计原则

- **描述人的行为，不描述 API 的形状**：接口按"一个人在聊天中做什么"来组织，不按平台 API 的技术结构。
- **最小完备**：每个接口都不可再拆，也不可移除。如果去掉它，Agent 就不再像一个完整的对话参与者。
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

Agent 说: "这条消息的发送者叫什么"
  飞书适配器: 查缓存 → 拉群成员 → 联系人 API → cli_xxx 推断
  Discord 适配器: message.author.display_name
  Agent 看到的: IncomingMessage.sender_name = "小明"
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
│  情绪 (Emotion)      我的即时反应              │  2 方法
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
| 情绪 | 我在想 | `react(message_id, emoji) → handle` | 非语言信号，人类交互的基本组成 |
| 情绪 | 我想完了 | `unreact(message_id, handle)` | 状态回收（"正在输入"→ 消失） |
| 感官 | 我看到了图片 | `fetch_media(msg_id, key) → (data, mime)` | 多模态理解能力 |
| 认知 | 这是谁 | `resolve_name(user_id) → str` | 对话中必须知道对方叫什么 |
| 认知 | 群里有谁 | `list_members(chat_id) → [Member]` | 群聊需要知道参与者 |

### 可选行为（有则更好，无则降级）

| 行为 | 方法 | 降级策略 |
|------|------|---------|
| 正在输入 | `show_typing(chat_id)` | 跳过 |
| 改口 | `edit(message_id, new_text) → bool` | 发新消息更正 |
| 撤回 | `unsend(message_id) → bool` | 不撤回 |
| 按钮交互 | `card.action` 事件 | 文字确认 |

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
    timestamp: int                # Unix 毫秒
    raw: Any = None               # 内核不访问
```

**适配器硬性要求：**
1. `text` — 所有占位符已替换为 `@真名`，本 bot 的 @ 已移除
2. `sender_name` — 真名，不允许返回 `cli_xxx`、`ou_xxx` 等原始 ID
3. `sender_type` — 正确区分 USER / BOT
4. 消息去重 — 适配器的责任

### 4.4 OutgoingMessage — 要说的话

**v1.2 核心变更：取代 v1.1 的 send_text / reply_text / send_card / reply_card 四个方法。**

一个人说话时不会想"我调哪个 API"。他就是**说了一句话**，可能回复某条，可能带格式。

```python
@dataclass
class OutgoingMessage:
    chat_id: str
    text: str = ""                       # Markdown 文本
    reply_to: str = ""                   # 引用回复的消息 ID（空 = 不引用）
    mentions: list[Mention] = field(default_factory=list)  # 需要 @的人
    card: dict | None = None             # 富内容卡片（与 text 二选一）
```

适配器收到 `OutgoingMessage` 后自行决定：
- `reply_to` 非空 + 平台支持引用 → 引用回复；否则普通发送
- `card` 非空 + 平台支持卡片 → 发卡片；否则从 card 提取文本发纯文本
- `mentions` 非空 → 将 `@name` 转换为平台原生格式（飞书 `<at>` / Discord `<@id>`）
- `text` 含 Markdown → 平台支持则保留，不支持则 strip

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
    message_id: str
    emoji: str
    operator_id: str
    operator_type: SenderType
```

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

    @abstractmethod
    async def get_identity(self) -> BotIdentity:
        """我是谁。

        启动时调用。内核用 bot_id 过滤自己发的消息。
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
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """停止感知，释放资源。"""
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

# ── 群组成员变动（合并了 v1.1 的 3 个事件）──
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

        统一的消息发送接口。适配器根据 OutgoingMessage 的字段自行决定：
        - reply_to 非空 → 引用回复（平台不支持则降级为普通发送）
        - card 非空 → 富内容（平台不支持则提取文本发纯文本）
        - mentions 非空 → 将 @name 转为平台原生格式
        - text 含 Markdown → 平台支持则保留，否则 strip

        Returns:
            发送成功返回 message_id，失败返回 None。
        """
        ...
```

**v1.2 变更：取代 v1.1 的 `send_text` / `reply_text` / `send_card` / `reply_card` / `format_mention` 五个方法。**

为什么合并：
- 人类不会想"我要 reply_card 还是 send_text"。他就是说了一句话。
- 引用回复 vs 新消息 → `reply_to` 字段
- 纯文本 vs 卡片 → `text` vs `card` 字段
- @提及 → `mentions` 字段，适配器内部处理格式转换
- Markdown 渲染 → 适配器的责任

---

## 8. 情绪 — 我的即时反应

表情回应是**非语言信号**，人类在聊天中大量使用。

```python
    @abstractmethod
    async def react(self, message_id: str, emoji: str) -> str | None:
        """对一条消息做出表情反应。

        Args:
            emoji: 平台无关标识（如 "thinking", "thumbsup", "eyes"）
                   适配器内部映射到平台原生 emoji

        Returns:
            reaction_handle（用于 unreact），失败返回 None。

        主要用途：
        1. 处理中指示 — 收到消息时 react("thinking")，回复后 unreact
        2. Bot 间协作 — 信号"我在处理这个问题"，避免重复回答
        """
        ...

    @abstractmethod
    async def unreact(self, message_id: str, handle: str) -> bool:
        """撤销之前的表情反应。"""
        ...
```

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

### 11.1 show_typing — 正在输入

```python
    async def show_typing(self, chat_id: str) -> None:
        """让对方看到"正在输入..."。

        比 react("thinking") 更自然的处理中指示。
        平台不支持时空实现即可。

        - Discord: channel.trigger_typing()
        - Telegram: send_chat_action("typing")
        - 飞书: 无原生支持，空实现
        """
        ...
```

### 11.2 edit — 改口

```python
    async def edit(self, message_id: str, new_text: str) -> bool:
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

    # ── 情绪 ──
    has_reactions: bool = False          # 支持表情回应

    # ── 感官 ──
    has_media_download: bool = False     # 支持下载图片/文件

    # ── 认知 ──
    has_group_members: bool = False      # 支持查询群成员
    has_mentions: bool = True            # 支持 @提及

    # ── 可选行为 ──
    has_typing: bool = False             # 支持 show_typing
    has_edit: bool = False               # 支持 edit
    has_unsend: bool = False             # 支持 unsend
```

### 降级逻辑

| 能力缺失 | 内核行为 |
|---------|---------|
| `has_reply = False` | `reply_to` 被忽略，降级为普通发送 |
| `has_rich_cards = False` | `card` 被提取文本后发纯文本 |
| `has_reactions = False` | 跳过处理中指示器、bot 间意图信号 |
| `has_media_download = False` | 图片消息降级为 `[图片]` 文字描述 |
| `has_group_members = False` | 跳过群成员相关上下文 |
| `has_card_actions = False` | 审批降级为文字确认 |
| `has_typing = False` | 跳过 typing 指示器 |
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

**v1.2 核心变更：日历从平台抽象中抽离。**

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
    has_reactions=True,
    has_media_download=True,
    has_group_members=True,
    has_mentions=True,
    has_typing=False,            # 飞书无 typing 指示器
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
| `send(msg)` | `reply_to` 判断 → `CreateMessageRequest` / `ReplyMessageRequest`；`card` 判断 → `msg_type="interactive"` / `"text"`；`mentions` → `<at>` 标签；Markdown → strip 或自动切卡片 |
| `react(msg_id, emoji)` | `POST /messages/{id}/reactions` |
| `unreact(msg_id, handle)` | `DELETE /messages/{id}/reactions/{rid}` |
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
    has_reactions=True,
    has_media_download=True,
    has_group_members=True,
    has_mentions=True,
    has_typing=True,                 # channel.trigger_typing()
    has_edit=True,                   # message.edit()
    has_unsend=True,                 # message.delete()
    max_message_length=2000,
)
```

### 不需要的飞书补偿

| 飞书补偿 | 为什么 Discord 不需要 |
|---------|-------------------|
| Bot 消息轮询 | Gateway 全推 |
| Bot 身份推断 | bot.user 有 id 和 name |
| 群成员补丁注册 | Guild.members 完整 |
| HTTP 400 退群检测 | on_guild_remove |
| Markdown 降级 | 原生支持 |
| Token 刷新 | Bot Token 长期有效 |

### 核心映射

| 抽象方法 | Discord 实现 |
|---------|-------------|
| `get_identity()` | `client.user` |
| `connect(queue)` | `discord.Client` + on_message / on_raw_reaction_add / etc. |
| `send(msg)` | `channel.send()` / `message.reply()`；card → `Embed`；mentions → `<@id>` |
| `react` / `unreact` | `message.add_reaction()` / `reaction.remove()` |
| `fetch_media` | `attachment.url` 直接 HTTP GET |
| `resolve_name` | `guild.get_member()` / `client.fetch_user()` |
| `list_members` | `guild.members` |
| `show_typing` | `channel.trigger_typing()` |
| `edit` | `message.edit()` |
| `unsend` | `message.delete()` |

---

## 附录 A：内核改造清单

### router.py

| 当前 | 改为 |
|------|------|
| `sender.send_text()` / `reply_text()` / `send_card()` / `reply_card()` | `adapter.send(OutgoingMessage(...))` |
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

## 附录 B：v1.1 → v1.2 变更记录

### 合并

| v1.1 | v1.2 | 理由 |
|------|------|------|
| `send_text` + `reply_text` + `send_card` + `reply_card` + `format_mention` | `send(OutgoingMessage)` | 人类说话是一个动作，不是五个 |
| `bot.added_to_group` + `bot.removed_from_group` + `user.joined_group` | `member_change` 事件 | 都是成员变动 |
| `get_user_name` | `resolve_name` | 统一命名 |
| `get_group_members` | `list_members` | 统一命名 |

### 抽离

| v1.1 | v1.2 | 理由 |
|------|------|------|
| `CalendarService` 在平台抽象中 | 独立为外部服务层 | 日历不是聊天平台的事 |

### 新增

| 方法 | 理由 |
|------|------|
| `show_typing(chat_id)` | 比 react("thinking") 更自然的处理中指示 |
| `edit(message_id, text)` | 人类会改口 |
| `unsend(message_id)` | 人类会撤回 |

### 精简结果

| 版本 | 总数 | 构成 |
|------|------|------|
| v1.0 | 25 个 | 含 6 个飞书补偿 |
| v1.1 | 19 个 | 移除飞书补偿 |
| v1.2 | **8 核心 + 3 可选 + 1 事件流** | send 合一、日历抽离、事件归并 |
