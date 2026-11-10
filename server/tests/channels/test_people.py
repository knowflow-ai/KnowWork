"""认人的渠道：私聊按提问人本人回答，会话记在本人名下、带上来源；对不上（没同步到、离职、不是空间成员）时回一句原因
并记进没认出的人，能免登的给免登入口；认出来后清掉记录；群聊照旧用渠道账号；这一轮等人确认时不取消、给网页链接。
管理：认人要选同平台、还在的组织来源，群聊知识库可以不选；不认人时不留来源。"""

import pytest

from knowwork_server.channels.admin import ChannelInput, Invalid
from knowwork_server.channels.settings import ChannelSettings
from knowwork_server.channels.types import IncomingMessage
from knowwork_server.db.base import in_workspace
from knowwork_server.identity.im_identities import ImIdentitySource
from knowwork_server.identity.models import ExternalIdentity, User

from .conftest import BASE_URL, FakeConnection, answer, make_channel, run

pytestmark = pytest.mark.anyio
LOGIN = "/api/auth/sso/src-wecom/login"


def wecom(env, *, source_id="src-wecom", login=LOGIN, name="示例科技") -> ImIdentitySource:
    """身份表是全局的、测试之间不清空：身份域带上本测试的空间 id，别的测试留下的人对不上来"""
    return ImIdentitySource(id=source_id, platform="wecom", name=name, realm=f"wecom:{env.ws}", login_url=login)


def message(channel, text="我的报销额度是多少", *, user="zhangsan", name="zhangsan", group="", msgid=None):
    return IncomingMessage(channel_id=channel.id, workspace_id=channel.workspace_id, platform="wecom",
                           external_id=user, chat_type="group" if group else "direct", chat_id=group,
                           message_id=msgid or f"m-{text}-{user}-{group}", text=text, display_name=name,
                           ids={"userid": user})


async def person(env, user_id, userid, *, name="张三", member=True, active=True, status="active") -> str:
    user_id = f"{env.ws}-{user_id}"
    async with env.global_sessionmaker() as session, session.begin():
        session.add(User(id=user_id, email=None, name=name, status=status))
        await session.flush()  # 两张表没建关系：先落用户，对照表的外键才找得到
        session.add(ExternalIdentity(realm=f"wecom:{env.ws}", subject=userid, user_id=user_id, active=active))
    if member:
        await env.access.join_workspace(user_id, env.ws)  # 普通成员，同渠道账号进空间的方式
    return user_id


async def member_channel(env, source=None, **settings):
    source = source or wecom(env)
    if source not in env.sources:
        env.sources.append(source)
    return await make_channel(env, audience="member", identity_source=source.id, **settings)


async def strangers(env, channel):
    with in_workspace(env.ws):
        return await env.channels.admin.strangers("boss", env.ws, channel.id)


async def test_recognized_people_are_answered_as_themselves(env):
    channel = await member_channel(env)
    connection = FakeConnection()
    # 还没对上时先来过一次：记进没认出的人
    await env.channels.service.handle(message(channel, "第一问"), connection)
    assert [s.reason for s in await strangers(env, channel)] == ["unknown"]

    zhang = await person(env, "u-zhang", "zhangsan")
    env.conversations.scripts = [answer(1, "你的报销额度是每月 3000 元。"), answer(2, "第二个回答")]
    await env.channels.service.handle(message(channel), connection)
    [conversation, title, selection] = env.conversations.created[0]
    # 会话在本人名下、记下渠道与平台（「最近」里打来源标记）；不点名知识库：能查的就是他自己能查的
    assert (conversation.user_id, conversation.channel_id, conversation.source) == (zhang, channel.id, "wecom")
    assert (title, selection.knowledge_bases) == ("我的报销额度是多少", None)
    assert connection.streams[0].final == "你的报销额度是每月 3000 元。"
    assert await strangers(env, channel) == []  # 认出来了，从没认出的人里删掉

    await env.channels.service.handle(message(channel, "那差旅呢"), connection)
    assert [sent[0] for sent in env.conversations.sent] == [conversation.id, conversation.id]
    with in_workspace(env.ws):
        [seen] = await env.channels.admin.sessions("boss", env.ws, channel.id)
    assert (seen.user_id, seen.user_name, seen.chat_type) == (zhang, "张三", "direct")


async def test_unknown_people_get_a_sign_in_link_and_are_listed(env):
    channel = await member_channel(env)
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, "第一问", name="小王", user="xiaowang"), connection)
    await env.channels.service.handle(message(channel, "第二问", name="小王", user="xiaowang"), connection)
    assert connection.replies[-1] == ("还没认出你是谁。点这里用企业微信登录一次，之后就按你自己的权限回答：\n"
                                      f"{BASE_URL}/api/auth/sso/src-wecom/login")
    assert env.conversations.created == []
    [stranger] = await strangers(env, channel)
    assert (stranger.external_id, stranger.display_name, stranger.reason, stranger.attempts) == (
        "xiaowang", "小王", "unknown", 2)


async def test_without_sign_in_unknown_people_are_told_to_ask_the_admin(env):
    channel = await member_channel(env, wecom(env, source_id="src-sync", login="", name="只同步"))
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.replies[-1] == "还没认出你是谁：你的企业微信账号还没同步进来，请联系管理员。"


async def test_left_people_and_outsiders_are_turned_away(env):
    channel = await member_channel(env)
    await person(env, "u-left", "left", active=False)  # 组织同步判了离职
    await person(env, "u-out", "outsider", member=False)  # 认得出，但不是这个空间的成员
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, user="left"), connection)
    assert connection.replies[-1] == "你的账号已停用，请联系管理员。"
    await env.channels.service.handle(message(channel, user="outsider"), connection)
    assert connection.replies[-1] == "你还不是这个空间的成员，请联系管理员把你加进来。"
    assert {(s.external_id, s.reason) for s in await strangers(env, channel)} == {
        ("left", "inactive"), ("outsider", "not_member")}
    assert env.conversations.created == []


async def test_groups_still_use_the_channel_account(env):
    channel = await member_channel(env)
    await person(env, "u-zhang", "zhangsan")
    env.conversations.scripts = [answer(1, "群里的回答")]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel, group="chat-1"), connection)
    [conversation, _, _] = env.conversations.created[0]
    assert conversation.user_id == channel.id  # 回答全群看得见：用渠道账号，不用本人权限

    only_private = await member_channel(env, bases=[])  # 只开私聊：群聊没选知识库
    await env.channels.service.handle(message(only_private, "群里问", group="chat-2"), connection)
    assert connection.replies[-1] == "这个机器人在群里没有可查的知识库。要按你自己的权限查，请私聊我。"


async def test_waiting_turns_are_kept_and_continued_on_the_web(env):
    channel = await member_channel(env)
    await person(env, "u-zhang", "zhangsan")
    env.conversations.scripts = [run(1, ("item.completed", {"kind": "message",
                                                            "data": {"role": "assistant", "text": "我先查一下"}}),
                                     ("run.waiting", {"data": {"reason": "approval"}}))]
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    conversation = env.conversations.created[0][0]
    assert connection.streams[0].final == (f"我先查一下\n\n（这一步要你确认，点这里在网页里继续："
                                           f"{BASE_URL}/chat/{conversation.id}）")
    assert env.conversations.cancelled == []  # 会话在本人名下，不取消，去网页里接着确认

    # 没去网页处理、又在 IM 里问：不往会话里发（会排在等批准的那一轮后面一直等），先请他去网页处理
    env.conversations.approvals_waiting = ["approval-1"]
    await env.channels.service.handle(message(channel, "那差旅呢"), connection)
    assert connection.streams[1].final == f"上一步还在等你确认，先在网页里处理完再接着问：{BASE_URL}/chat/{conversation.id}"
    assert len(env.conversations.sent) == 1


async def test_a_removed_source_makes_the_bot_ask_for_a_new_save(env):
    channel = await member_channel(env)
    env.sources.clear()  # 组织来源被删了
    connection = FakeConnection()
    await env.channels.service.handle(message(channel), connection)
    assert connection.replies[-1] == "这个机器人的配置不完整，请联系管理员在渠道里重新保存一次。"


def _input(*, audience="member", platform="wecom", **settings) -> ChannelInput:
    return ChannelInput(name="助手", platform=platform, enabled=True, bot_id="bot-check", bot_name="助手",
                        secret="sk-secret-123456", settings=ChannelSettings(**settings), audience=audience)


async def test_saving_a_recognizing_channel_checks_its_source(env):
    env.sources.extend([wecom(env), ImIdentitySource(id="src-feishu", platform="feishu", name="飞书", realm="feishu:t")])
    admin = env.channels.admin
    with in_workspace(env.ws):
        for data, error in [
            (_input(), "请选认人用的组织来源"),
            (_input(identity_source="missing"), "这个组织来源不存在了，请重新选"),
            (_input(identity_source="src-feishu"), "组织来源和机器人不是同一个平台"),
            (_input(audience="public", bases=[]), "至少选一个知识库：机器人只按选的知识库回答"),
        ]:
            with pytest.raises(Invalid, match=error):
                await admin.create("boss", env.ws, data)
        # 认人时群聊知识库可以不选
        view = await admin.create("boss", env.ws, _input(identity_source="src-wecom"))
        assert (view.audience, view.settings["identity_source"], view.settings["bases"]) == ("member", "src-wecom", [])
        # 改回不认人：来源不留
        view = await admin.update("boss", env.ws, view.id, _input(audience="public", bases=["builtin.kb1"],
                                                                  identity_source="src-wecom"))
        assert (view.audience, view.settings["identity_source"]) == ("public", None)
        listed = await admin.identity_sources("boss", env.ws, "wecom")
    assert [(source.id, source.name, source.login) for source in listed] == [("src-wecom", "示例科技", True)]
