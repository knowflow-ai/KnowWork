"""收集编译事实：只用各模块已有的按调用者过滤入口；取不到就失败关闭；空间角色来自成员表。"""

import uuid

import pytest

from knowwork_server.access.models import Workspace, WorkspaceMember
from knowwork_server.profile.clearance import AuthorizedToolRegistry, ToolClearance
from knowwork_server.profile.errors import ProfileUnavailable
from knowwork_server.profile.facts import ProfileCompiler, ProfileSources, knowledge_from_admin, member_role_lookup
from knowwork_server.profile.snapshot import TurnSelection

from .profile_support import WS, Grants, Model, registry, skill

pytestmark = pytest.mark.anyio


class Skills:
    def __init__(self, entries=(), error=None):
        self.entries, self.error, self.calls = entries, error, 0

    async def for_session(self, workspace_id, user_id):
        self.calls += 1
        if self.error:
            raise self.error
        return self.entries


class Models:
    def __init__(self, capabilities=frozenset({"chat"})):
        self.capabilities = capabilities

    async def usable_models(self, user_id, workspace_id):
        return (Model("m1", "default", "默认"),)

    async def default_capabilities(self, workspace_id):
        return self.capabilities


class Summary:
    def __init__(self, id_, enabled=True, bases=("*",)):
        self.id, self.name, self.kind, self.enabled, self.bases = id_, f"源{id_}", "knowflow", enabled, bases


class Admin:
    def __init__(self):
        self.asked = []

    async def queryable(self, actor, workspace_id):
        self.asked.append((actor, workspace_id))
        return [Summary("k1", bases=("ds1",)), Summary("k2", enabled=False)]


async def fixed_role(user_id, workspace_id):
    return "editor"


def compiler(grants=None, skills=None, models=None, host_denied=()):
    grants = grants or Grants({"ed": "editor"})
    clearance = ToolClearance(grants, builtin=frozenset({"kw_now"}))
    reg = AuthorizedToolRegistry(registry(), clearance)
    admin = Admin()
    sources = ProfileSources(all_tools=reg.all_tools, clearance=clearance, skills=skills or Skills((skill("a"),)),
                             models=models or Models(), knowledge=knowledge_from_admin(admin), role_of=fixed_role,
                             host_denied=host_denied)
    return ProfileCompiler(sources), admin


async def test_compiles_from_sources():
    comp, admin = compiler()
    profile = await comp.compile(workspace_id=WS, user_id="ed", session_id="c_1",
                                 selection=TurnSelection(skills=("a",)))
    assert profile.role == "editor" and profile.member
    assert [t.name for t in profile.tools] == ["kw_now"]
    assert profile.denied_tools == ("crm_write", "erp_read", "read_image"), "默认模型不能看图，看图工具一并收掉"
    assert [(s.name, s.selected) for s in profile.skills] == [("a", True)]
    assert [m.alias for m in profile.models] == ["default"]
    assert [(k.id, k.bases) for k in profile.knowledge] == [("k1", ("ds1",))]
    assert admin.asked == [("ed", WS)], "知识库按调用者本人的授权过滤"


@pytest.mark.parametrize(("capabilities", "denied"), [
    (frozenset({"chat"}), True),
    (None, True),  # 没配模型或默认模型已停用：不知道能不能看图，按不能处理
    (frozenset({"chat", "vision"}), False),
])
async def test_image_tool_follows_default_model_vision(capabilities, denied):
    """默认模型没勾「看图」就收掉 read_image，免得模型先转图片再去调一个必然失败的工具。"""
    comp, _ = compiler(models=Models(capabilities), host_denied=("bash",))
    profile = await comp.compile(workspace_id=WS, user_id="ed", session_id="c_1")
    assert ("read_image" in profile.denied_tools) is denied
    assert "bash" in profile.denied_tools, "部署层面收掉的工具照旧"


async def test_non_member_skips_other_sources():
    skills = Skills((skill("a"),))
    comp, admin = compiler(skills=skills)
    profile = await comp.compile(workspace_id=WS, user_id="stranger", session_id="c_1")
    assert not profile.member and profile.skills == ()
    assert skills.calls == 0 and admin.asked == []


async def test_source_failure_is_profile_unavailable():
    comp, _ = compiler(skills=Skills(error=RuntimeError("技能库连不上：password=x")))
    with pytest.raises(ProfileUnavailable) as raised:
        await comp.compile(workspace_id=WS, user_id="ed", session_id="c_1")
    assert "password" not in str(raised.value)


async def test_member_role_lookup(sessionmaker):
    ws = f"w{uuid.uuid4().hex[:8]}"
    async with sessionmaker() as session:
        session.add(Workspace(id=ws, name="测试"))
        await session.flush()
        session.add(WorkspaceMember(workspace_id=ws, user_id="ed", role="editor"))
        await session.commit()
    role_of = member_role_lookup(sessionmaker)
    assert await role_of("ed", ws) == "editor"
    assert await role_of("nobody", ws) is None
