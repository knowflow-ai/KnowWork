"""技能的安装、草稿、发布、启停、删除，以及个人与空间两种范围的权限。"""

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from knowwork_server.skills.errors import PackageInvalid, SkillConflict, SkillForbidden, SkillNotFound
from knowwork_server.skills.models import Skill, SkillRelease
from knowwork_server.skills.service import StorageUnavailable

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio

EXTRACT = {"allowed": "extract_records kw_now", "completion": "extract_records"}


async def test_install_personal_is_published_at_once_and_private(env):
    svc = env.skills.service
    view = await svc.install(actor("carol"), "personal", skill_zip(version="1.0.0"))
    assert (view.status, view.scope, view.owner_id, view.draft_revision) == ("published", "personal", "carol", 1)
    assert (view.published_version, view.has_unpublished_changes, view.enabled) == ("1.0.0", False, True)
    assert view.draft["files"][1]["path"] == "references/guide.md"
    assert [v.name for v in await svc.list(actor("carol"), "personal")] == ["contract-extract"]
    assert await svc.list(actor("dave"), "personal") == ()
    with pytest.raises(SkillNotFound):
        await svc.get(actor("dave"), view.id)
    with pytest.raises(SkillNotFound):
        await svc.get(actor("carol"), view.id, scope="workspace")


async def test_install_rules(env):
    svc = env.skills.service
    with pytest.raises(SkillForbidden):
        await svc.install(actor("carol"), "workspace", skill_zip())
    with pytest.raises(SkillForbidden):
        await svc.install(actor("mallory"), "personal", skill_zip())
    with pytest.raises(PackageInvalid):
        await svc.install(actor("bob"), "global", skill_zip())
    await svc.install(actor("bob"), "workspace", skill_zip())
    with pytest.raises(SkillConflict) as caught:
        await svc.install(actor("bob"), "workspace", skill_zip())
    assert caught.value.code == "skill_exists"
    with pytest.raises(PackageInvalid) as caught:
        await svc.install(actor("bob"), "workspace", skill_zip("other", allowed="extract_records"))
    assert caught.value.code == "completion_tools_required"
    assert await env.blobs.exists((await svc.list(actor("bob"), "workspace"))[0].draft_digest)


async def test_workspace_visibility_follows_rbac(env):
    svc = env.skills.service
    view = await svc.install(actor("bob"), "workspace", skill_zip())
    assert [v.id for v in await svc.list(actor("dave"), "workspace")] == [view.id], "新建的默认全体成员可用"
    await env.access.set_users("alice", "ws1", f"skill:{view.id}", [])  # 新建的默认全体成员，先收窄才测得出授权
    assert await svc.list(actor("dave"), "workspace") == ()
    with pytest.raises(SkillNotFound):
        await svc.get(actor("dave"), view.id)
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{view.id}", "use")
    assert [v.id for v in await svc.list(actor("dave"), "workspace")] == [view.id]
    with pytest.raises(SkillForbidden):
        await svc.set_enabled(actor("dave"), view.id, False)
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{view.id}", "edit")
    assert (await svc.set_enabled(actor("dave"), view.id, False)).enabled is False
    with pytest.raises(SkillForbidden):
        await svc.publish(actor("dave"), view.id)


async def test_draft_update_and_publish_state_machine(env):
    svc = env.skills.service
    view = await svc.install(actor("bob"), "workspace", skill_zip(version="1.0.0"))
    release = await svc.publish(actor("bob"), view.id, expected_revision=1)
    assert release.version == "1.0.0"
    detail, releases = await svc.get(actor("bob"), view.id)
    assert (detail.status, detail.published_version, detail.has_unpublished_changes) == ("published", "1.0.0", False)
    assert [r.version for r in releases] == ["1.0.0"]
    with pytest.raises(SkillConflict) as caught:
        await svc.publish(actor("bob"), view.id)
    assert caught.value.code == "nothing_to_publish"

    with pytest.raises(SkillConflict) as caught:
        await svc.replace_draft(actor("bob"), view.id, skill_zip(body="新版"), expected_revision=5)
    assert caught.value.code == "revision_conflict"
    with pytest.raises(PackageInvalid) as caught:
        await svc.replace_draft(actor("bob"), view.id, skill_zip("renamed"))
    assert caught.value.code == "name_mismatch"
    updated = await svc.replace_draft(actor("bob"), view.id, skill_zip(version="1.0.0", body="新版"),
                                      expected_revision=1)
    assert (updated.draft_revision, updated.has_unpublished_changes, updated.status) == (2, True, "published")
    with pytest.raises(SkillConflict) as caught:
        await svc.publish(actor("bob"), view.id)
    assert caught.value.code == "skill_version_exists"
    second = await svc.publish(actor("bob"), view.id, version="1.1.0")
    assert second.archive_digest == updated.draft_digest
    await svc.replace_draft(actor("bob"), view.id, skill_zip(body="再改"))
    with pytest.raises(PackageInvalid):
        await svc.publish(actor("bob"), view.id, version="bad version")


async def test_version_falls_back_to_digest_prefix(env):
    svc = env.skills.service
    view = await svc.install(actor("bob"), "workspace", skill_zip())
    release = await svc.publish(actor("bob"), view.id)
    assert release.version == view.draft_digest[:12]
    reverted = await svc.replace_draft(actor("bob"), view.id, skill_zip())
    assert reverted.has_unpublished_changes is False


async def test_personal_upload_publishes_and_reuses_versions(env):
    svc = env.skills.service
    view = await svc.install(actor("carol"), "personal", skill_zip(version="1.0.0", body="一"))
    same_label = await svc.replace_draft(actor("carol"), view.id, skill_zip(version="1.0.0", body="二"),
                                         scope="personal")
    assert same_label.status == "published" and same_label.published_version == same_label.draft_digest[:12], \
        "包里的版本号已经发布过、内容又变了，就按摘要编号"
    back = await svc.replace_draft(actor("carol"), view.id, skill_zip(version="1.0.0", body="一"), scope="personal")
    assert (back.published_version, back.has_unpublished_changes) == ("1.0.0", False), "换回旧包就用回旧版本"
    _, releases = await svc.get(actor("carol"), view.id)
    assert sorted(r.version for r in releases) == sorted(["1.0.0", same_label.draft_digest[:12]])


async def test_one_tool_one_skill_across_scopes(env):
    svc = env.skills.service
    await svc.install(actor("bob"), "workspace", skill_zip("contract-extract", **EXTRACT))
    with pytest.raises(SkillConflict) as caught:
        await svc.install(actor("carol"), "personal", skill_zip("my-extract", **EXTRACT))
    assert caught.value.code == "tool_already_claimed"
    other = await svc.install(actor("bob"), "workspace", skill_zip("report", allowed="kw_now"))
    with pytest.raises(SkillConflict):
        await svc.replace_draft(actor("bob"), other.id, skill_zip("report", **EXTRACT))


async def test_delete_removes_releases(env):
    svc = env.skills.service
    view = await svc.install(actor("bob"), "workspace", skill_zip())
    await svc.publish(actor("bob"), view.id)
    await env.access.grant("alice", "ws1", "user:carol", f"skill:{view.id}", "edit")
    with pytest.raises(SkillForbidden):
        await svc.delete(actor("carol"), view.id)
    await svc.delete(actor("bob"), view.id)
    with pytest.raises(SkillNotFound):
        await svc.get(actor("bob"), view.id)
    assert await env.access.list_grants("alice", "ws1", resource=f"skill:{view.id}") == (), "授权随技能一起删"
    assert not env.access.check("carol", "ws1", f"skill:{view.id}", "use")
    async with env.sessionmaker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', true)"))
        assert (await session.scalars(select(SkillRelease))).all() == []


async def test_storage_failure_is_reported_without_details(env, monkeypatch):
    from knowwork_server.skills.errors import BlobIntegrityError

    async def broken(_data):
        raise BlobIntegrityError("disk full at /secret/path")

    monkeypatch.setattr(env.blobs, "put", broken)
    with pytest.raises(StorageUnavailable) as caught:
        await env.skills.service.install(actor("carol"), "personal", skill_zip())
    assert "/secret" not in caught.value.message


async def test_row_level_security_isolates_workspaces(env):
    await env.access.create_workspace(workspace_id="ws2", name="空间二", owner_user_id="alice")
    await env.skills.service.install(actor("alice", "ws2"), "workspace", skill_zip("ws2-only"))
    await env.skills.service.install(actor("alice"), "workspace", skill_zip("ws1-only"))
    assert [v.name for v in await env.skills.service.list(actor("alice"), "workspace")] == ["ws1-only"]
    async with env.sessionmaker() as session, session.begin():
        assert (await session.scalars(select(Skill))).all() == []  # 未设置空间变量：一行都看不到
    admin = create_async_engine(env.admin_url)
    async with admin.connect() as conn:
        assert await conn.scalar(text("SELECT count(*) FROM skill")) == 2
    await admin.dispose()


async def test_concurrent_installs_of_the_same_name_yield_one_conflict(env):
    import asyncio

    results = await asyncio.gather(*(env.skills.service.install(actor("carol"), "personal", skill_zip())
                                     for _ in range(3)), return_exceptions=True)
    assert sum(not isinstance(r, Exception) for r in results) == 1
    assert all(isinstance(r, SkillConflict) and r.code == "skill_exists" for r in results if isinstance(r, Exception))
