"""扩展给一段对话的范围收窄（profile/compiler.py 的 ProfileNarrowing）：只会更少，和本人的权限、这一轮的选择取交集。

- 知识库：只查范围里的；这一轮又选了的取交集，选了范围外的等于没选到；「<知识源>.*」两边都认；点名的文件、标签
  在范围外的库里的去掉；
- 技能：只给范围里的，本人没有的给不了；
- MCP 服务：范围外服务的工具进 denied_tools，本人没授权的照样没有；不是 MCP 的工具不受影响；
- 不收窄（None）时和没有范围一样。
"""

from knowwork_server.profile.compiler import KnowledgeEntry, ProfileFacts, compile_profile
from knowwork_server.profile.snapshot import NO_SELECTION, PinnedScope, ProfileNarrowing, TurnSelection
from knowwork_server.tools.registry import ToolSpec

from .profile_support import BUILTIN, ERP, skill

SCHEMA = {"type": "object", "properties": {}}
MAIL = ToolSpec("mcp__mail__send", "发邮件", SCHEMA, "write")
WIKI = ToolSpec("mcp__wiki__search", "查百科", SCHEMA, "read")


def facts(narrowing: ProfileNarrowing | None = None, selection: TurnSelection = NO_SELECTION) -> ProfileFacts:
    return ProfileFacts(
        workspace_id="default", user_id="u1", role="editor", member=True,
        tools=(BUILTIN.spec, ERP.spec, MAIL, WIKI),
        permitted_tools=frozenset({"kw_now", "erp_read", "mcp__mail__send", "mcp__wiki__search"}),
        builtin_tools=frozenset({"kw_now"}),
        skills=(skill("contract-review"), skill("weekly-report")),
        knowledge=(KnowledgeEntry("k1", "制度库", "knowflow"), KnowledgeEntry("k2", "合同库", "knowflow", bases=("ds1", "ds2"))),
        selection=selection, narrowing=narrowing)


def bases(profile):
    return {source.id: source.bases for source in profile.knowledge}


def test_no_narrowing_is_the_same_as_before():
    assert compile_profile(facts(ProfileNarrowing())) == compile_profile(facts())


def test_knowledge_is_narrowed_to_the_scope_and_intersected_with_the_turn():
    scoped = compile_profile(facts(ProfileNarrowing(knowledge_bases=("k2.ds1",))))
    assert bases(scoped) == {"k2": ("ds1",)}
    assert scoped.bound_knowledge == ("k2.ds1",)
    picked = compile_profile(facts(ProfileNarrowing(knowledge_bases=("k2.*",)), TurnSelection(knowledge_bases=("k2.ds2", "k1.*"))))
    assert bases(picked) == {"k2": ("ds2",)}, "这一轮选的范围外的库等于没选到"
    widened = compile_profile(facts(ProfileNarrowing(knowledge_bases=("k2.ds1",)), TurnSelection(knowledge_bases=("k2.*",))))
    assert bases(widened) == {"k2": ("ds1",)}, "选了整个知识源也只到范围里的库"
    nothing = compile_profile(facts(ProfileNarrowing(knowledge_bases=("k1.x",)), TurnSelection(knowledge_bases=("k2.ds1",))))
    assert nothing.knowledge == () and nothing.bound_knowledge == ()


def test_pins_outside_the_scope_are_dropped():
    pins = (PinnedScope(base="k2.ds1", documents=("d1",)), PinnedScope(base="k1.kb", documents=("d2",)))
    profile = compile_profile(facts(ProfileNarrowing(knowledge_bases=("k2.ds1",)), TurnSelection(pins=pins)))
    assert [pin.base for pin in profile.pins] == ["k2.ds1"]


def test_skills_are_narrowed_to_the_scope():
    profile = compile_profile(facts(ProfileNarrowing(skills=("weekly-report", "not-mine"))))
    assert [s.name for s in profile.skills] == ["weekly-report"]
    assert compile_profile(facts(ProfileNarrowing(skills=()))).skills == ()


def test_mcp_servers_outside_the_scope_are_denied():
    profile = compile_profile(facts(ProfileNarrowing(mcp_servers=("mail",))))
    assert [t.name for t in profile.tools] == ["erp_read", "kw_now", "mcp__mail__send"]
    assert "mcp__wiki__search" in profile.denied_tools
    unpermitted = facts(ProfileNarrowing(mcp_servers=("mail", "wiki")))
    unpermitted = ProfileFacts(**{**unpermitted.__dict__, "permitted_tools": frozenset({"kw_now", "erp_read"})})
    assert "mcp__mail__send" in compile_profile(unpermitted).denied_tools, "范围给不了本人没授权的"
