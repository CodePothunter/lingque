# 聊天平台抽象层接口规范

> LingQue 平台无关通信协议 v1.0
>
> 本文档定义了 LingQue 内核与外部聊天平台之间的全部交互动作。
> 任何新平台（Discord、Telegram、Slack、微信等）只需实现本文档定义的接口，即可接入 LingQue。

---

## 目录

1. [设计原则](#1-设计原则)
2. [标准化数据类型](#2-标准化数据类型)
3. [接口总览](#3-接口总览)
4. [连接与生命周期](#4-连接与生命周期-platformconnection)
5. [消息发送](#5-消息发送-messagesender)
6. [消息与事件接收](#6-消息与事件接收-eventlistener)
7. [用户与群组查询](#7-用户与群组查询-platformqueries)
8. [Reaction / 表情回应](#8-reaction--表情回应-reactionmanager)
9. [多媒体资源](#9-多媒体资源-mediahandler)
10. [日历集成（可选）](#10-日历集成可选-calendarservice)
11. [富内容卡片 / Embed](#11-富内容卡片--embed-richcontentbuilder)
12. [卡片交互回调](#12-卡片交互回调-interactivecallback)
13. [平台特有能力声明](#13-平台特有能力声明-platformcapabilities)
14. [平台配置](#14-平台配置-platformconfig)
15. [飞书实现参考](#15-飞书实现参考)
16. [Discord 实现要点（示例）](#16-discord-实现要点示例)

---

## 1. 设计原则

- **内核零依赖**：LingQue 核心（router、memory、session、executor）不引用任何平台 SDK。所有平台交互通过本文档定义的抽象接口完成。
- **数据归一化**：不同平台的消息、用户、群组等概念统一为标准数据类型，内核只处理标准类型。
- **能力声明制**：并非所有平台都支持所有功能（如日历、Reaction、富文本卡片）。适配器通过 `PlatformCapabilities` 声明自身能力，内核据此降级。
- **异步优先**：所有 I/O 接口均为 `async def`。
- **事件驱动**：平台适配器将平台原始事件转换为标准事件，投入统一的事件队列，由内核消费。

---

## 2. 标准化数据类型

### 2.1 ChatType — 会话类型

```python
class ChatType(str, Enum):
    PRIVATE = "private"   # 一对一私聊
    GROUP = "group"       # 多人群聊
```

### 2.2 SenderType — 发送者类型

```python
class SenderType(str, Enum):
    USER = "user"         # 人类用户
    BOT = "bot"           # 机器人/应用
```

### 2.3 MessageType — 消息内容类型

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

### 2.4 Mention — @提及

```python
@dataclass
class Mention:
    user_id: str            # 被提及用户的平台无关 ID
    name: str               # 显示名
    is_bot_self: bool       # 是否提及的是本 bot
    offset: int = 0         # 在原始文本中的偏移位置
    length: int = 0         # 在原始文本中的长度
```

### 2.5 IncomingMessage — 收到的消息

```python
@dataclass
class IncomingMessage:
    message_id: str                  # 平台消息唯一 ID
    chat_id: str                     # 会话 ID（私聊或群聊的标识）
    chat_type: ChatType              # 会话类型
    sender_id: str                   # 发送者 ID
    sender_type: SenderType          # 发送者类型
    sender_name: str                 # 发送者显示名（尽可能填充）
    message_type: MessageType        # 消息内容类型
    text: str                        # 提取后的纯文本内容（富文本已转 Markdown）
    mentions: list[Mention]          # @提及列表
    is_mention_bot: bool             # 是否 @了本 bot
    image_keys: list[str]            # 图片资源标识列表（需通过 download_media 获取）
    timestamp: int                   # 消息时间戳（Unix 毫秒）
    raw: Any = None                  # 原始平台事件对象（供适配器内部使用）
```

### 2.6 OutgoingMessage — 发出的消息

```python
@dataclass
class OutgoingMessage:
    text: str                            # 消息文本（Markdown 格式）
    chat_id: str = ""                    # 目标会话 ID
    reply_to_message_id: str = ""        # 引用回复的消息 ID（空则不引用）
    mentions: list[Mention] = field(default_factory=list)  # 需要 @提及的用户
    card: dict | None = None             # 富内容卡片结构（平台适配器负责转换为平台格式）
```

### 2.7 User — 用户信息

```python
@dataclass
class User:
    user_id: str              # 平台用户 ID
    name: str                 # 显示名
    sender_type: SenderType   # 用户/机器人
```

### 2.8 ChatMember — 群组成员

```python
@dataclass
class ChatMember:
    user_id: str
    name: str
    is_bot: bool
```

### 2.9 FetchedMessage — 历史消息查询结果

```python
@dataclass
class FetchedMessage:
    message_id: str
    sender_id: str
    sender_type: SenderType
    sender_name: str         # 适配器应尽可能解析填充
    text: str                # 已解析的纯文本
    timestamp: int           # Unix 毫秒
    mentioned_names: list[str]  # @提及的名字列表
```

### 2.10 BotIdentity — 机器人身份

```python
@dataclass
class BotIdentity:
    bot_id: str              # 机器人在平台上的唯一 ID
    bot_name: str            # 机器人显示名
    app_id: str = ""         # 应用 ID（部分平台有，如飞书 cli_xxx）
```

### 2.11 Reaction — 表情回应

```python
@dataclass
class Reaction:
    reaction_id: str         # 回应 ID（用于移除时引用）
    message_id: str          # 被回应的消息 ID
    emoji: str               # 表情标识（如 "thumbsup", "OnIt"）
    operator_id: str         # 操作者 ID
    operator_type: SenderType
```

### 2.12 CardAction — 卡片交互

```python
@dataclass
class CardAction:
    action_type: str         # 动作类型（如 "confirm", "cancel", "button_click"）
    value: dict              # 动作携带的数据
    operator_id: str         # 操作者 ID
    message_id: str = ""     # 来源卡片的消息 ID
```

### 2.13 CalendarEvent — 日历事件

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

## 3. 接口总览

| 接口模块 | 职责 | 必须实现 |
|---------|------|---------|
| `PlatformConnection` | 连接、认证、生命周期 | **是** |
| `MessageSender` | 发送文本/卡片、引用回复 | **是** |
| `EventListener` | 接收消息和事件，转换为标准类型 | **是** |
| `PlatformQueries` | 查询用户名、群成员、历史消息 | **是** |
| `ReactionManager` | 添加/移除表情回应 | 否（能力声明） |
| `MediaHandler` | 下载图片/文件/媒体 | 否（能力声明） |
| `CalendarService` | 日历 CRUD | 否（能力声明） |
| `RichContentBuilder` | 构建富卡片/Embed | 否（能力声明） |
| `InteractiveCallback` | 卡片按钮交互回调 | 否（能力声明） |
| `PlatformCapabilities` | 声明平台支持的功能集 | **是** |
| `PlatformConfig` | 平台特定配置 | **是** |

---

## 4. 连接与生命周期 (`PlatformConnection`)

管理与平台的连接建立、认证、事件循环和优雅关闭。

```python
class PlatformConnection(ABC):

    @abstractmethod
    async def connect(self, queue: asyncio.Queue, loop: asyncio.AbstractEventLoop) -> None:
        """建立与平台的连接。

        适配器负责：
        1. 使用平台凭证认证
        2. 建立长连接（WebSocket / 长轮询 / HTTP webhook）
        3. 将收到的平台原始事件转换为标准 Event 并投入 queue

        Args:
            queue: 标准事件队列，适配器需将转换后的事件放入此队列
            loop:  主 asyncio 事件循环引用，用于跨线程桥接
        """
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """优雅关闭连接，释放资源。"""
        ...

    @abstractmethod
    async def get_bot_identity(self) -> BotIdentity:
        """获取机器人自身身份信息。

        在 connect() 之后调用，返回 bot 在平台上的 ID 和名字。
        内核用此识别"自己发的消息"并过滤。
        """
        ...
```

### 标准事件格式

适配器将平台原始事件转换为以下标准格式后投入队列：

```python
# 消息事件
{
    "event_type": "message.received",      # 固定值
    "message": IncomingMessage,            # 标准消息对象
}

# Reaction 事件
{
    "event_type": "reaction.added",        # 或 "reaction.removed"
    "reaction": Reaction,
}

# Bot 入群事件
{
    "event_type": "bot.added_to_group",
    "chat_id": str,
    "operator_id": str,                    # 邀请者（可为空）
}

# 用户入群事件
{
    "event_type": "user.joined_group",
    "chat_id": str,
    "users": list[User],
}

# 卡片交互事件
{
    "event_type": "card.action",
    "action": CardAction,
}

# 群聊缓冲区评估超时（内核内部事件，非平台产生）
{
    "event_type": "eval_timeout",
    "chat_id": str,
}
```

---

## 5. 消息发送 (`MessageSender`)

### 5.1 send_text — 发送文本消息

```python
@abstractmethod
async def send_text(self, chat_id: str, text: str) -> str | None:
    """向指定会话发送文本消息。

    Args:
        chat_id: 目标会话 ID
        text:    Markdown 格式文本

    Returns:
        发送成功返回 message_id，失败返回 None。

    实现注意：
    - 适配器负责处理 Markdown 到平台原生格式的转换
    - 如平台不支持 Markdown，应做降级处理（strip 标记或转纯文本）
    - 如平台对长文本有限制，适配器自行分段发送
    - 飞书：代码块等复杂 Markdown 自动切换为卡片消息
    - Discord：原生支持 Markdown
    - Telegram：使用 ParseMode.MARKDOWN_V2
    """
    ...
```

### 5.2 reply_text — 引用回复文本消息

```python
@abstractmethod
async def reply_text(self, message_id: str, text: str) -> str | None:
    """引用回复指定消息。

    Args:
        message_id: 被回复消息的 ID
        text:       Markdown 格式文本

    Returns:
        发送成功返回新消息的 message_id，失败返回 None。

    实现注意：
    - 部分平台的引用回复有展示差异（飞书引用线、Discord reply 标签）
    - 如平台不支持引用回复，降级为普通 send_text
    """
    ...
```

### 5.3 send_card — 发送富内容卡片

```python
@abstractmethod
async def send_card(self, chat_id: str, card: dict) -> str | None:
    """发送平台富卡片消息。

    Args:
        chat_id: 目标会话 ID
        card:    标准卡片结构（见 RichContentBuilder）

    Returns:
        message_id 或 None

    实现注意：
    - card 是平台无关的标准结构，适配器负责转换为平台原生格式
    - 标准卡片结构见 §11 RichContentBuilder
    - 飞书 → interactive 卡片 JSON
    - Discord → Embed 对象
    - Telegram → InlineKeyboard + 格式化文本
    - 如平台不支持卡片，降级为格式化文本
    """
    ...
```

### 5.4 reply_card — 引用回复卡片

```python
@abstractmethod
async def reply_card(self, message_id: str, card: dict) -> str | None:
    """引用回复卡片消息。

    Args:
        message_id: 被回复消息的 ID
        card:       标准卡片结构

    Returns:
        message_id 或 None

    实现注意：
    - 如平台不支持卡片引用回复，降级为 reply_text（从卡片提取文本）
    """
    ...
```

---

## 6. 消息与事件接收 (`EventListener`)

适配器负责将平台原始事件转换为 §4 中定义的标准事件格式。

### 6.1 需要转换的入站事件

| 平台原始事件 | 标准事件类型 | 说明 |
|-------------|-------------|------|
| 收到消息 | `message.received` | 所有消息类型（文本/图片/富文本/文件等） |
| Reaction 添加 | `reaction.added` | 用户或 bot 给消息添加表情 |
| Reaction 移除 | `reaction.removed` | 用户或 bot 移除消息表情 |
| Bot 被加入群聊 | `bot.added_to_group` | 适配器主动获取群成员信息 |
| 用户加入群聊 | `user.joined_group` | 新用户入群 |
| 卡片/按钮交互 | `card.action` | 用户点击卡片按钮 |
| 消息已读 | （忽略） | 内核当前不处理 |
| 消息撤回 | （忽略） | 内核当前不处理 |
| Bot 被移出群聊 | （忽略） | 内核当前不处理 |
| 用户退出群聊 | （忽略） | 内核当前不处理 |

### 6.2 消息转换规范

适配器在将原始消息转换为 `IncomingMessage` 时必须完成以下工作：

1. **文本提取**：
   - 纯文本消息：直接提取
   - 富文本/post 消息：转换为 Markdown 格式
     - 链接 → `[text](url)`
     - @提及 → `@name`（并填充 mentions 列表）
     - 图片 → `[图片]`（图片 key 放入 image_keys）
     - 媒体 → `[媒体]`
   - 图片消息：text 为空或用户附带的说明文字，image_keys 填入图片标识

2. **Mention 解析**：
   - 解析平台特定的 @提及格式
   - 判断是否 @了本 bot（设置 `is_mention_bot`）
   - 将 @占位符替换为 `@真名`，自己的 @移除
   - 文本层兜底：检查 `@bot名` 是否出现在文本中

3. **发送者信息**：
   - 尽可能填充 sender_name（适配器内部缓存或查询）
   - 正确设置 sender_type（用户 vs 机器人）

4. **消息去重**：
   - 适配器应处理平台级别的消息重复推送（如飞书 WS 偶尔重复推送同一消息）

---

## 7. 用户与群组查询 (`PlatformQueries`)

### 7.1 get_user_name — 获取用户名

```python
@abstractmethod
async def get_user_name(self, user_id: str, chat_id: str = "") -> str:
    """获取用户显示名，带内存缓存。

    Args:
        user_id: 用户 ID
        chat_id: 所在会话 ID（部分平台通过群成员 API 批量缓存）

    Returns:
        用户名。查不到时返回 ID 尾部截断作为 fallback。

    实现注意：
    - 必须实现内存缓存，避免高频查询
    - 飞书：通过群成员 API 批量缓存，再 fallback 到联系人 API
    - Discord：从 Guild.members 或 User 对象获取
    """
    ...
```

### 7.2 resolve_name — 解析用户/Bot 名字

```python
@abstractmethod
async def resolve_name(self, user_id: str) -> str:
    """解析任意 ID（用户或 Bot）的名字。

    与 get_user_name 不同之处在于此方法也处理 Bot 的 app_id 格式。

    Args:
        user_id: 用户或 Bot 的 ID

    Returns:
        名字，查不到返回 ID 尾部截断。
    """
    ...
```

### 7.3 get_chat_members — 获取群组成员

```python
@abstractmethod
async def get_chat_members(self, chat_id: str) -> list[ChatMember]:
    """获取群组成员列表（含机器人），带缓存。

    Args:
        chat_id: 群组 ID

    Returns:
        成员列表

    实现注意：
    - 结果应缓存（按 chat_id），避免频繁调用
    - 需要区分人类成员和机器人成员
    - 飞书：GET /im/v1/chats/{chat_id}/members
    - Discord：Guild.members
    """
    ...
```

### 7.4 get_bot_members — 获取群内其他 Bot

```python
@abstractmethod
def get_bot_members(self, chat_id: str) -> set[str]:
    """返回群聊中其他 bot 的 ID 集合（不含自己）。

    用于：
    - bot 间协作感知（谁在群里）
    - 防止 bot 间无限对话循环
    - 构建自我认知上下文（"我的姐妹实例"）
    """
    ...
```

### 7.5 fetch_chat_messages — 拉取历史消息

```python
@abstractmethod
async def fetch_chat_messages(self, chat_id: str, count: int = 10) -> list[FetchedMessage]:
    """拉取群聊/私聊的最近消息（含 Bot 消息），按时间正序。

    Args:
        chat_id: 会话 ID
        count:   最多返回消息数

    Returns:
        消息列表（时间正序）

    核心用途：
    1. 群聊主动轮询 — 补充 WebSocket 收不到的 bot 消息
    2. 群聊上下文补充 — @回复时获取完整对话上下文
    3. 早安问候检测 — 判断今天群里是否已有消息
    4. Bot 身份推断 — 通过消息上下文推断未知 bot 的名字

    实现注意：
    - 飞书 WS 收不到其他 bot 的消息，必须通过 REST API 轮询
    - Discord bot 可以通过 Gateway 收到所有消息（包括其他 bot），
      此方法可降级为返回空列表或从本地缓存读取
    - 适配器应处理 @占位符到真名的转换
    """
    ...
```

### 7.6 is_chat_left — 检查是否已离开群聊

```python
@abstractmethod
def is_chat_left(self, chat_id: str) -> bool:
    """检查 bot 是否已退出/被移出某群聊。

    用于：
    - 跳过已退出群聊的消息处理
    - 跳过已退出群聊的早安问候
    - 停止对已退出群聊的轮询
    """
    ...
```

### 7.7 register_bot_member — 注册发现的 Bot

```python
@abstractmethod
def register_bot_member(self, chat_id: str, bot_id: str) -> None:
    """通过消息信号注册群聊中发现的 bot（补充成员 API 的不足）。

    飞书的消息列表 API 对 bot 返回 app_id 而非 open_id，
    需要通过消息上下文逐步发现群内的 bot。

    对于原生支持 bot 成员检测的平台（Discord），可空实现。
    """
    ...
```

---

## 8. Reaction / 表情回应 (`ReactionManager`)

**能力依赖：`capabilities.has_reactions == True`**

### 8.1 add_reaction — 添加表情回应

```python
@abstractmethod
async def add_reaction(self, message_id: str, emoji: str) -> str | None:
    """给消息添加表情回应。

    Args:
        message_id: 目标消息 ID
        emoji:      表情标识（如 "OnIt", "thumbsup", "eyes"）

    Returns:
        reaction_id（用于后续移除），失败返回 None。

    核心用途：
    1. 处理中指示器 — 收到消息后添加 "OnIt" 表情，处理完毕后移除
    2. Bot 间意图信号 — 表示"我正在思考这个问题"，避免重复回答

    实现注意：
    - emoji 标识需要适配器做平台特定的映射
    - 飞书："OnIt" → reaction API emoji_type
    - Discord：可用 Unicode emoji 或自定义 emoji
    """
    ...
```

### 8.2 remove_reaction — 移除表情回应

```python
@abstractmethod
async def remove_reaction(self, message_id: str, reaction_id: str) -> bool:
    """移除之前添加的表情回应。

    Args:
        message_id:  消息 ID
        reaction_id: add_reaction 返回的 ID

    Returns:
        是否成功
    """
    ...
```

---

## 9. 多媒体资源 (`MediaHandler`)

**能力依赖：`capabilities.has_media_download == True`**

### 9.1 download_media — 下载媒体资源

```python
@abstractmethod
async def download_media(
    self, message_id: str, resource_key: str,
) -> tuple[str, str] | None:
    """下载消息中的图片/文件资源。

    Args:
        message_id:   消息 ID
        resource_key:  资源标识（图片 key、文件 key 等）

    Returns:
        (base64_encoded_data, media_type) 或 None
        - base64_encoded_data: base64 编码的二进制数据
        - media_type: MIME 类型（如 "image/jpeg", "image/png"）

    实现注意：
    - 超大文件应自动压缩（当前阈值 10MB）
    - 图片压缩策略：缩小尺寸（最大 4096x4096）→ 降低 JPEG 质量
    - GIF 不压缩（避免丢失动画帧）
    - 下载超时建议 30 秒
    - 飞书：GET /im/v1/messages/{msg_id}/resources/{file_key}?type=image
    - Discord：attachment.url 直接下载
    """
    ...
```

---

## 10. 日历集成（可选）(`CalendarService`)

**能力依赖：`capabilities.has_calendar == True`**

并非所有平台都内建日历功能。适配器可以：
- 直接对接平台日历（飞书日历）
- 对接外部日历（Google Calendar、Outlook）
- 不实现（返回 `has_calendar = False`）

### 10.1 create_event — 创建日历事件

```python
@abstractmethod
async def create_event(
    self, summary: str, start_time: str, end_time: str, description: str = "",
) -> dict:
    """创建日历事件。

    Args:
        summary:     事件标题
        start_time:  ISO 8601 格式（如 "2024-01-01T15:00:00+08:00"）
        end_time:    ISO 8601 格式
        description: 事件描述

    Returns:
        {"success": True, "event_id": "..."} 或
        {"success": False, "error": "..."}
    """
    ...
```

### 10.2 list_events — 查询日历事件

```python
@abstractmethod
async def list_events(self, start_time: str, end_time: str) -> list[CalendarEvent]:
    """查询时间范围内的日历事件。

    Args:
        start_time: ISO 8601 格式
        end_time:   ISO 8601 格式

    Returns:
        CalendarEvent 列表
    """
    ...
```

---

## 11. 富内容卡片 / Embed (`RichContentBuilder`)

**能力依赖：`capabilities.has_rich_cards == True`**

### 标准卡片结构

适配器接收以下平台无关的标准卡片结构，转换为平台原生格式：

```python
# 信息卡片
{
    "type": "info",
    "title": "卡片标题",
    "content": "Markdown 内容",
    "fields": [                          # 可选：字段列表
        {"key": "字段名", "value": "字段值", "short": True},
    ],
    "color": "blue",                     # blue/green/orange/red/purple
}

# 日程卡片
{
    "type": "schedule",
    "events": [
        {"start_time": "09:00", "end_time": "10:00", "summary": "会议"},
    ],
}

# 任务卡片
{
    "type": "task_list",
    "tasks": [
        {"title": "任务名", "done": True},
    ],
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
    "confirm_text": "确认",              # 确认按钮文字
    "cancel_text": "取消",               # 取消按钮文字
    "callback_data": {"type": "approval", "id": "xxx"},  # 回调数据
}
```

### 平台映射

| 标准卡片 | 飞书 | Discord | Telegram |
|---------|------|---------|----------|
| info | Interactive Card (markdown) | Embed | 格式化文本 |
| schedule | Interactive Card | Embed with fields | 格式化文本 |
| task_list | Interactive Card | Embed with ✅⬜ | 格式化文本 |
| error | Interactive Card (red) | Embed (red) | 格式化文本 + ⚠️ |
| confirm | Interactive Card + buttons | Message + Button components | InlineKeyboard |

### 降级策略

如平台不支持卡片（`has_rich_cards == False`），`send_card` 和 `reply_card` 应：
1. 从标准卡片结构中提取 title + content 拼接为纯文本
2. 调用 `send_text` / `reply_text` 发送

---

## 12. 卡片交互回调 (`InteractiveCallback`)

**能力依赖：`capabilities.has_card_actions == True`**

当用户点击卡片中的按钮时，适配器应将事件转换为标准 `CardAction` 并投入事件队列：

```python
{
    "event_type": "card.action",
    "action": CardAction(
        action_type="confirm",           # 或 "cancel", "button_click"
        value={"type": "approval", "id": "xxx"},
        operator_id="user_123",
        message_id="msg_456",
    ),
}
```

内核当前使用此功能实现：
- **操作审批**：向主人发送确认/取消卡片，等待审批结果

---

## 13. 平台特有能力声明 (`PlatformCapabilities`)

```python
@dataclass
class PlatformCapabilities:
    # 基础消息
    has_reply: bool = True               # 是否支持引用回复
    has_markdown: bool = True            # 是否支持 Markdown 渲染
    max_message_length: int = 4000       # 单条消息最大字符数

    # 富内容
    has_rich_cards: bool = False         # 是否支持富卡片/Embed
    has_card_actions: bool = False       # 卡片是否支持交互按钮

    # 多媒体
    has_media_download: bool = False     # 是否支持下载消息中的图片/文件
    has_media_upload: bool = False       # 是否支持主动上传图片/文件

    # 表情回应
    has_reactions: bool = False          # 是否支持 Reaction

    # 群组
    has_group_members: bool = False      # 是否支持查询群组成员
    has_message_history: bool = False    # 是否支持查询历史消息
    has_bot_messages_via_ws: bool = True # WebSocket/Gateway 能否收到其他 bot 消息

    # 日历
    has_calendar: bool = False           # 是否支持日历集成

    # @提及
    has_mentions: bool = True            # 是否支持 @提及
    mention_format: str = "@{name}"      # @提及的发送格式模板
    # 飞书: '<at user_id="{id}">{name}</at>'
    # Discord: '<@{id}>'
    # Telegram: '@{username}' 或 tg://user?id={id}

    # 连接模式
    connection_type: str = "websocket"   # websocket / webhook / long_polling
```

### 内核降级逻辑

| 能力缺失 | 内核行为 |
|---------|---------|
| `has_reply == False` | `reply_text` → `send_text`，`reply_card` → `send_card` |
| `has_rich_cards == False` | `send_card` → 提取文本后 `send_text` |
| `has_reactions == False` | 跳过处理中指示器和 bot 间意图信号 |
| `has_media_download == False` | 图片消息降级为 `[图片]` 文本描述 |
| `has_message_history == False` | 跳过群聊主动轮询 |
| `has_bot_messages_via_ws == True` | 跳过群聊主动轮询（无需轮询即可收到 bot 消息） |
| `has_calendar == False` | 日历工具返回 "日历功能未配置" |
| `has_card_actions == False` | 审批功能降级为文字确认 |

---

## 14. 平台配置 (`PlatformConfig`)

每个平台适配器定义自己的配置 dataclass，但必须包含以下公共字段：

```python
@dataclass
class PlatformConfig(ABC):
    platform_type: str                   # "feishu", "discord", "telegram", etc.
    owner_chat_id: str = ""              # 主人的会话 ID（用于主动消息、晨报等）
    bot_open_id: str = ""                # 运行时填充

    @abstractmethod
    def validate(self) -> list[str]:
        """校验配置完整性，返回错误列表（空列表表示通过）。"""
        ...
```

### 飞书配置

```python
@dataclass
class FeishuPlatformConfig(PlatformConfig):
    platform_type: str = "feishu"
    app_id: str = ""
    app_secret: str = ""
```

### Discord 配置

```python
@dataclass
class DiscordPlatformConfig(PlatformConfig):
    platform_type: str = "discord"
    bot_token: str = ""
    guild_id: str = ""                   # 可选，限制特定服务器
```

---

## 15. 飞书实现参考

当前代码库中飞书相关模块与抽象接口的映射关系：

| 抽象接口 | 飞书实现 | 源文件 |
|---------|---------|--------|
| `PlatformConnection.connect` | `FeishuListener.start_blocking` (daemon thread) + `call_soon_threadsafe` | `feishu/listener.py` |
| `PlatformConnection.get_bot_identity` | `FeishuSender.fetch_bot_info` (GET /bot/v3/info) | `feishu/sender.py:191` |
| `MessageSender.send_text` | `FeishuSender.send_text` (Markdown→strip/card 自动切换) | `feishu/sender.py:95` |
| `MessageSender.reply_text` | `FeishuSender.reply_text` | `feishu/sender.py:124` |
| `MessageSender.send_card` | `FeishuSender.send_card` (msg_type="interactive") | `feishu/sender.py:151` |
| `MessageSender.reply_card` | `FeishuSender.reply_card` | `feishu/sender.py:172` |
| `PlatformQueries.get_user_name` | `FeishuSender.get_user_name` (群成员 API 批量缓存) | `feishu/sender.py:209` |
| `PlatformQueries.resolve_name` | `FeishuSender.resolve_name` (cli_→推断, ou_→联系人 API) | `feishu/sender.py:280` |
| `PlatformQueries.get_chat_members` | `FeishuSender._cache_chat_members` (GET /chats/{id}/members) | `feishu/sender.py:227` |
| `PlatformQueries.get_bot_members` | `FeishuSender.get_bot_members` | `feishu/sender.py:406` |
| `PlatformQueries.fetch_chat_messages` | `FeishuSender.fetch_chat_messages` (GET /im/v1/messages) | `feishu/sender.py:461` |
| `PlatformQueries.is_chat_left` | `FeishuSender.is_chat_left` | `feishu/sender.py:267` |
| `PlatformQueries.register_bot_member` | `FeishuSender.register_bot_member` | `feishu/sender.py:271` |
| `ReactionManager.add_reaction` | `FeishuSender.add_reaction` (POST /messages/{id}/reactions) | `feishu/sender.py:417` |
| `ReactionManager.remove_reaction` | `FeishuSender.remove_reaction` (DELETE) | `feishu/sender.py:439` |
| `MediaHandler.download_media` | `FeishuSender.download_image` (GET /messages/{id}/resources/{key}) | `feishu/sender.py:533` |
| `CalendarService.create_event` | `FeishuCalendar.create_event` | `feishu/calendar.py:42` |
| `CalendarService.list_events` | `FeishuCalendar.list_events` | `feishu/calendar.py:94` |
| `RichContentBuilder` | `feishu/cards.py` (build_info_card, build_schedule_card, etc.) | `feishu/cards.py` |
| `InteractiveCallback` | `FeishuListener._on_card_action` → `router._handle_card_action` | `feishu/listener.py:123`, `router.py:2756` |
| Token 管理 | `FeishuSender._get_tenant_token` (飞书特有) | `feishu/sender.py:660` |
| Bot 身份推断 | `FeishuSender.infer_bot_identities` (飞书特有) | `feishu/sender.py:339` |

### 飞书特有行为（需封装在适配器内部）

以下行为是飞书平台特有的，不应暴露给内核：

1. **Token 管理**：tenant_access_token 获取与刷新（2 小时有效期，提前 5 分钟刷新）
2. **receive_id_type 推断**：根据 ID 前缀（oc_/ou_/on_）推断 chat_id/open_id/union_id
3. **Markdown 降级**：飞书文本消息不支持 Markdown，需 strip 或自动切换卡片
4. **Bot 身份推断**：飞书消息列表 API 对 bot 返回 app_id (cli_xxx)，需要通过时序/排除法推断真名
5. **@标签格式**：飞书使用 `<at user_id="ou_xxx">名字</at>` 格式
6. **WS 不推送 bot 消息**：飞书 WebSocket 收不到其他 bot 的消息，需 REST 轮询补充

### 飞书能力声明

```python
FEISHU_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=False,              # 文本消息不支持 Markdown，但卡片支持
    max_message_length=10000,
    has_rich_cards=True,
    has_card_actions=True,
    has_media_download=True,
    has_media_upload=False,          # 当前未实现上传
    has_reactions=True,
    has_group_members=True,
    has_message_history=True,
    has_bot_messages_via_ws=False,   # WS 收不到 bot 消息
    has_calendar=True,
    has_mentions=True,
    mention_format='<at user_id="{id}">{name}</at>',
    connection_type="websocket",
)
```

---

## 16. Discord 实现要点（示例）

以 Discord 为例，说明如何基于本文档实现适配器。

### 能力声明

```python
DISCORD_CAPABILITIES = PlatformCapabilities(
    has_reply=True,
    has_markdown=True,               # Discord 原生支持 Markdown
    max_message_length=2000,
    has_rich_cards=True,             # Discord Embed
    has_card_actions=True,           # Discord Button components
    has_media_download=True,         # attachment.url 直接下载
    has_media_upload=True,           # 可发送文件附件
    has_reactions=True,              # Unicode + 自定义 emoji
    has_group_members=True,          # Guild.members
    has_message_history=True,        # channel.history()
    has_bot_messages_via_ws=True,    # Gateway 能收到所有消息
    has_calendar=False,              # Discord 无内建日历
    has_mentions=True,
    mention_format="<@{id}>",
    connection_type="websocket",
)
```

### 关键差异

| 维度 | 飞书 | Discord |
|------|------|---------|
| 连接方式 | lark_oapi WS Client (阻塞) | discord.py Gateway (asyncio 原生) |
| 认证 | app_id + app_secret → tenant_token | Bot Token |
| Bot 消息可见性 | WS 收不到 → 需 REST 轮询 | Gateway 全部可见 → 无需轮询 |
| @提及格式 | `<at user_id="ou_xxx">名字</at>` | `<@user_id>` |
| 富卡片 | Interactive Card JSON | Embed + View (buttons) |
| Markdown | 文本消息不支持，卡片支持 | 原生支持 |
| 日历 | 内建日历 API | 无（需对接 Google Calendar 等） |
| 消息长度限制 | 10000+ | 2000 |
| 文件下载 | 需 tenant_token 鉴权 | attachment.url 直接 HTTP GET |
| Reaction | emoji_type 字符串 | Unicode emoji 或 `<:name:id>` |
| 私聊标识 | chat_type="p2p" | isinstance(channel, DMChannel) |
| 群聊标识 | chat_type="group" | isinstance(channel, TextChannel) |

### 实现清单

实现 Discord 适配器时需要完成的接口：

- [ ] `DiscordConnection` — 基于 discord.py `Client.run()` 或 `Bot.start()`
- [ ] `DiscordSender` — `send_text`（直接发送 Markdown）、`reply_text`（message.reply）、`send_card`（Embed）、`reply_card`
- [ ] `DiscordEventAdapter` — on_message → IncomingMessage、on_raw_reaction_add → Reaction、on_member_join → user.joined_group
- [ ] `DiscordQueries` — Guild.get_member()、channel.history()
- [ ] `DiscordReactionManager` — message.add_reaction()、reaction.remove()
- [ ] `DiscordMediaHandler` — attachment.url HTTP 下载
- [ ] `DiscordConfig` — bot_token、guild_id

---

## 附录 A：内核需要修改的部分

将当前代码库改造为平台无关架构时，以下模块需要修改：

### A.1 router.py

- `MessageRouter.__init__` 的 `sender` 参数类型：`FeishuSender` → 抽象接口
- `_dispatch_message`：直接访问 `event.message.chat_type` 等飞书 SDK 属性 → 接收 `IncomingMessage`
- `_extract_text` / `_extract_image_keys` / `_resolve_at_mentions`：飞书消息解析 → 移入飞书适配器
- `_replace_at_mentions`：飞书 `<at>` 标签格式 → 通过 `capabilities.mention_format` 动态生成
- `_handle_card_action`：飞书 SDK 对象属性访问 → 接收标准 `CardAction`
- `_execute_tool` 中的 `send_card`：直接 `from lq.feishu.cards import` → 使用标准卡片结构
- `_request_owner_approval`：直接 `from lq.feishu.cards import` → 使用标准卡片结构

### A.2 gateway.py

- `_start`：硬编码创建 `FeishuSender` + `FeishuListener` → 通过工厂创建平台适配器
- `_poll_active_groups`：直接调用 `sender.fetch_chat_messages` → 通过 `PlatformQueries`
- `_make_heartbeat_callback`：直接 `from lq.feishu.cards import` → 使用标准卡片结构
- `_make_fake_event`：构造飞书 SDK 兼容对象 → 构造标准 `IncomingMessage`

### A.3 config.py

- `FeishuConfig` → 改为平台配置子类，新增 `PlatformConfig` 基类
- `LQConfig.feishu` → `LQConfig.platform: PlatformConfig`

### A.4 conversation.py

- `LocalSender` → 已经是很好的参考实现（实现了 sender 接口的本地模拟版本）

---

## 附录 B：完整动作清单

以下是 LingQue 内核需要与外部聊天平台交互的**全部 25 个动作**，一个不漏：

### 出站动作（Bot → 平台）

| # | 动作 | 方法 | 触发场景 |
|---|------|------|---------|
| 1 | 发送文本消息 | `send_text(chat_id, text)` | 私聊回复、群聊介入、心跳任务、早安问候、好奇心探索通知 |
| 2 | 引用回复文本 | `reply_text(message_id, text)` | 私聊引用回复、群聊 @回复、非文本消息提示 |
| 3 | 发送卡片消息 | `send_card(chat_id, card)` | 日程卡片、错误卡片、审批卡片、工具通知 |
| 4 | 引用回复卡片 | `reply_card(message_id, card)` | 工具通知（引用回复形式） |
| 5 | 添加 Reaction | `add_reaction(message_id, emoji)` | 处理中指示、bot 间意图信号 |
| 6 | 移除 Reaction | `remove_reaction(message_id, reaction_id)` | 处理完成后清理指示器 |

### 入站事件（平台 → Bot）

| # | 事件 | 标准类型 | 说明 |
|---|------|---------|------|
| 7 | 收到消息 | `message.received` | 文本/图片/富文本/文件/语音/视频/贴纸/分享 |
| 8 | Reaction 被添加 | `reaction.added` | 其他用户/bot 对消息添加表情 |
| 9 | Reaction 被移除 | `reaction.removed` | （当前忽略） |
| 10 | Bot 被加入群聊 | `bot.added_to_group` | 触发自我介绍 |
| 11 | 用户加入群聊 | `user.joined_group` | 触发欢迎消息 |
| 12 | 卡片交互回调 | `card.action` | 用户点击确认/取消按钮 |
| 13 | 消息已读 | （忽略） | |
| 14 | 消息撤回 | （忽略） | |
| 15 | Bot 被移出群聊 | （忽略） | |
| 16 | 用户退出群聊 | （忽略） | |

### 查询动作（Bot → 平台 → Bot）

| # | 动作 | 方法 | 触发场景 |
|---|------|------|---------|
| 17 | 获取 Bot 身份 | `get_bot_identity()` | 启动时初始化 |
| 18 | 获取用户名 | `get_user_name(user_id, chat_id)` | 消息处理时解析发送者 |
| 19 | 解析 ID 名字 | `resolve_name(user_id)` | 解析 bot/用户名字 |
| 20 | 获取群组成员 | `get_chat_members(chat_id)` | bot 入群时刷新、用户名解析 |
| 21 | 获取群内 Bot | `get_bot_members(chat_id)` | bot 协作感知、自我认知 |
| 22 | 拉取历史消息 | `fetch_chat_messages(chat_id, count)` | 群聊轮询、上下文补充、早安检测 |
| 23 | 下载媒体资源 | `download_media(message_id, resource_key)` | 处理图片消息 |
| 24 | 创建日历事件 | `create_event(summary, start, end, desc)` | 用户指令 |
| 25 | 查询日历事件 | `list_events(start, end)` | 用户指令、晨报 |
