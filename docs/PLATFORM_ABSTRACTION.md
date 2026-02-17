# 聊天平台抽象层接口规范

> LingQue 平台无关通信协议 v1.1
>
> 本文档定义了 LingQue 内核与外部聊天平台之间的全部交互契约。
> 任何新平台（Discord、Telegram、Slack、微信等）只需实现本文档定义的接口，即可接入 LingQue。

---

## 目录

1. [设计原则](#1-设计原则)
2. [核心哲学：抽象需求 vs 平台补偿](#2-核心哲学抽象需求-vs-平台补偿)
3. [标准化数据类型](#3-标准化数据类型)
4. [接口总览](#4-接口总览)
5. [连接与生命周期](#5-连接与生命周期-platformconnection)
6. [消息发送](#6-消息发送-messagesender)
7. [消息与事件接收](#7-消息与事件接收-eventlistener)
8. [身份与成员查询](#8-身份与成员查询-identityresolver)
9. [Reaction / 表情回应](#9-reaction--表情回应-reactionmanager)
10. [多媒体资源](#10-多媒体资源-mediahandler)
11. [日历集成（可选）](#11-日历集成可选-calendarservice)
12. [富内容卡片 / Embed](#12-富内容卡片--embed-richcontentbuilder)
13. [卡片交互回调](#13-卡片交互回调)
14. [平台能力声明](#14-平台能力声明-platformcapabilities)
15. [平台配置](#15-平台配置-platformconfig)
16. [飞书适配指南](#16-飞书适配指南)
17. [Discord 适配指南](#17-discord-适配指南)
18. [附录 A：内核改造清单](#附录-a内核改造清单)
19. [附录 B：完整动作清单](#附录-b完整动作清单)

---

## 1. 设计原则

- **内核零依赖**：LingQue 核心（router、memory、session、executor）不引用任何平台 SDK。所有平台交互通过本文档定义的抽象接口完成。
- **数据归一化**：不同平台的消息、用户、群组等概念统一为标准数据类型，内核只处理标准类型。
- **描述需求，不描述补偿**：抽象接口只定义**内核需要什么**，不定义**平台怎么满足**。平台特有的补偿行为（轮询、身份推断、格式转换等）封装在适配器内部，对内核完全透明。
- **能力声明制**：适配器通过 `PlatformCapabilities` 声明自身能力，内核据此降级或跳过功能。
- **异步优先**：所有 I/O 接口均为 `async def`。
- **事件驱动**：适配器将平台原始事件转换为标准事件，投入统一的事件队列，由内核消费。

---

## 2. 核心哲学：抽象需求 vs 平台补偿

设计抽象层时，最关键的区分是：**什么是内核的真实需求，什么是为了应付特定平台限制的补偿行为**。

### 内核的真实需求

内核关心的是：

| 我需要... | 而不是... |
|-----------|----------|
| 收到会话中的**所有消息**（含其他 bot 的） | 轮询 REST API 补漏 |
| 知道消息发送者的**名字** | 批量拉群成员 + 时序推断 bot 身份 |
| 知道群里**有哪些 bot** | 通过消息信号逐步注册 |
| 知道 bot 是否**还在群里** | 检测 HTTP 400 错误 |
| 把一段 Markdown 文本**发出去** | 判断是否含代码块，切换卡片/纯文本 |
| @一个用户 | 生成 `<at user_id="ou_xxx">名字</at>` 标签 |
| 给消息加一个**表情** | 管理 reaction_id、处理 token 刷新 |

### 适配器的补偿职责

以下行为是**飞书平台限制**的补偿，属于飞书适配器的内部实现，**绝对不应出现在抽象接口中**：

| 飞书限制 | 补偿行为 | 为什么其他平台不需要 |
|---------|---------|-------------------|
| WS 收不到其他 bot 的消息 | REST 轮询 `fetch_chat_messages` 补漏 | Discord/Telegram 的事件流天然包含所有 bot 消息 |
| 消息 API 对 bot 返回 app_id (cli_xxx) 而非 open_id | 通过时序法/排除法推断 `infer_bot_identities` | Discord/Telegram 的 bot 有统一 ID 体系 |
| 群成员 API 对 bot 信息不完整 | 通过消息信号逐步 `register_bot_member` | Discord 的 Guild.members 直接返回完整列表 |
| 无法直接检测 bot 已退群 | 通过 HTTP 400 副作用推断 `is_chat_left` | Discord 有 on_guild_remove 事件，Telegram 有相关 update |
| 文本消息不支持 Markdown 渲染 | 检测复杂 Markdown → 自动切换卡片发送 | Discord/Telegram 原生支持 Markdown |
| @提及使用占位符 @_user_N | 解析 mentions 数组替换占位符 | Discord 使用 `<@id>` 原始格式，解析更直接 |
| receive_id 需根据前缀推断类型 (oc_/ou_/on_) | `_infer_receive_id_type` | Discord/Telegram 用统一的 channel_id/chat_id |
| Token 有效期 2 小时 | `_get_tenant_token` 自动刷新 | Discord 用长期 Bot Token，无需刷新 |

### 原则：适配器对内核的承诺

适配器向内核承诺的是：**你要的数据和能力我都帮你搞定，你不需要知道我怎么做到的**。

```
内核说: "给我所有消息"
  飞书适配器: WS 收一半 + REST 轮询补另一半 → 统一投入事件队列
  Discord 适配器: Gateway 直接全收 → 投入事件队列
  内核看到的: 事件队列里源源不断的 IncomingMessage，一视同仁

内核说: "这条消息的发送者叫什么"
  飞书适配器: 查缓存 → 没有就拉群成员 → 还没有就调联系人 API → cli_xxx 走推断
  Discord 适配器: message.author.display_name
  内核看到的: IncomingMessage.sender_name = "小明"
```

---

## 3. 标准化数据类型

### 3.1 ChatType — 会话类型

```python
class ChatType(str, Enum):
    PRIVATE = "private"   # 一对一私聊
    GROUP = "group"       # 多人群聊
```

### 3.2 SenderType — 发送者类型

```python
class SenderType(str, Enum):
    USER = "user"         # 人类用户
    BOT = "bot"           # 机器人/应用
```

### 3.3 MessageType — 消息内容类型

```python
class MessageType(str, Enum):
    TEXT = "text"                # 纯文本
    IMAGE = "image"             # 单张图片
    RICH_TEXT = "rich_text"     # 富文本（含格式、链接、图片等混合内容）
    FILE = "file"               # 文件附件
    AUDIO = "audio"             # 语音消息
    VIDEO = "video"             # 视频消息
    STICKER = "sticker"         # 表情贴纸
    SHARE_LINK = "share_link"   # 分享链接
    SHARE_CHAT = "share_chat"   # 分享群聊
    SHARE_USER = "share_user"   # 分享用户名片
    CARD = "card"               # 平台富卡片/Embed
    UNKNOWN = "unknown"         # 未识别类型
```

### 3.4 Mention — @提及

```python
@dataclass
class Mention:
    user_id: str            # 被提及用户的 ID
    name: str               # 显示名
    is_bot_self: bool       # 是否提及的是本 bot
```

### 3.5 IncomingMessage — 收到的消息

内核唯一接触的消息结构。适配器负责将平台原始事件完整转换为此格式，**所有平台特有的解析、占位符替换、名字解析都在转换阶段完成**。

```python
@dataclass
class IncomingMessage:
    message_id: str                  # 平台消息唯一 ID
    chat_id: str                     # 会话 ID
    chat_type: ChatType              # 会话类型
    sender_id: str                   # 发送者 ID
    sender_type: SenderType          # 发送者类型（用户 / bot）
    sender_name: str                 # 发送者显示名（适配器必须尽力填充）
    message_type: MessageType        # 消息内容类型
    text: str                        # 已完成转换的纯文本（Markdown 格式）
    mentions: list[Mention]          # @提及列表（已解析）
    is_mention_bot: bool             # 是否 @了本 bot
    image_keys: list[str]            # 图片资源标识（需通过 MediaHandler 获取内容）
    timestamp: int                   # 消息时间戳（Unix 毫秒）
    raw: Any = None                  # 原始平台对象（内核不访问，仅供适配器内部传递）
```

**适配器转换时的硬性要求：**

1. `text` 必须是**已经完成所有占位符替换**的最终文本。飞书的 `@_user_1` 占位符、Discord 的 `<@123>` 标签，都必须在适配器内部替换为 `@真名`。本 bot 的 @ 应移除。
2. `sender_name` 必须尽力填充。不允许返回原始 ID（如 `cli_xxx`、`ou_xxx`）给内核。适配器内部无论用什么手段（缓存、API、推断）解决名字问题，内核不关心。
3. `sender_type` 必须正确区分人类用户和 bot。
4. 消息去重是**适配器的责任**。飞书 WS 偶尔重复推送同一条消息、Discord 的 message_update 等，适配器自行处理。

### 3.6 BotIdentity — 机器人身份

```python
@dataclass
class BotIdentity:
    bot_id: str              # 机器人在平台上的唯一 ID
    bot_name: str            # 机器人显示名
```

### 3.7 ChatMember — 群组成员

```python
@dataclass
class ChatMember:
    user_id: str
    name: str
    is_bot: bool
```

### 3.8 Reaction — 表情回应

```python
@dataclass
class Reaction:
    reaction_id: str         # 回应 ID（用于移除时引用）
    message_id: str          # 被回应的消息 ID
    emoji: str               # 表情标识（如 "thumbsup", "OnIt"）
    operator_id: str         # 操作者 ID
    operator_type: SenderType
```

### 3.9 CardAction — 卡片交互

```python
@dataclass
class CardAction:
    action_type: str         # 动作类型（如 "confirm", "cancel", "button_click"）
    value: dict              # 动作携带的数据
    operator_id: str         # 操作者 ID
    message_id: str = ""     # 来源卡片的消息 ID
```

### 3.10 CalendarEvent — 日历事件

```python
@dataclass
class CalendarEvent:
    event_id: str
    summary: str
    description: str = ""
    start_time: str = ""     # ISO 8601 或 "HH:MM" 显示格式
    end_time: str = ""
```

---

## 4. 接口总览

| 接口模块 | 内核的需求 | 必须实现 |
|---------|-----------|---------|
| `PlatformConnection` | 连接平台、获取自身身份、关闭连接 | **是** |
| `MessageSender` | 发文本、发卡片、引用回复 | **是** |
| `EventListener` | 收到所有消息和事件（适配器保证完整性） | **是** |
| `IdentityResolver` | 知道某个 ID 的名字、知道群里有谁 | **是** |
| `ReactionManager` | 给消息加/移除表情 | 否（能力声明） |
| `MediaHandler` | 获取消息中的图片/文件 | 否（能力声明） |
| `CalendarService` | 日历 CRUD | 否（能力声明） |
| `RichContentBuilder` | 发送结构化富内容 | 否（能力声明） |
| `PlatformCapabilities` | 声明平台支持的功能 | **是** |
| `PlatformConfig` | 平台凭证和配置 | **是** |

---

## 5. 连接与生命周期 (`PlatformConnection`)

```python
class PlatformConnection(ABC):

    @abstractmethod
    async def connect(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        """建立与平台的连接，开始接收事件。

        适配器负责：
        1. 使用平台凭证认证
        2. 建立事件通道（WebSocket / 长轮询 / Webhook）
        3. 将所有平台事件转换为标准 Event 格式后投入 queue

        关键契约：
        - 适配器必须保证 queue 中能收到会话中的**所有消息**，
          包括其他 bot 发的消息。
        - 如果平台的原生事件流不包含 bot 消息（如飞书），
          适配器需要内部补偿（如轮询 REST API），但这对内核透明。
        - 适配器内部处理事件去重、token 刷新等平台细节。

        Args:
            queue: 标准事件队列
            loop:  主 asyncio 事件循环（用于跨线程桥接，如飞书阻塞 WS）
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """优雅关闭连接，释放资源。"""
        ...

    @abstractmethod
    async def get_bot_identity(self) -> BotIdentity:
        """获取机器人自身身份信息。

        connect() 后调用。内核用 bot_id 识别"自己发的消息"并过滤。
        """
        ...
```

---

## 6. 消息发送 (`MessageSender`)

### 6.1 send_text — 发送文本消息

```python
@abstractmethod
async def send_text(self, chat_id: str, text: str) -> str | None:
    """向指定会话发送文本消息。

    Args:
        chat_id: 目标会话 ID
        text:    Markdown 格式文本

    Returns:
        发送成功返回 message_id，失败返回 None。

    适配器职责：
    - Markdown → 平台原生格式的转换（适配器全权负责）
    - 长文本分段（如 Discord 2000 字限制）
    - 飞书特殊处理（代码块自动切卡片、纯文本 strip Markdown）
      — 这些是适配器内部逻辑，内核只管传 Markdown 进来
    """
    ...
```

### 6.2 reply_text — 引用回复

```python
@abstractmethod
async def reply_text(self, message_id: str, text: str) -> str | None:
    """引用回复指定消息。

    如平台不支持引用回复，降级为 send_text。
    适配器内部决定降级策略，内核不感知。
    """
    ...
```

### 6.3 send_card — 发送富内容

```python
@abstractmethod
async def send_card(self, chat_id: str, card: dict) -> str | None:
    """发送结构化富内容（卡片/Embed）。

    Args:
        card: 标准卡片结构（见 §12）

    适配器职责：
    - 标准卡片 → 平台原生格式（飞书 Interactive Card / Discord Embed）
    - 如平台不支持卡片，降级为格式化文本
    """
    ...
```

### 6.4 reply_card — 引用回复卡片

```python
@abstractmethod
async def reply_card(self, message_id: str, card: dict) -> str | None:
    """引用回复富内容。不支持时降级为 send_card 或 reply_text。"""
    ...
```

### 6.5 format_mention — 生成 @提及标记

```python
@abstractmethod
def format_mention(self, user_id: str, name: str) -> str:
    """将 @名字 转换为平台原生的提及格式。

    内核在发送消息时，如果回复中包含 @某人，调用此方法获取
    平台原生格式的提及标记，然后嵌入文本。

    Returns:
        平台原生格式的 @ 标记
        - 飞书: '<at user_id="ou_xxx">名字</at>'
        - Discord: '<@123456>'
        - Telegram: '[名字](tg://user?id=123)'
        - 不支持: '@名字'（纯文本 fallback）
    """
    ...
```

---

## 7. 消息与事件接收 (`EventListener`)

适配器负责将平台原始事件转换为标准事件格式后投入队列。

### 7.1 标准事件格式

```python
# 消息事件 — 包括所有参与者（人类和 bot）的消息
{
    "event_type": "message.received",
    "message": IncomingMessage,
}

# Reaction 添加事件
{
    "event_type": "reaction.added",
    "reaction": Reaction,
}

# Bot 入群事件
{
    "event_type": "bot.added_to_group",
    "chat_id": str,
    "operator_id": str,        # 邀请者（可为空）
}

# Bot 被移出群聊
{
    "event_type": "bot.removed_from_group",
    "chat_id": str,
}

# 用户入群事件
{
    "event_type": "user.joined_group",
    "chat_id": str,
    "users": list[dict],       # [{"user_id": str, "name": str}]
}

# 卡片交互事件
{
    "event_type": "card.action",
    "action": CardAction,
}

# 内核内部事件（非平台产生）
{
    "event_type": "eval_timeout",
    "chat_id": str,
}
```

### 7.2 消息完整性契约

**这是抽象层最核心的契约：**

> 适配器必须保证事件队列中能收到会话中的**全部消息**，无论发送者是人类还是 bot。

内核不关心适配器怎么实现这一点：

| 平台 | 原生能力 | 适配器策略 |
|------|---------|-----------|
| 飞书 | WS 只推人类消息，不推 bot 消息 | 适配器内部开轮询线程，REST 拉取 bot 消息，合并后投入队列 |
| Discord | Gateway 推送所有消息（含 bot） | 直接转换投入队列，无需补偿 |
| Telegram | Bot API 推送所有消息 | 直接转换投入队列 |
| Slack | Events API 推送所有消息 | 直接转换投入队列 |

**飞书适配器的补偿逻辑（对内核完全透明）：**
- 启动一个后台任务，定期调用 `GET /im/v1/messages` 拉取活跃群聊的消息
- 过滤出 `sender_type=app` 的 bot 消息
- 去重（与 WS 已收到的消息对比）
- 解析发送者名字（含 cli_xxx → 真名的推断）
- 转换为 `IncomingMessage` 投入队列
- **内核完全不知道这些消息是 WS 推的还是 REST 拉的**

### 7.3 群组离开检测

> 适配器必须在检测到 bot 已不在某群聊时，投递 `bot.removed_from_group` 事件。

内核不关心检测手段：

| 平台 | 检测方式 |
|------|---------|
| 飞书 | 调群成员 API 返回 400 → 推断已退群 → 投递事件 |
| Discord | `on_guild_remove` 事件 → 直接投递 |
| Telegram | `my_chat_member` update 中 status=left → 投递 |

### 7.4 消息转换规范

适配器将原始消息转换为 `IncomingMessage` 时必须完成：

1. **文本提取与格式化**：
   - 纯文本 → 直接提取
   - 富文本（飞书 post / Discord Markdown / Telegram HTML）→ 统一转 Markdown
   - @提及占位符 → 替换为 `@真名`，本 bot 的 @ 移除
   - 图片标签 → `[图片]`，key 放入 `image_keys`

2. **发送者解析**（适配器内部完成，内核不参与）：
   - `sender_name` 必须是已解析的真名
   - `sender_type` 必须正确区分 USER / BOT
   - 如何解析是适配器的事（缓存、API、推断、任何手段）

3. **消息去重**（适配器内部完成）：
   - 飞书 WS 偶尔重复推送 → 适配器 dedup
   - REST 轮询与 WS 重叠 → 适配器 dedup

---

## 8. 身份与成员查询 (`IdentityResolver`)

### 8.1 get_user_name — 查询用户名

```python
@abstractmethod
async def get_user_name(self, user_id: str) -> str:
    """获取任意 ID（用户或 Bot）的显示名。

    Args:
        user_id: 用户或 bot 的 ID

    Returns:
        显示名。查不到时返回有意义的 fallback（如 ID 尾部截断）。

    适配器职责：
    - 内部实现缓存（必须），避免重复查询
    - 飞书：群成员批量缓存 + 联系人 API + bot 身份推断
    - Discord：Guild.get_member() 或 client.fetch_user()
    - 内核不关心实现细节，只要能拿到名字
    """
    ...
```

> **注意：v1.0 中的 `get_user_name(user_id, chat_id)` 和 `resolve_name(user_id)` 合并为一个方法。**
>
> 之前拆成两个方法是因为飞书有两种查找路径（群成员 API vs 联系人 API），
> 且 bot 的 app_id (cli_xxx) 需要特殊处理。
> 这些都是飞书内部的实现策略，不应该暴露给内核。
> 适配器内部自行决定用什么策略查找名字，内核只调一个方法。

### 8.2 get_group_members — 获取群组成员

```python
@abstractmethod
async def get_group_members(self, chat_id: str) -> list[ChatMember]:
    """获取群组成员列表，包含人类用户和 bot。

    Returns:
        ChatMember 列表，is_bot 字段正确标记。

    适配器职责：
    - 内部缓存结果
    - 飞书：GET /chats/{id}/members + 通过消息信号补充 bot 信息
    - Discord：Guild.members（直接包含完整信息）
    - 内核拿到的是完整列表，不需要自己"注册"bot
    """
    ...
```

> **注意：v1.0 中的 `get_bot_members`、`register_bot_member`、`is_chat_left` 被移除。**
>
> - `get_bot_members` → 内核从 `get_group_members` 结果中自行过滤 `is_bot=True`
> - `register_bot_member` → 这是飞书的补偿行为（通过消息信号发现 bot），
>   应封装在飞书适配器的 `get_group_members` 内部实现中
> - `is_chat_left` → 改为适配器投递 `bot.removed_from_group` 事件，
>   内核监听此事件更新自己的群聊状态

---

## 9. Reaction / 表情回应 (`ReactionManager`)

**能力依赖：`capabilities.has_reactions == True`**

### 9.1 add_reaction

```python
@abstractmethod
async def add_reaction(self, message_id: str, emoji: str) -> str | None:
    """给消息添加表情回应。

    Args:
        emoji: 平台无关的表情标识（如 "thinking", "thumbsup", "eyes"）

    Returns:
        reaction_id（用于后续移除），失败返回 None。

    适配器职责：
    - 将标准 emoji 标识映射到平台原生格式
    - 飞书: "thinking" → API emoji_type "OnIt"
    - Discord: "thinking" → Unicode 🤔 或自定义 emoji
    - 适配器维护标准名 → 平台名的映射表
    """
    ...
```

### 9.2 remove_reaction

```python
@abstractmethod
async def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
    """移除之前添加的表情回应。"""
    ...
```

---

## 10. 多媒体资源 (`MediaHandler`)

**能力依赖：`capabilities.has_media_download == True`**

### 10.1 download_media

```python
@abstractmethod
async def download_media(
    self, message_id: str, resource_key: str,
) -> tuple[str, str] | None:
    """下载消息中的媒体资源。

    Args:
        message_id:   消息 ID
        resource_key: 资源标识（来自 IncomingMessage.image_keys）

    Returns:
        (base64_data, mime_type) 或 None

    适配器职责：
    - 鉴权下载（飞书需 tenant_token，Discord 直接 HTTP GET）
    - 超大文件自动压缩（建议阈值 10MB）
    - 超时处理（建议 30 秒）
    - 格式归一化（统一返回 base64 + MIME type）
    """
    ...
```

---

## 11. 日历集成（可选）(`CalendarService`)

**能力依赖：`capabilities.has_calendar == True`**

日历功能独立于聊天平台。适配器可以对接平台内建日历（飞书）、外部日历（Google Calendar）、或不实现。

### 11.1 create_event

```python
@abstractmethod
async def create_event(
    self, summary: str, start_time: str, end_time: str, description: str = "",
) -> dict:
    """创建日历事件。时间为 ISO 8601 格式。
    Returns: {"success": True, "event_id": "..."} 或 {"success": False, "error": "..."}
    """
    ...
```

### 11.2 list_events

```python
@abstractmethod
async def list_events(self, start_time: str, end_time: str) -> list[CalendarEvent]:
    """查询时间范围内的日历事件。"""
    ...
```

---

## 12. 富内容卡片 / Embed (`RichContentBuilder`)

**能力依赖：`capabilities.has_rich_cards == True`**

### 标准卡片结构

内核使用以下平台无关的卡片描述，适配器负责转换：

```python
# 信息卡片
{
    "type": "info",
    "title": "卡片标题",
    "content": "Markdown 内容",
    "fields": [{"key": "字段名", "value": "字段值", "short": True}],  # 可选
    "color": "blue",    # blue/green/orange/red/purple
}

# 日程卡片
{
    "type": "schedule",
    "events": [{"start_time": "09:00", "end_time": "10:00", "summary": "会议"}],
}

# 任务卡片
{
    "type": "task_list",
    "tasks": [{"title": "任务名", "done": True}],
}

# 错误卡片
{
    "type": "error",
    "title": "错误标题",
    "message": "错误详情",
}

# 确认卡片（含交互按钮）
{
    "type": "confirm",
    "title": "操作审批",
    "content": "描述文本",
    "confirm_text": "确认",
    "cancel_text": "取消",
    "callback_data": {"type": "approval", "id": "xxx"},
}
```

### 降级策略

如 `has_rich_cards == False`，适配器的 `send_card` / `reply_card` 应：
1. 从卡片中提取 title + content
2. 拼为纯文本
3. 调用 `send_text` / `reply_text`

---

## 13. 卡片交互回调

**能力依赖：`capabilities.has_card_actions == True`**

用户点击卡片按钮 → 适配器转换为 `CardAction` → 投入事件队列：

```python
{
    "event_type": "card.action",
    "action": CardAction(
        action_type="confirm",
        value={"type": "approval", "id": "xxx"},
        operator_id="user_123",
    ),
}
```

如 `has_card_actions == False`，审批等功能降级为文字交互。

---

## 14. 平台能力声明 (`PlatformCapabilities`)

```python
@dataclass
class PlatformCapabilities:
    # ── 基础消息 ──
    has_reply: bool = True               # 支持引用回复
    has_markdown: bool = True            # 支持 Markdown 渲染
    max_message_length: int = 4000       # 单条消息最大字符数

    # ── 富内容 ──
    has_rich_cards: bool = False         # 支持富卡片/Embed
    has_card_actions: bool = False       # 卡片支持交互按钮

    # ── 多媒体 ──
    has_media_download: bool = False     # 支持下载消息中的图片/文件

    # ── 表情回应 ──
    has_reactions: bool = False          # 支持 Reaction

    # ── 群组 ──
    has_group_members: bool = False      # 支持查询群组成员列表

    # ── 日历 ──
    has_calendar: bool = False           # 支持日历集成

    # ── @提及 ──
    has_mentions: bool = True            # 支持 @提及
```

### 内核降级逻辑

| 能力缺失 | 内核行为 |
|---------|---------|
| `has_reply == False` | `reply_text` → `send_text` |
| `has_rich_cards == False` | `send_card` → 提取文本后 `send_text` |
| `has_reactions == False` | 跳过处理中指示器、bot 间意图信号 |
| `has_media_download == False` | 图片消息降级为 `[图片]` 文本描述 |
| `has_group_members == False` | 跳过群成员相关功能 |
| `has_calendar == False` | 日历工具返回 "日历功能未配置" |
| `has_card_actions == False` | 审批降级为文字确认 |

---

## 15. 平台配置 (`PlatformConfig`)

```python
@dataclass
class PlatformConfig(ABC):
    platform_type: str                   # "feishu", "discord", "telegram", etc.
    owner_chat_id: str = ""              # 主人的会话 ID（用于主动消息、晨报等）

    @abstractmethod
    def validate(self) -> list[str]:
        """校验配置完整性，返回错误列表。"""
        ...
```

---

## 16. 飞书适配指南

### 能力声明

```python
FEISHU_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=False,          # 文本消息不渲染 Markdown，卡片渲染
    max_message_length=10000,
    has_rich_cards=True,
    has_card_actions=True,
    has_media_download=True,
    has_reactions=True,
    has_group_members=True,
    has_calendar=True,
    has_mentions=True,
)
```

### 适配器内部补偿行为清单

以下行为全部封装在飞书适配器内部，对内核不可见：

#### (1) Bot 消息补漏轮询

**问题**：飞书 WebSocket 只推送人类消息，不推送其他 bot 的消息。

**补偿策略**：
- 适配器内部启动后台任务 `_poll_bot_messages()`
- 定期调用 `GET /im/v1/messages` 拉取活跃群聊的近期消息
- 过滤出 sender_type=app 且非本 bot 的消息
- 与 WS 已推送的消息去重（按 message_id）
- 转换为 `IncomingMessage` 后投入事件队列
- 内核看到的效果：事件队列中源源不断出现所有参与者的消息

**实现细节**：
- 轮询间隔：3-5 秒
- 轮询范围：活跃群聊（有近期消息的群 + 已知群）
- TTL 淘汰：600 秒无消息的非已知群停止轮询
- 轮询上限：每群最多连续 5 次无新消息后暂停
- HTTP 400/403：标记群聊为已退出，停止轮询

#### (2) Bot 身份推断

**问题**：飞书消息列表 API 对 bot 返回 app_id (cli_xxx) 而非 open_id (ou_xxx)，无法通过联系人 API 查到名字。

**补偿策略**（全部在适配器内部执行）：
- 策略 A — 排除法：消息中只有 1 个未知 bot + 1 个未匹配的 @提及名字 → 建立映射
- 策略 B — 时序法：@提及后紧跟 bot 回复 → 推断该 bot 就是被 @ 的那个
- 持久化到 `bot_identities.json`，重启后恢复

**效果**：`IncomingMessage.sender_name` 总是填充的真名，内核不感知推断过程。

#### (3) 群组退出检测

**问题**：飞书没有明确的 "bot 被移出群聊" 事件（虽然订阅了 `p2_im_chat_member_bot_deleted_v1`，但实际不可靠）。

**补偿策略**：
- 调群成员 API 返回 HTTP 400 → 推断已退群
- 轮询群消息 API 连续 3 次失败 → 推断已退群
- 检测到退群后投递 `bot.removed_from_group` 事件

#### (4) Token 管理

- `tenant_access_token` 有效期 2 小时，提前 5 分钟自动刷新
- 完全封装在适配器内部，内核不感知

#### (5) Markdown 发送策略

- 检测文本是否含代码块 → 含则自动切换为卡片消息
- 纯文本消息 strip Markdown 标记（飞书文本消息不渲染 Markdown）
- 这些都是 `send_text` 的内部实现

#### (6) receive_id_type 推断

- 根据 ID 前缀 (oc_/ou_/on_) 推断 API 参数中的 receive_id_type
- 完全封装在适配器内部

#### (7) @提及处理

- 入站：`@_user_N` 占位符 → 查 mentions 数组 → 替换为 `@真名`
- 出站：`@名字` → `<at user_id="ou_xxx">名字</at>` 标签
- 文本层兜底：当 SDK 未解析 @时，检查 `@bot名` 是否出现在文本中

#### (8) 消息去重

- WS 偶尔用不同 event_id 重复推送同一 message_id → 维护最近 200 条的滑动窗口去重
- REST 轮询与 WS 重叠 → 按 message_id 去重

### 飞书实现映射

| 抽象接口 | 飞书实现 | 源文件 |
|---------|---------|--------|
| `PlatformConnection.connect` | `FeishuListener.start_blocking` + `_poll_bot_messages` | `feishu/listener.py` |
| `PlatformConnection.get_bot_identity` | `GET /bot/v3/info` | `feishu/sender.py:191` |
| `MessageSender.send_text` | Markdown 检测 + strip/卡片切换 + `CreateMessageRequest` | `feishu/sender.py:95` |
| `MessageSender.reply_text` | `ReplyMessageRequest` | `feishu/sender.py:124` |
| `MessageSender.send_card` | `msg_type="interactive"` | `feishu/sender.py:151` |
| `MessageSender.reply_card` | `ReplyMessageRequest(interactive)` | `feishu/sender.py:172` |
| `MessageSender.format_mention` | `<at user_id="{id}">{name}</at>`（仅 ou_ 格式生效） | `router.py:2520` |
| `IdentityResolver.get_user_name` | 群成员批量缓存 + 联系人 API + bot 推断 | `feishu/sender.py:209,280,339` |
| `IdentityResolver.get_group_members` | `GET /chats/{id}/members` + bot 信号注册 | `feishu/sender.py:227` |
| `ReactionManager.add_reaction` | `POST /messages/{id}/reactions` | `feishu/sender.py:417` |
| `ReactionManager.remove_reaction` | `DELETE /messages/{id}/reactions/{rid}` | `feishu/sender.py:439` |
| `MediaHandler.download_media` | `GET /messages/{id}/resources/{key}` + 压缩 | `feishu/sender.py:533` |
| `CalendarService.*` | `FeishuCalendar` | `feishu/calendar.py` |
| `RichContentBuilder` | `feishu/cards.py` | `feishu/cards.py` |

---

## 17. Discord 适配指南

### 能力声明

```python
DISCORD_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=True,               # 原生 Markdown
    max_message_length=2000,
    has_rich_cards=True,             # Embed
    has_card_actions=True,           # Button components
    has_media_download=True,         # attachment.url
    has_reactions=True,              # Unicode / 自定义 emoji
    has_group_members=True,          # Guild.members
    has_calendar=False,              # 无内建日历
    has_mentions=True,
)
```

### 适配器实现要点

#### 不需要补偿的部分

以下飞书补偿行为，Discord **完全不需要**：

| 飞书补偿 | Discord 为什么不需要 |
|---------|-------------------|
| Bot 消息轮询 | Gateway 推送所有消息，含 bot |
| Bot 身份推断 | bot.user 直接有 id 和 name |
| register_bot_member | Guild.members 含完整 bot 列表 |
| is_chat_left 检测 | on_guild_remove 事件直接通知 |
| Markdown 降级 | 原生支持 |
| receive_id_type 推断 | channel_id 统一 |
| Token 刷新 | Bot Token 长期有效 |

#### 需要实现的映射

| 维度 | 飞书 | Discord |
|------|------|---------|
| 连接 | `lark_oapi.ws.Client` (阻塞, 需 daemon thread) | `discord.Client` (asyncio 原生) |
| 私聊判断 | `chat_type == "p2p"` | `isinstance(channel, DMChannel)` |
| 群聊判断 | `chat_type == "group"` | `isinstance(channel, TextChannel)` |
| @提及格式 | `<at user_id="ou_xxx">名字</at>` | `<@user_id>` |
| 卡片 | Interactive Card JSON | `discord.Embed` + `discord.ui.View` |
| Reaction emoji | emoji_type 字符串 (`"OnIt"`) | Unicode emoji (`"🤔"`) 或 `<:name:id>` |
| 消息 ID 前缀 | `om_xxx` | 纯数字 snowflake |
| 图片下载 | 需 tenant_token 鉴权 | `attachment.url` 直接 GET |
| bot 消息可见性 | WS 不推 → 需轮询 | Gateway 全推 → 无需额外操作 |

#### 实现清单

- [ ] `DiscordConnection` — `discord.Client` + `on_ready` → `get_bot_identity`
- [ ] `DiscordSender` — `channel.send()` / `message.reply()` / `Embed` / `format_mention`
- [ ] `DiscordEventAdapter` — `on_message` → `IncomingMessage`、`on_raw_reaction_add` → `Reaction`
- [ ] `DiscordIdentityResolver` — `guild.get_member()` / `client.fetch_user()`
- [ ] `DiscordReactionManager` — `message.add_reaction()` / `reaction.remove()`
- [ ] `DiscordMediaHandler` — `attachment.url` HTTP 下载
- [ ] `DiscordConfig` — `bot_token`, `guild_id`

---

## 附录 A：内核改造清单

### A.1 router.py — 最大改动模块

| 当前代码 | 改为 |
|---------|------|
| `sender: FeishuSender` 参数 | `sender: MessageSender` 抽象接口 |
| `_dispatch_message(event)` 访问 `event.message.chat_type` 等飞书 SDK 属性 | 接收 `IncomingMessage`（适配器已完成转换） |
| `_extract_text()` / `_extract_image_keys()` / `_resolve_at_mentions()` | 全部移入飞书适配器的消息转换逻辑中 |
| `_replace_at_mentions()` 生成飞书 `<at>` 标签 | 调用 `sender.format_mention(user_id, name)` |
| `_handle_card_action()` 访问飞书 SDK 对象属性 | 接收标准 `CardAction` |
| `from lq.feishu.cards import build_info_card` | 使用标准卡片结构 dict |
| `sender._user_name_cache` 直接访问 | 通过 `sender.get_user_name()` 接口查询 |
| `sender.is_chat_left()` | 监听 `bot.removed_from_group` 事件维护内部集合 |
| `sender.register_bot_member()` | 删除，由适配器内部处理 |
| `sender.fetch_chat_messages()` 在 router 中调用 | 删除，由适配器内部轮询后投入事件队列 |

### A.2 gateway.py

| 当前代码 | 改为 |
|---------|------|
| 硬编码创建 `FeishuSender` + `FeishuListener` | 通过工厂 / 配置创建平台适配器 |
| `_poll_active_groups()` | 删除 — 轮询职责移入飞书适配器 |
| `from lq.feishu.cards import build_schedule_card` | 使用标准卡片结构 |
| 构造飞书 SDK 兼容的 fake event | 构造标准 `IncomingMessage` |

### A.3 config.py

| 当前代码 | 改为 |
|---------|------|
| `LQConfig.feishu: FeishuConfig` | `LQConfig.platform: PlatformConfig` |

### A.4 conversation.py

`LocalSender` 已经是一个很好的适配器参考 — 它实现了消息发送接口的终端模拟版本。

---

## 附录 B：完整动作清单

以下是 LingQue 内核需要的**全部平台交互能力**。

### 出站动作（Bot → 平台）— 6 个

| # | 需求 | 方法 |
|---|------|------|
| 1 | 发送文本消息 | `send_text(chat_id, text)` |
| 2 | 引用回复文本 | `reply_text(message_id, text)` |
| 3 | 发送富内容 | `send_card(chat_id, card)` |
| 4 | 引用回复富内容 | `reply_card(message_id, card)` |
| 5 | 给消息添加表情 | `add_reaction(message_id, emoji)` |
| 6 | 移除消息表情 | `remove_reaction(message_id, reaction_id)` |

### 入站事件（平台 → Bot）— 6 个活跃 + 4 个忽略

| # | 需求 | 事件类型 |
|---|------|---------|
| 7 | 收到消息（含所有参与者的） | `message.received` |
| 8 | 有人给消息加了表情 | `reaction.added` |
| 9 | Bot 被加入群聊 | `bot.added_to_group` |
| 10 | Bot 被移出群聊 | `bot.removed_from_group` |
| 11 | 新用户加入群聊 | `user.joined_group` |
| 12 | 用户点击了卡片按钮 | `card.action` |
| — | 消息已读 / 撤回 / Reaction 移除 / 用户退群 | （忽略） |

### 查询能力（Bot ↔ 平台）— 5 个

| # | 需求 | 方法 |
|---|------|------|
| 13 | 获取自身身份 | `get_bot_identity()` |
| 14 | 查询某人的名字 | `get_user_name(user_id)` |
| 15 | 查询群组成员 | `get_group_members(chat_id)` |
| 16 | 下载消息中的图片/文件 | `download_media(message_id, resource_key)` |
| 17 | 生成平台原生 @标记 | `format_mention(user_id, name)` |

### 日历能力（可选）— 2 个

| # | 需求 | 方法 |
|---|------|------|
| 18 | 创建日历事件 | `create_event(...)` |
| 19 | 查询日历事件 | `list_events(start, end)` |

**合计 19 个抽象动作**（对比 v1.0 的 25 个，去掉了 6 个飞书补偿行为）。
