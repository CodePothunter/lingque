"""平台抽象层单元测试 — types / adapter ABC / LocalAdapter / memory"""

from __future__ import annotations

import asyncio
from dataclasses import fields

import pytest

from lq.platform.types import (
    ChatType,
    SenderType,
    MessageType,
    Mention,
    IncomingMessage,
    OutgoingMessage,
    BotIdentity,
    ChatMember,
    Reaction,
    CardAction,
)
from lq.platform.adapter import PlatformAdapter
from lq.conversation import LocalAdapter


# ╔════════════════════════════════════════════════════╗
# ║  1. 枚举类型                                       ║
# ╚════════════════════════════════════════════════════╝


class TestEnums:
    def test_chat_type_values(self):
        assert ChatType.PRIVATE == "private"
        assert ChatType.GROUP == "group"

    def test_sender_type_values(self):
        assert SenderType.USER == "user"
        assert SenderType.BOT == "bot"

    def test_message_type_values(self):
        assert MessageType.TEXT == "text"
        assert MessageType.IMAGE == "image"
        assert MessageType.RICH_TEXT == "rich_text"
        assert MessageType.UNKNOWN == "unknown"

    def test_enums_are_str(self):
        """枚举同时是 str，可直接用于字符串比较"""
        assert isinstance(ChatType.PRIVATE, str)
        assert isinstance(SenderType.USER, str)
        assert isinstance(MessageType.TEXT, str)


# ╔════════════════════════════════════════════════════╗
# ║  2. 数据类型（dataclass）                           ║
# ╚════════════════════════════════════════════════════╝


class TestIncomingMessage:
    def test_required_fields(self):
        msg = IncomingMessage(
            message_id="msg_001",
            chat_id="chat_001",
            chat_type=ChatType.PRIVATE,
            sender_id="user_001",
            sender_type=SenderType.USER,
            sender_name="Alice",
            message_type=MessageType.TEXT,
            text="hello",
        )
        assert msg.message_id == "msg_001"
        assert msg.chat_type == ChatType.PRIVATE
        assert msg.text == "hello"

    def test_default_values(self):
        msg = IncomingMessage(
            message_id="m", chat_id="c", chat_type=ChatType.GROUP,
            sender_id="s", sender_type=SenderType.BOT, sender_name="Bot",
            message_type=MessageType.TEXT, text="hi",
        )
        assert msg.mentions == []
        assert msg.is_mention_bot is False
        assert msg.image_keys == []
        assert msg.reply_to_id == ""
        assert msg.timestamp == 0
        assert msg.raw is None

    def test_mentions(self):
        mention = Mention(user_id="bot_1", name="MyBot", is_bot_self=True)
        msg = IncomingMessage(
            message_id="m", chat_id="c", chat_type=ChatType.GROUP,
            sender_id="s", sender_type=SenderType.USER, sender_name="U",
            message_type=MessageType.TEXT, text="@MyBot hi",
            mentions=[mention], is_mention_bot=True,
        )
        assert len(msg.mentions) == 1
        assert msg.mentions[0].is_bot_self is True
        assert msg.is_mention_bot is True


class TestOutgoingMessage:
    def test_text_message(self):
        msg = OutgoingMessage(chat_id="c", text="hello")
        assert msg.chat_id == "c"
        assert msg.text == "hello"
        assert msg.reply_to == ""
        assert msg.card is None

    def test_card_message(self):
        card = {"type": "info", "title": "Test"}
        msg = OutgoingMessage(chat_id="c", card=card)
        assert msg.card == card
        assert msg.text == ""

    def test_reply_with_mentions(self):
        mention = Mention(user_id="u1", name="Bob", is_bot_self=False)
        msg = OutgoingMessage(
            chat_id="c", text="Hi @Bob", reply_to="msg_001",
            mentions=[mention],
        )
        assert msg.reply_to == "msg_001"
        assert len(msg.mentions) == 1


class TestBotIdentity:
    def test_fields(self):
        identity = BotIdentity(bot_id="bot_123", bot_name="灵雀")
        assert identity.bot_id == "bot_123"
        assert identity.bot_name == "灵雀"


class TestChatMember:
    def test_fields(self):
        member = ChatMember(user_id="u1", name="Alice", is_bot=False)
        assert not member.is_bot
        bot_member = ChatMember(user_id="b1", name="Bot", is_bot=True)
        assert bot_member.is_bot


class TestReaction:
    def test_required_fields(self):
        r = Reaction(
            reaction_id="r1", chat_id="c1", message_id="m1",
            emoji="SMILE", operator_id="u1", operator_type=SenderType.USER,
        )
        assert r.emoji == "SMILE"
        assert r.is_thinking_signal is False

    def test_thinking_signal(self):
        r = Reaction(
            reaction_id="r2", chat_id="c1", message_id="m1",
            emoji="EYES", operator_id="b1", operator_type=SenderType.BOT,
            is_thinking_signal=True,
        )
        assert r.is_thinking_signal is True


class TestCardAction:
    def test_defaults(self):
        action = CardAction(action_type="approve")
        assert action.value == {}
        assert action.operator_id == ""
        assert action.message_id == ""

    def test_with_value(self):
        action = CardAction(
            action_type="button_click",
            value={"key": "confirm", "tag": "approve"},
            operator_id="u1",
        )
        assert action.value["key"] == "confirm"


# ╔════════════════════════════════════════════════════╗
# ║  3. PlatformAdapter ABC                            ║
# ╚════════════════════════════════════════════════════╝


class TestPlatformAdapterABC:
    def test_cannot_instantiate_abc(self):
        """ABC 不能直接实例化"""
        with pytest.raises(TypeError):
            PlatformAdapter()  # type: ignore[abstract]

    def test_abstract_methods_declared(self):
        abstract = PlatformAdapter.__abstractmethods__
        expected = {
            "get_identity", "connect", "disconnect", "send",
            "start_thinking", "stop_thinking", "fetch_media",
            "resolve_name", "list_members",
        }
        assert expected == abstract

    def test_optional_methods_have_defaults(self):
        """可选方法（react/unreact/edit/unsend）不在 abstractmethods 中"""
        abstract = PlatformAdapter.__abstractmethods__
        assert "react" not in abstract
        assert "unreact" not in abstract
        assert "edit" not in abstract
        assert "unsend" not in abstract

    def test_partial_impl_cannot_instantiate(self):
        """部分实现仍然不能实例化"""

        class PartialAdapter(PlatformAdapter):
            async def get_identity(self):
                pass

            async def connect(self, queue):
                pass

        with pytest.raises(TypeError):
            PartialAdapter()


# ╔════════════════════════════════════════════════════╗
# ║  4. LocalAdapter                                   ║
# ╚════════════════════════════════════════════════════╝


class TestLocalAdapter:
    @pytest.fixture
    def adapter(self):
        return LocalAdapter("测试bot")

    async def test_get_identity(self, adapter):
        identity = await adapter.get_identity()
        assert identity.bot_id == "local_bot"
        assert identity.bot_name == "测试bot"

    async def test_connect_disconnect(self, adapter):
        """connect/disconnect 不抛异常"""
        queue = asyncio.Queue()
        await adapter.connect(queue)
        await adapter.disconnect()

    async def test_send_text(self, adapter, capsys):
        msg = OutgoingMessage(chat_id="c", text="hello world")
        result = await adapter.send(msg)
        assert result == "local_msg"
        captured = capsys.readouterr()
        assert "hello world" in captured.out

    async def test_send_card(self, adapter, capsys):
        card = {"elements": [{"content": "**card content**"}]}
        msg = OutgoingMessage(chat_id="c", card=card)
        result = await adapter.send(msg)
        assert result == "local_msg"
        captured = capsys.readouterr()
        assert "card content" in captured.out

    async def test_send_empty(self, adapter, capsys):
        """空文本空卡片仍返回 local_msg"""
        msg = OutgoingMessage(chat_id="c")
        result = await adapter.send(msg)
        assert result == "local_msg"

    async def test_start_stop_thinking(self, adapter):
        """start_thinking 返回 truthy handle，stop_thinking 设置完成信号"""
        handle = await adapter.start_thinking("msg_1")
        assert handle == "local"
        assert not adapter._turn_done.is_set()
        await adapter.stop_thinking("msg_1", handle)
        assert adapter._turn_done.is_set()

    async def test_turn_done_reset(self, adapter):
        """_turn_done 可重复使用：clear → stop_thinking → set"""
        adapter._turn_done.clear()
        assert not adapter._turn_done.is_set()
        await adapter.stop_thinking("msg_1", "local")
        assert adapter._turn_done.is_set()
        adapter._turn_done.clear()
        assert not adapter._turn_done.is_set()

    async def test_connect_stores_queue(self, adapter):
        """connect 保存 queue 引用"""
        queue = asyncio.Queue()
        await adapter.connect(queue)
        assert adapter._queue is queue

    async def test_fetch_media(self, adapter):
        result = await adapter.fetch_media("msg_1", "key_1")
        assert result is None

    async def test_resolve_name_known(self, adapter):
        name = await adapter.resolve_name("local_cli_user")
        assert name == "用户"

    async def test_resolve_name_unknown(self, adapter):
        name = await adapter.resolve_name("ou_abcdefghijklmnop")
        assert name == "ijklmnop"  # 取末尾 8 字符

    async def test_list_members(self, adapter):
        members = await adapter.list_members("any_chat")
        assert members == []

    async def test_optional_methods_return_defaults(self, adapter):
        """可选方法使用 ABC 默认实现"""
        assert await adapter.react("m1", "SMILE") is None
        assert await adapter.unreact("m1", "h1") is False
        assert await adapter.edit("m1", OutgoingMessage("c")) is False
        assert await adapter.unsend("m1") is False


# ╔════════════════════════════════════════════════════╗
# ║  5. MemoryManager（build_context 签名变更）         ║
# ╚════════════════════════════════════════════════════╝


class TestMemoryManager:
    @pytest.fixture
    def memory(self, tmp_path):
        from lq.memory import MemoryManager
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "SOUL.md").write_text("我是一个测试灵雀", encoding="utf-8")
        (ws / "MEMORY.md").write_text("## 测试记忆\n用户喜欢猫\n", encoding="utf-8")
        return MemoryManager(ws)

    def test_build_context_no_sender_param(self, memory):
        """build_context 不再接受 sender 参数"""
        import inspect
        sig = inspect.signature(memory.build_context)
        param_names = list(sig.parameters.keys())
        assert "sender" not in param_names
        assert "chat_id" in param_names
        assert "include_tools_awareness" in param_names

    def test_build_context_returns_string(self, memory):
        ctx = memory.build_context()
        assert isinstance(ctx, str)
        assert "测试灵雀" in ctx

    def test_build_context_with_chat_id(self, memory):
        ctx = memory.build_context(chat_id="test_chat")
        assert isinstance(ctx, str)

    def test_build_neighbor_context_empty(self, memory):
        result = memory.build_neighbor_context([])
        assert result == ""

    def test_build_neighbor_context_with_names(self, memory):
        result = memory.build_neighbor_context(["小助手", "大白"])
        assert "<neighbors>" in result
        assert "小助手" in result
        assert "大白" in result
        assert "</neighbors>" in result

    def test_read_soul(self, memory):
        assert "测试灵雀" in memory.read_soul()

    def test_read_memory(self, memory):
        assert "用户喜欢猫" in memory.read_memory()
