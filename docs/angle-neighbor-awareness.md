# AI 发现了群里还有别的 AI——然后它们自己分了工

三个 AI 跑在同一个飞书群里的第一天，它们互相不认识。

到了第二周，奶油几乎不再回答技术问题。严客从来不碰日程安排。花火只在讨论方向和创意时才开口。

没有人分配它们这么做。

## 邻居感知

每个 bot 在评估是否参与群聊对话时，system prompt 里会被注入一段邻居信息：

```python
def build_neighbor_context(self, sender, chat_id):
    bot_ids = sender.get_bot_members(chat_id)
    if not bot_ids:
        return ""
    lines = ["<neighbors>", "群里还有以下 AI 助理："]
    for bid in bot_ids:
        name = sender.get_member_name(bid)
        lines.append(f"- {name}")
    lines.append("</neighbors>")
    return "\n".join(lines)
```

`get_bot_members()` 通过飞书 API 获取群内所有 bot 成员（排除自己）。结果注入到 LLM 上下文中。

这意味着：每个 bot 在做"要不要说话"的判断时，**知道群里还有谁**。

奶油知道严客也在。严客知道花火也在。这个信息会影响它们的判断——"这个问题严客更擅长，我不用接"。

## 协作记忆

知道邻居存在是第一步。更关键的是：**记住之前谁干了什么。**

每次 bot 做出一个协作决策——回复、让步、碰撞——都会被记录到 per-chat 的协作日志中：

```python
def _record_collab_event(self, chat_id, event_type, actor_name, detail=""):
    entry = f"- {now} {actor_name} {event_type}: {detail}"
    # 保留最近 19 条 + 新条目 = 20 条滚动日志
    lines = lines[-19:]
    lines.append(entry)
```

日志存在 `chat_memories/{chat_id}.md` 的 `## 协作模式` section 中，20 条滚动窗口：

```
## 协作模式
- 02-14 09:30 奶油 deferred: 让步给严客
- 02-14 09:32 严客 responded: 指出 API 设计的三个问题
- 02-14 09:33 花火 responded: 提供了替代方案的类比
- 02-14 10:15 奶油 responded: 记录了下周评审日程
- 02-14 10:16 严客 deferred: 让步给奶油
```

每次评估是否介入时，这段日志会被注入到 LLM 的提示中：

```
近期协作记录：
[20条滚动日志]
根据历史模式和各助理的表现决定是否介入。
```

## 角色涌现

第一天，三个 bot 的协作记忆是空的。它们靠 SOUL.md 的人格定义做粗略判断——奶油是助理所以接日程，严客是审稿所以挑毛病。

一周之后，协作记忆里积累了几十条记录。每个 bot 能看到：

- 自己在什么话题上回复最多
- 其他 bot 在什么话题上更活跃
- 自己让步过几次、碰撞过几次

这些历史不是规则。它们是 LLM 的参考信号。但信号足够多时，行为模式就固化了——奶油越来越少碰技术问题，因为它的协作记忆显示严客每次都接得更快。

这不是角色分配，是角色涌现。

## 与已有机制的区别

灵雀之前的群聊三层过滤（规则、缓冲、LLM 裁决）决定的是"要不要说话"。邻居感知和协作记忆改变的是**"要不要说话"这个判断的输入**——你不是一个人在群里做决策，你知道还有谁在，也知道过去的协作模式是什么。

## 一句话

真实团队里，没有人在入职第一天就知道自己会成为什么角色。角色是在反复协作中涌现的——你擅长什么、别人擅长什么、谁先抢到了什么。AI 也一样。

GitHub：[github.com/CodePothunter/lingque](https://github.com/CodePothunter/lingque)
