"""官方会话标题同步进对话（conversations/titles.py）：事件泵看到 session/title 就写对话标题，先后几个只留最后一个；
别的帧、空标题不理。"""

import pytest

from knowwork_server.conversations.models import Conversation
from knowwork_server.conversations.titles import TitleObserver, title_of

pytestmark = pytest.mark.anyio


def _title_frame(title):
    return {"type": "session/title", "seq": 3, "data": {"title": title, "source": "user"}}


def test_only_official_title_events_carry_a_title():
    assert title_of("session/event", _title_frame("  差旅\n报销 ")) == "差旅 报销"
    assert title_of("session/event", _title_frame("一" * 300)) == "一" * 200
    assert title_of("session/event", _title_frame("   ")) is None
    assert title_of("session/event", {"type": "turn/end", "data": {"title": "x"}}) is None
    assert title_of("agent/status", _title_frame("x")) is None
    assert title_of("session/event", {"type": "session/title", "data": "x"}) is None


async def test_the_latest_official_title_becomes_the_conversation_title(sessionmaker, conversation_id):
    titles = TitleObserver(sessionmaker)
    for title in ("第一句话", "差旅报销标准"):
        titles(conversation_id, "default", "session/event", _title_frame(title))
    titles(conversation_id, "default", "session/event", {"type": "turn/end", "data": {}})
    await titles.drain()
    async with sessionmaker() as session:
        assert (await session.get(Conversation, conversation_id)).title == "差旅报销标准"

    titles("c_missing", "default", "session/event", _title_frame("没有这段对话"))
    await titles.drain()  # 对话不在了：什么都不写，也不报错
