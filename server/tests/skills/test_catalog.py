"""本会话可用技能清单与宿主拉取内容：只给已发布、已启用、看得到的技能；内容按摘要钉住版本并校验。"""

import hashlib

import pytest

from knowwork_server.skills.errors import SkillNotFound
from knowwork_server.skills.service import StorageUnavailable

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio

EXTRACT = {"allowed": "extract_records kw_now", "completion": "extract_records"}


async def published(env, user, scope, name, **kwargs):
    svc = env.skills.service
    view = await svc.install(actor(user), scope, skill_zip(name, **kwargs))
    await svc.publish(actor(user), view.id)
    return view


async def test_catalog_contents_and_filters(env):
    catalog = env.skills.delivery.catalog
    ws_skill = await published(env, "bob", "workspace", "contract-extract", version="2.0.0", **EXTRACT)
    await env.skills.service.install(actor("bob"), "workspace", skill_zip("draft-only"))
    mine = await published(env, "dave", "personal", "my-notes")
    assert await catalog.for_session("ws1", "dave") != ()
    assert [e.name for e in await catalog.for_session("ws1", "dave")] == ["my-notes"]

    await env.access.grant("alice", "ws1", "user:dave", f"skill:{ws_skill.id}", "use")
    entries = {e.name: e for e in await catalog.for_session("ws1", "dave")}
    assert list(entries) == ["contract-extract", "my-notes"]
    host = entries["contract-extract"].to_host()
    assert host["version"] == "2.0.0"
    assert host["unlocks"] == ["extract_records"]
    assert host["completionTools"] == ["extract_records"]
    assert host["allowedTools"] == ["extract_records", "kw_now"]
    assert host["contentDigest"] == hashlib.sha256("按步骤抽取合同字段。".encode()).hexdigest()
    assert [e.name for e in await catalog.for_session("ws1", "carol")] == []  # 别人的个人技能看不到

    await env.skills.service.set_enabled(actor("dave"), mine.id, False)
    assert [e.name for e in await catalog.for_session("ws1", "dave")] == ["contract-extract"]
    assert await catalog.for_session("ws1", "mallory") == ()


async def test_personal_skill_with_taken_name_is_skipped(env):
    catalog = env.skills.delivery.catalog
    ws_skill = await published(env, "bob", "workspace", "summary")
    await published(env, "dave", "personal", "summary", description="我的版本")
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{ws_skill.id}", "use")
    entries = await catalog.for_session("ws1", "dave")
    assert [(e.name, e.scope) for e in entries] == [("summary", "workspace")]


async def test_load_content_pins_version_and_checks_scope(env):
    svc, catalog = env.skills.service, env.skills.delivery.catalog
    view = await published(env, "dave", "personal", "my-notes", body="第一版正文")
    first = (await catalog.for_session("ws1", "dave"))[0]
    content = await catalog.load("ws1", "dave", "my-notes", first.digest)
    assert (content.content, content.content_digest) == ("第一版正文", first.content_digest)

    updated = await svc.replace_draft(actor("dave"), view.id, skill_zip("my-notes", body="第二版正文"))
    with pytest.raises(SkillNotFound):  # 草稿不对外
        await catalog.load("ws1", "dave", "my-notes", updated.draft_digest)
    await svc.publish(actor("dave"), view.id, version="2")
    assert (await catalog.load("ws1", "dave", "my-notes", updated.draft_digest)).content == "第二版正文"
    pinned = await catalog.load("ws1", "dave", "my-notes", first.digest)  # 会话钉住旧版本
    assert pinned.content == "第一版正文"
    with pytest.raises(SkillNotFound):
        await catalog.load("ws1", "carol", "my-notes", first.digest)
    with pytest.raises(SkillNotFound):
        await catalog.load("ws1", "dave", "my-notes", "0" * 64)


async def test_revoked_grant_stops_loading(env):
    catalog = env.skills.delivery.catalog
    view = await published(env, "bob", "workspace", "contract-extract")
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{view.id}", "use")
    entry = (await catalog.for_session("ws1", "dave"))[0]
    assert (await catalog.load("ws1", "dave", entry.name, entry.digest)).content
    await env.access.revoke("alice", "ws1", "user:dave", f"skill:{view.id}")
    with pytest.raises(SkillNotFound):
        await catalog.load("ws1", "dave", entry.name, entry.digest)


async def test_tampered_content_is_not_served(env):
    catalog = env.skills.delivery.catalog
    await published(env, "dave", "personal", "my-notes")
    entry = (await catalog.for_session("ws1", "dave"))[0]
    path = env.blobs._path(entry.content_digest)
    path.write_bytes("被篡改的指令".encode())
    with pytest.raises(StorageUnavailable):
        await catalog.load("ws1", "dave", entry.name, entry.digest)
