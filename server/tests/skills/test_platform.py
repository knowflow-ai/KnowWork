"""企业技能与内置技能（企业层，代码里叫 platform）：系统管理员维护、发给选定或全部空间；发到的空间默认全体成员都能用，
上层优先；空间管理员可以在本空间停用、收窄给谁用。"""

from dataclasses import dataclass

import pytest

from knowwork_server.skills.bundled import BundledSkills
from knowwork_server.skills.errors import PackageInvalid, SkillConflict, SkillForbidden, SkillNotFound
from knowwork_server.skills.layers import UpperSkills

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio

ROOT = "root"  # 系统管理员


@dataclass(frozen=True)
class Bundled:
    name: str
    description: str
    user_invocable: bool = True


@pytest.fixture
async def platform(env):
    await env.access.create_workspace(workspace_id="default", name="默认空间", owner_user_id="alice")
    env.access.set_system_admin(ROOT)
    return env.skills.platform


async def published_platform(platform, name="company-style", audience=("*",), **kwargs):
    view = await platform.install(ROOT, skill_zip(name, **kwargs))
    await platform.publish(ROOT, view.id)
    return await platform.set_audience(ROOT, view.id, list(audience))


async def names(env, user="carol", workspace="ws1"):
    return [(e.name, e.scope) for e in await env.skills.delivery.catalog.for_session(workspace, user)]


async def test_only_system_admin_manages_platform_skills(env, platform):
    with pytest.raises(SkillForbidden):
        await platform.install("alice", skill_zip("company-style"))
    view = await platform.install(ROOT, skill_zip("company-style"))
    assert (view.scope, view.status, view.audience) == ("platform", "draft", ())
    with pytest.raises(SkillForbidden):
        await platform.skills("alice")
    with pytest.raises(SkillNotFound):  # 默认空间的空间技能路由碰不到公共技能
        await env.skills.service.get(actor("alice", "default"), view.id, scope="workspace")
    assert await env.skills.service.list(actor("alice", "default"), "workspace") == ()
    assert [v.name for v in await platform.skills(ROOT)] == ["company-style"]


async def test_reaches_only_audience_and_everyone_there_can_use_it(env, platform):
    view = await platform.install(ROOT, skill_zip("company-style"))
    await platform.publish(ROOT, view.id)
    assert await names(env) == [], "刚建时没发给任何空间"
    await platform.set_audience(ROOT, view.id, ["ws1"])
    assert await names(env) == [("company-style", "platform")], "发到的空间里成员不用另外授权"
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    assert await names(env, "alice", "ws2") == []
    everywhere = await platform.set_audience(ROOT, view.id, ["*", "ws1"])
    assert everywhere.audience == ("*",)
    assert await names(env, "alice", "ws2") == [("company-style", "platform")]
    await platform.set_enabled(ROOT, view.id, False)
    assert await names(env) == []
    with pytest.raises(PackageInvalid):
        await platform.set_audience(ROOT, view.id, ["nope"])


async def test_platform_wins_over_workspace_and_personal(env, platform):
    svc = env.skills.service
    shared = await svc.install(actor("bob"), "workspace", skill_zip("summary"))
    await svc.publish(actor("bob"), shared.id)
    await env.access.grant("alice", "ws1", "user:carol", f"skill:{shared.id}", "use")
    mine = await svc.install(actor("carol"), "personal", skill_zip("summary", description="我的"))
    await published_platform(platform, "summary")
    assert await names(env) == [("summary", "platform")]
    catalog = env.skills.delivery.catalog
    assert await catalog.inactive_workspace("ws1") == {shared.id: "name_taken"}
    assert await catalog.inactive_personal("ws1", "carol") == {mine.id: "name_taken"}


async def test_content_of_platform_skill_is_loaded_and_pinned(env, platform):
    view = await published_platform(platform, body="第一版")
    catalog = env.skills.delivery.catalog
    [first] = await catalog.for_session("ws1", "carol")
    assert (await catalog.load("ws1", "carol", first.name, first.digest)).content == "第一版"
    await platform.replace_draft(ROOT, view.id, skill_zip("company-style", body="第二版"))
    await platform.publish(ROOT, view.id, version="2")
    [second] = await catalog.for_session("ws1", "carol")
    assert (await catalog.load("ws1", "carol", second.name, second.digest)).content == "第二版"
    assert (await catalog.load("ws1", "carol", first.name, first.digest)).content == "第一版", "会话钉住旧版本"


async def test_workspace_admin_turns_off_platform_and_bundled_here(env, platform):
    view = await published_platform(platform)
    host = BundledSkills(_host(Bundled("office-docx", "Word"), Bundled("company-style", "同名的自带")))
    layers = UpperSkills(env.sessionmaker, env.access, host)
    listed = await layers.list(actor("bob"))
    assert [(s.key, s.source, s.off) for s in listed] == [
        (f"platform:{view.id}", "platform", False), ("bundled:office-docx", "bundled", False)]
    with pytest.raises(SkillForbidden):
        await layers.list(actor("carol"))
    with pytest.raises(SkillForbidden):
        await layers.set_off(actor("carol"), f"platform:{view.id}", True)
    with pytest.raises(PackageInvalid):
        await layers.set_off(actor("bob"), "skill:x", True)

    await layers.set_off(actor("bob"), f"platform:{view.id}", True)
    await layers.set_off(actor("bob"), f"platform:{view.id}", True)  # 重复停用不报错
    await layers.set_off(actor("bob"), "bundled:office-docx", True)
    catalog = env.skills.delivery.catalog
    assert await names(env) == []
    assert await catalog.bundled_off("ws1", "carol") == ("office-docx",)
    assert all(s.off for s in await layers.list(actor("bob")))
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    assert await names(env, "alice", "ws2") == [("company-style", "platform")], "只停用本空间"
    assert await catalog.bundled_off("ws2", "alice") == ()
    await layers.set_off(actor("bob"), f"platform:{view.id}", False)
    assert await names(env) == [("company-style", "platform")]


async def test_workspace_admin_narrows_who_uses_platform_and_bundled_here(env, platform):
    """收窄给谁用：选中的对象（和管理员）能用；用不了企业版的人，同名的空间技能照常生效；放回全体成员不看授权；
    收窄到的用户组被删时不会放大成全体成员。"""
    view = await published_platform(platform, "summary")
    host = BundledSkills(_host(Bundled("office-docx", "Word")))
    layers = UpperSkills(env.sessionmaker, env.access, host)
    svc = env.skills.service
    shared = await svc.install(actor("bob"), "workspace", skill_zip("summary", description="空间版"))
    await svc.publish(actor("bob"), shared.id)
    catalog = env.skills.delivery.catalog
    key = f"platform:{view.id}"

    with pytest.raises(SkillForbidden):
        await layers.set_users(actor("carol"), key, ["user:carol"])
    with pytest.raises(PackageInvalid):
        await layers.set_users(actor("bob"), "skill:x", ["user:carol"])
    group = await env.access.create_group("alice", "ws1", "试点组")
    await env.access.add_group_member("alice", "ws1", group, "dave")
    await layers.set_users(actor("bob"), key, [f"group:{group}"])
    await layers.set_users(actor("bob"), "bundled:office-docx", [])
    assert {s.key: s.narrowed for s in await layers.list(actor("bob"))} == {key: True, "bundled:office-docx": True}
    assert await names(env, "carol") == [("summary", "workspace")], "用不了企业版的人，同名的空间技能照常生效"
    assert await names(env, "dave") == [("summary", "platform")]
    assert await catalog.bundled_off("ws1", "carol") == ("office-docx",)
    assert await catalog.bundled_off("ws1", "bob") == (), "管理员本来就能用全部"

    await env.access.delete_group("alice", "ws1", group)
    assert await names(env, "dave") == [("summary", "workspace")], "收窄到的组没了，只会更窄，不会放大成全体"

    await layers.set_users(actor("bob"), key, ["role:editor", "user:carol"])
    assert [s.narrowed for s in await layers.list(actor("bob")) if s.key == key] == [False]
    assert await names(env, "carol") == [("summary", "platform")]
    await layers.set_off(actor("bob"), key, True)
    await layers.set_off(actor("bob"), key, False)
    assert await names(env, "carol") == [("summary", "platform")], "启用回来时仍是全体成员"


async def test_promote_workspace_skill_to_platform(env, platform):
    svc = env.skills.service
    ws_skill = await svc.install(actor("bob"), "workspace", skill_zip("contract-review", version="1.0.0"))
    with pytest.raises(SkillNotFound):  # 还没发布的不能收
        await platform.promote(ROOT, "ws1", ws_skill.id)
    await svc.publish(actor("bob"), ws_skill.id)
    assert [(p.workspace_id, p.name) for p in await platform.promotable(ROOT)] == [("ws1", "contract-review")]
    with pytest.raises(SkillForbidden):
        await platform.promote("bob", "ws1", ws_skill.id)
    copy = await platform.promote(ROOT, "ws1", ws_skill.id)
    assert (copy.scope, copy.status, copy.draft_digest, copy.audience) == ("platform", "draft", ws_skill.draft_digest, ())
    with pytest.raises(SkillConflict) as caught:
        await platform.promote(ROOT, "ws1", ws_skill.id)
    assert caught.value.code == "already_promoted"
    await svc.replace_draft(actor("bob"), ws_skill.id, skill_zip("contract-review", version="1.1.0", body="改过"))
    await svc.publish(actor("bob"), ws_skill.id)
    again = await platform.promote(ROOT, "ws1", ws_skill.id)
    assert again.id == copy.id and again.draft_revision == 2 and again.draft["version"] == "1.1.0"

    other = await svc.install(actor("bob"), "workspace", skill_zip("company-style"))
    await svc.publish(actor("bob"), other.id)
    await platform.install(ROOT, skill_zip("company-style"))
    with pytest.raises(SkillConflict) as caught:
        await platform.promote(ROOT, "ws1", other.id)
    assert caught.value.code == "skill_exists"


def _host(*skills: Bundled):
    async def fetch():
        return skills

    return fetch
