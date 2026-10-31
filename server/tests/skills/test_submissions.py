"""个人技能提交到空间与审核：提交的是发布版本的快照，管理员通过后复制成空间技能（以后的提交是它的新版本）。"""

import pytest
from sqlalchemy import select, text

from knowwork_server.skills.errors import PackageInvalid, SkillConflict, SkillForbidden, SkillNotFound
from knowwork_server.skills.models import Skill, SkillSubmission

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio

EXTRACT = {"allowed": "extract_records kw_now", "completion": "extract_records"}


async def mine(env, user="dave", name="my-notes", **kwargs):
    return await env.skills.service.install(actor(user), "personal", skill_zip(name, **kwargs))


async def test_submit_snapshots_the_published_release(env):
    subs = env.skills.submissions
    view = await mine(env, version="1.0.0", body="第一版")
    sub = await subs.submit(actor("dave"), view.id, note="部门都用得上")
    assert (sub.status, sub.version, sub.note, sub.submitted_by) == ("pending", "1.0.0", "部门都用得上", "dave")
    assert sub.archive_digest == view.draft_digest
    # 提交之后作者再改，不影响这次提交
    await env.skills.service.replace_draft(actor("dave"), view.id, skill_zip("my-notes", version="1.1.0"),
                                           scope="personal")
    [pending] = await subs.pending(actor("bob"))
    assert (pending.id, pending.version) == (sub.id, "1.0.0")


async def test_submit_rules(env):
    subs = env.skills.submissions
    view = await mine(env)
    with pytest.raises(SkillNotFound):
        await subs.submit(actor("carol"), view.id)  # 别人的个人技能当不存在
    ws = await env.skills.service.install(actor("bob"), "workspace", skill_zip("summary"))
    with pytest.raises(SkillNotFound):
        await subs.submit(actor("bob"), ws.id)  # 只能提交个人技能
    clash = await mine(env, name="summary")
    with pytest.raises(SkillConflict) as caught:
        await subs.submit(actor("dave"), clash.id)
    assert caught.value.code == "skill_exists"
    with pytest.raises(PackageInvalid):
        await subs.submit(actor("dave"), view.id, note="长" * 501)


async def test_resubmit_replaces_pending_and_withdraw(env):
    subs, svc = env.skills.submissions, env.skills.service
    view = await mine(env, version="1.0.0")
    first = await subs.submit(actor("dave"), view.id)
    again = await subs.submit(actor("dave"), view.id, note="补一句")
    assert again.id == first.id and again.note == "补一句", "同一版再提交只改附言"
    await svc.replace_draft(actor("dave"), view.id, skill_zip("my-notes", version="1.1.0"), scope="personal")
    newer = await subs.submit(actor("dave"), view.id)
    assert newer.id != first.id and newer.version == "1.1.0"
    assert [p.id for p in await subs.pending(actor("bob"))] == [newer.id], "新提交顶掉旧的待审"
    await subs.withdraw(actor("dave"), view.id)
    assert await subs.pending(actor("bob")) == ()
    assert (await subs.latest(actor("dave")))[view.id].status == "withdrawn"
    with pytest.raises(SkillConflict):
        await subs.withdraw(actor("dave"), view.id)


async def test_approve_copies_into_workspace_then_updates_it(env):
    subs, svc = env.skills.submissions, env.skills.service
    view = await mine(env, version="1.0.0", **EXTRACT)
    sub = await subs.submit(actor("dave"), view.id)
    with pytest.raises(SkillForbidden):
        await subs.approve(actor("carol"), sub.id)
    copy = await subs.approve(actor("bob"), sub.id)
    assert (copy.scope, copy.name, copy.status, copy.owner_id) == ("workspace", "my-notes", "draft", "bob")
    assert copy.draft_digest == view.draft_digest and copy.draft["unlocks"] == ["extract_records"]
    assert (await subs.latest(actor("dave")))[view.id].status == "approved"
    with pytest.raises(SkillConflict):
        await subs.approve(actor("bob"), sub.id)
    # 第一次进空间同新建的空间技能，默认全体成员可用；发布后别人的会话里就有
    await svc.publish(actor("bob"), copy.id)
    assert [e.name for e in await env.skills.delivery.catalog.for_session("ws1", "carol")] == ["my-notes"]

    await svc.replace_draft(actor("dave"), view.id, skill_zip("my-notes", version="2.0.0", body="改过", **EXTRACT),
                            scope="personal")
    second = await subs.submit(actor("dave"), view.id)
    detail = await subs.detail(actor("bob"), second.id)
    assert detail.content.strip() == "改过" and detail.target is not None
    assert detail.target.skill_id == copy.id and detail.target.content.strip() == "按步骤抽取合同字段。"
    updated = await subs.approve(actor("bob"), second.id, publish=True)
    assert updated.id == copy.id, "以后的提交是同一个空间技能的新版本"
    assert (updated.status, updated.published_version, updated.has_unpublished_changes) == ("published", "2.0.0",
                                                                                          False)
    assert (await subs.latest(actor("dave")))[view.id].target_skill_id == copy.id


async def test_identical_version_already_in_workspace_is_not_resubmitted(env):
    subs = env.skills.submissions
    view = await mine(env)
    await subs.approve(actor("bob"), (await subs.submit(actor("dave"), view.id)).id)
    with pytest.raises(SkillConflict) as caught:
        await subs.submit(actor("dave"), view.id)
    assert caught.value.code == "already_in_workspace"


async def test_approve_rechecks_name_taken_meanwhile(env):
    subs = env.skills.submissions
    view = await mine(env)
    sub = await subs.submit(actor("dave"), view.id)
    await env.skills.service.install(actor("bob"), "workspace", skill_zip("my-notes"))
    with pytest.raises(SkillConflict) as caught:
        await subs.approve(actor("bob"), sub.id)
    assert caught.value.code == "skill_exists"


async def test_reject_needs_reason_and_author_sees_it(env):
    subs = env.skills.submissions
    view = await mine(env)
    sub = await subs.submit(actor("dave"), view.id)
    with pytest.raises(PackageInvalid):
        await subs.reject(actor("bob"), sub.id, "  ")
    rejected = await subs.reject(actor("bob"), sub.id, "和「合同初审」重复了")
    assert (rejected.status, rejected.review_note, rejected.reviewed_by) == ("rejected", "和「合同初审」重复了", "bob")
    assert (await subs.latest(actor("dave")))[view.id].review_note == "和「合同初审」重复了"
    assert await subs.latest(actor("carol")) == {}


async def test_archive_download_is_the_submitted_zip(env):
    subs = env.skills.submissions
    data = skill_zip("my-notes", version="1.0.0")
    view = await env.skills.service.install(actor("dave"), "personal", data)
    sub = await subs.submit(actor("dave"), view.id)
    name, body = await subs.archive(actor("bob"), sub.id)
    assert (name, body) == ("my-notes-1.0.0.zip", data)
    with pytest.raises(SkillForbidden):
        await subs.archive(actor("dave"), sub.id)


async def test_author_deleting_personal_skill_drops_pending_but_keeps_workspace_copy(env):
    subs, svc = env.skills.submissions, env.skills.service
    view = await mine(env)
    copy = await subs.approve(actor("bob"), (await subs.submit(actor("dave"), view.id)).id)
    await svc.replace_draft(actor("dave"), view.id, skill_zip("my-notes", body="新"), scope="personal")
    await subs.submit(actor("dave"), view.id)
    await svc.delete(actor("dave"), view.id, scope="personal")
    assert await subs.pending(actor("bob")) == ()
    assert (await svc.get(actor("bob"), copy.id))[0].name == "my-notes"


async def test_member_removed_deletes_personal_skills_and_submissions(env):
    subs = env.skills.submissions
    view = await mine(env)
    await subs.submit(actor("dave"), view.id)
    await mine(env, user="carol", name="carol-notes")
    env.access.on_member_removed(env.skills.service.forget_member)
    await env.access.remove_member("alice", "ws1", "dave")
    async with env.sessionmaker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', true)"))
        assert [row.name for row in (await session.scalars(select(Skill))).all()] == ["carol-notes"]
        assert (await session.scalars(select(SkillSubmission))).all() == []
