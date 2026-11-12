"""网页渠道挂对象（channels/targets.py；扩展登记的，例如专家）：

- 管理：挂了对象的不用选回答方式与资料，存的是对象的回答方式；渠道账号的授权跟着对象能查的资料（只管渠道管的那几类）；
  对象不在了、类型没人登记的不能挂；只有网页渠道能挂；公开访问时对象一样资料都没选不让挂（访客什么都查不到）；
- 对象改了（changed）：挂着它的渠道重新同步授权；删了：授权收回、入口关闭（配置读不到、换不了会话令牌）；
- 公开配置：标题、开场白、快捷提问没填时用对象的；
- 会话：从挂了对象的渠道开的会话挂到对象上（之后按对象自己登记的范围与说明答）；对象没认领这段对话时（挂上失败、
  对象删了）什么都不给查，不退回渠道自己的配置。"""

from dataclasses import replace

import pytest

from knowwork_server.channels.targets import Target

from .test_web import ALICE, BOSS, actor, http, make_web, open_session, started  # noqa: F401  （夹具）

pytestmark = pytest.mark.anyio

EXPERT = Target(kind="expert", id="ex1", name="经营分析", answer_mode="numbers",
                resources=("knowledge_base:builtin.kb1", "data_source:ds1", "skill:周报"),
                greeting="我能查销售库和财务制度", starters=("上月营收是多少",))
TARGETED = {"settings": {"target": "expert:ex1"}, "web": {"origins": ["https://bi.example.com"]}}


async def grants_of(env, channel_id: str) -> list[tuple[str, str]]:
    grants = await env.access.list_grants("boss", env.ws, subject=f"user:{channel_id}")
    return sorted((grant.resource, grant.action) for grant in grants)


async def test_targeted_channels_answer_and_read_as_the_target(env, http):  # noqa: F811
    env.experts.items["ex1"] = EXPERT
    channel = await make_web(env, http, **TARGETED)
    assert channel["settings"]["target"] == "expert:ex1"
    assert (channel["settings"]["answer_mode"], channel["settings"]["bases"], channel["settings"]["resources"]) == \
        ("numbers", [], [])
    assert channel["target"] == {"ref": "expert:ex1", "label": "专家", "name": "经营分析"}
    # 技能不是渠道管的那几类：不授
    assert await grants_of(env, channel["id"]) == [("data_source:ds1", "use"), ("knowledge_base:builtin.kb1", "use")]
    config = (await http.get(f"/api/embed/v1/channels/{channel['web']['web_key']}")).json()
    assert (config["title"], config["greeting"], config["starters"], config["answerMode"]) == \
        ("经营分析", "我能查销售库和财务制度", ["上月营收是多少"], "numbers")
    # 渠道自己填了的优先
    own = await make_web(env, http, **{**TARGETED, "web": {"title": "BI 助手", "greeting": "你好", "starters": ["a"]}})
    config = (await http.get(f"/api/embed/v1/channels/{own['web']['web_key']}")).json()
    assert (config["title"], config["greeting"], config["starters"]) == ("BI 助手", "你好", ["a"])
    listed = (await http.get(f"/api/workspaces/{env.ws}/channels/targets", headers=BOSS)).json()["items"]
    assert listed == [{"ref": "expert:ex1", "label": "专家", "name": "经营分析", "answer_mode": "numbers"}]


async def test_changed_targets_resync_and_deleted_ones_close_the_door(env, http):  # noqa: F811
    env.experts.items["ex1"] = EXPERT
    channel = await make_web(env, http, **TARGETED)
    key = channel["web"]["web_key"]
    env.experts.items["ex1"] = replace(EXPERT, resources=("knowledge_base:builtin.kb2",))
    await env.targets.changed(env.ws, "expert:ex1")
    assert await grants_of(env, channel["id"]) == [("knowledge_base:builtin.kb2", "use")]
    del env.experts.items["ex1"]
    await env.targets.changed(env.ws, "expert:ex1")
    assert await grants_of(env, channel["id"]) == []
    assert (await http.get(f"/api/embed/v1/channels/{key}")).status_code == 404
    assert (await open_session(http, key)).status_code == 404
    view = (await http.get(f"/api/workspaces/{env.ws}/channels", headers=BOSS)).json()["items"]
    assert next(item for item in view if item["id"] == channel["id"])["target"] == \
        {"ref": "expert:ex1", "label": "专家", "name": None}


async def test_targets_are_checked_when_saving(env, http):  # noqa: F811
    root = f"/api/workspaces/{env.ws}/channels"
    env.experts.items["ex1"] = EXPERT
    env.experts.items["bare"] = replace(EXPERT, id="bare", resources=("skill:周报",))
    unknown = await http.post(root, json={**_web(), **TARGETED, "settings": {"target": "expert:nope"}}, headers=BOSS)
    assert unknown.status_code == 422
    nobody = await http.post(root, json={**_web(), "settings": {"target": "robot:r1"}}, headers=BOSS)
    assert nobody.status_code == 422
    im = {**_web(), "platform": "wecom", "bot_id": "b1", "secret": "sk-wecom-secret-123456", "web": None,
          "settings": {"target": "expert:ex1"}}
    assert (await http.post(root, json=im, headers=BOSS)).status_code == 422
    # 公开访问用渠道账号查：对象一样资料都没选（或都不是渠道管的）就什么都查不到，不让挂
    bare = await http.post(root, json={**_web(), "settings": {"target": "expert:bare"}}, headers=BOSS)
    assert bare.status_code == 422 and "能查的" in bare.json()["detail"]
    # 认证访问按本人的权限：可以挂
    member = await http.post(root, json={**_web(), "audience": "member", "settings": {"target": "expert:bare"}},
                             headers=BOSS)
    assert member.status_code == 201, member.text


async def test_conversations_attach_to_the_target_and_never_fall_back(env, http):  # noqa: F811
    env.experts.items["ex1"] = EXPERT
    channel = await make_web(env, http, **TARGETED)
    conversation_id = await started(env, actor(env, channel["id"], ALICE))
    assert env.experts.attached == [(conversation_id, "ex1", channel["id"])]
    # 对象自己的范围提供者没认领时（这里没有）：什么都不给查
    scope = await env.channels.web.scope(env.ws, conversation_id)
    assert scope is not None and scope.label == "经营分析"
    assert (scope.knowledge_bases, scope.resources, scope.skills, scope.mcp_servers) == ((), (), (), ())


def _web() -> dict:
    return {"name": "BI 小窗", "platform": "web", "web": {"origins": []}}
