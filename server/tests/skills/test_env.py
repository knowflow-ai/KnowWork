"""技能的环境变量：谁管这一层谁填，值只写不读；会话里加载过的技能跑命令时带上，同名时上层优先；复制到上一层时不带。"""

import io
import zipfile

import pytest
from sqlalchemy import select, text

from knowwork_server.skills.errors import PackageInvalid, SkillForbidden, SkillNotFound
from knowwork_server.skills.models import Skill

from .conftest import actor

pytestmark = pytest.mark.anyio


def env_zip(name: str = "report-sync", *, env: str = "REPORT_API_KEY required 报表系统的密钥",
            body: str = "运行 sync.sh") -> bytes:
    names = " ".join(line.split()[0] for line in env.splitlines())
    md = f"---\nname: {name}\ndescription: 同步报表\nenv:\n" + "".join(f"  {line}\n" for line in env.splitlines())
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, data in (("SKILL.md", md + f"---\n\n{body}\n"), ("sync.sh", f"echo {names}")):
            archive.writestr(zipfile.ZipInfo(path, date_time=(2026, 1, 1, 0, 0, 0)), data)  # 固定时间戳，摘要稳定
    return buffer.getvalue()


async def shared(env, **kwargs):
    svc = env.skills.service
    view = await svc.install(actor("bob"), "workspace", env_zip(**kwargs))
    await svc.publish(actor("bob"), view.id)
    await env.access.set_users("alice", "ws1", f"skill:{view.id}", [])  # 新建的默认全体成员，先收窄才测得出授权
    await env.access.grant("alice", "ws1", "user:carol", f"skill:{view.id}", "use")
    return view


async def test_admin_fills_values_which_are_never_read_back(env):
    svc = env.skills.service
    view = await shared(env)
    filled = await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": "sk-live-123456"})
    assert filled.env_filled == ("REPORT_API_KEY",)
    assert "sk-live" not in str(filled.to_json()), "接口不能把值吐回去"
    with pytest.raises(SkillForbidden):  # 能用不等于能填
        await svc.set_env(actor("carol"), view.id, {"REPORT_API_KEY": "x"})
    with pytest.raises(PackageInvalid) as caught:
        await svc.set_env(actor("bob"), view.id, {"OTHER": "x"})
    assert caught.value.code == "env_undeclared"
    with pytest.raises(PackageInvalid):
        await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": "x" * 5000})
    cleared = await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": None})
    assert cleared.env_filled == ()
    async with env.sessionmaker() as session, session.begin():
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', true)"))
        row = await session.scalar(select(Skill).where(Skill.id == view.id))
        assert row.env_ciphertext is None


async def test_loaded_skills_get_their_values_when_running_commands(env):
    svc, environment = env.skills.service, env.skills.environment
    view = await shared(env)
    await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": "sk-live-123456"})
    assert await environment.for_exec("ws1", "carol", ["report-sync"]) == {"REPORT_API_KEY": "sk-live-123456"}
    assert await environment.for_exec("ws1", "carol", ["other"]) == {}, "没加载的技能不带"
    assert await environment.for_exec("ws1", "dave", ["report-sync"]) == {}, "清单里没有（没授权）的不认"
    await svc.set_enabled(actor("bob"), view.id, False)
    assert await environment.for_exec("ws1", "carol", ["report-sync"]) == {}


async def test_values_stay_with_the_skill_across_versions_and_are_bound_to_the_row(env):
    svc, environment = env.skills.service, env.skills.environment
    view = await shared(env)
    await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": "sk-live-123456"})
    await svc.replace_draft(actor("bob"), view.id, env_zip(body="新做法"))
    await svc.publish(actor("bob"), view.id, version="2")
    assert await environment.for_exec("ws1", "carol", ["report-sync"]) == {"REPORT_API_KEY": "sk-live-123456"}
    other = await shared(env, name="report-copy")
    async with env.sessionmaker() as session, session.begin():  # 把密文挪到另一个技能上：解不开，当作没填
        await session.execute(text("SELECT set_config('app.workspace_id', 'ws1', true)"))
        source = await session.scalar(select(Skill).where(Skill.id == view.id))
        target = await session.scalar(select(Skill).where(Skill.id == other.id))
        target.env_ciphertext, target.env_filled = source.env_ciphertext, source.env_filled
    assert await environment.for_exec("ws1", "carol", ["report-copy"]) == {}


async def test_personal_values_only_for_the_owner_and_not_copied_upward(env):
    svc, subs, environment = env.skills.service, env.skills.submissions, env.skills.environment
    mine = await svc.install(actor("carol"), "personal", env_zip(name="my-sync"))
    with pytest.raises(SkillNotFound):
        await svc.set_env(actor("dave"), mine.id, {"REPORT_API_KEY": "x"}, scope="personal")
    await svc.set_env(actor("carol"), mine.id, {"REPORT_API_KEY": "carol-own-key"}, scope="personal")
    assert await environment.for_exec("ws1", "carol", ["my-sync"]) == {"REPORT_API_KEY": "carol-own-key"}
    copy = await subs.approve(actor("bob"), (await subs.submit(actor("carol"), mine.id)).id, publish=True)
    assert copy.env_filled == (), "个人填的可能是作者自己的凭据，进空间时不带，管理员重填"


async def test_upper_layer_wins_when_two_loaded_skills_declare_the_same_name(env):
    svc, environment = env.skills.service, env.skills.environment
    view = await shared(env)
    await svc.set_env(actor("bob"), view.id, {"REPORT_API_KEY": "workspace-key"})
    mine = await svc.install(actor("carol"), "personal", env_zip(name="my-sync"))
    await svc.set_env(actor("carol"), mine.id, {"REPORT_API_KEY": "personal-key"}, scope="personal")
    assert await environment.for_exec("ws1", "carol", ["my-sync", "report-sync"]) == {"REPORT_API_KEY": "workspace-key"}
