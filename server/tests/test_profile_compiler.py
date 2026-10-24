"""运行配置编译的纯函数：同输入同版本号、授权变化版本号变化、单个技能失败只跳过。"""

import hashlib
import logging
import random

from knowwork_server.profile.compiler import KnowledgeEntry, ProfileFacts, compile_profile
from knowwork_server.profile.snapshot import PinnedScope, TurnSelection, canonical_json

from .profile_support import BUILTIN, CRM, ERP, skill

TOOLS = (BUILTIN.spec, CRM.spec, ERP.spec)


def facts(**overrides) -> ProfileFacts:
    base = {"workspace_id": "default", "user_id": "u1", "role": "editor", "member": True, "tools": TOOLS,
            "permitted_tools": frozenset({"kw_now", "erp_read"}), "builtin_tools": frozenset({"kw_now"}),
            "skills": (skill("contract-review"), skill("weekly-report", version="2.0.0", digest="b" * 64)),
            "knowledge": (KnowledgeEntry("k1", "制度库", "knowflow"),
                          KnowledgeEntry("k2", "合同库", "knowflow", bases=("ds1", "ds2")))}
    return ProfileFacts(**{**base, **overrides})


def test_same_input_same_version_regardless_of_order():
    first = compile_profile(facts())
    shuffled = facts(tools=tuple(random.Random(1).sample(TOOLS, len(TOOLS))),
                     skills=tuple(reversed(facts().skills)),
                     knowledge=tuple(reversed(facts().knowledge)))
    assert compile_profile(shuffled).version == first.version
    assert compile_profile(facts()) == first


def test_version_is_sha256_of_canonical_json():
    profile = compile_profile(facts())
    assert profile.version == hashlib.sha256(canonical_json(profile.payload()).encode()).hexdigest()
    assert len(profile.version) == 64


def test_grant_change_changes_version_and_denied_tools():
    before = compile_profile(facts())
    after = compile_profile(facts(permitted_tools=frozenset({"kw_now", "erp_read", "crm_write"})))
    assert before.version != after.version
    assert before.denied_tools == ("crm_write",)
    assert after.denied_tools == ()
    assert [(t.name, t.risk, t.builtin) for t in after.tools] == [
        ("crm_write", "write", False), ("erp_read", "read", False), ("kw_now", "read", True)]
    assert before.policy().deny == ("crm_write",)


def test_role_change_changes_version():
    assert compile_profile(facts()).version != compile_profile(facts(role="admin")).version


def test_broken_skill_is_skipped_and_logged(caplog):
    broken = skill("broken", digest="not-a-digest")
    caplog.set_level(logging.WARNING)
    profile = compile_profile(facts(skills=(*facts().skills, broken, object())))
    assert [s.name for s in profile.skills] == ["contract-review", "weekly-report"]
    assert "broken" in caplog.text
    assert [(s.version, s.summary) for s in profile.skills][1] == ("2.0.0", "技能说明")


def test_duplicate_skill_name_keeps_first():
    profile = compile_profile(facts(skills=(skill("x"), skill("x", version="9.9.9"))))
    assert [(s.name, s.version) for s in profile.skills] == [("x", "1.0.0")]


def test_selected_skills_are_marked_and_unknown_ignored(caplog):
    caplog.set_level(logging.WARNING)
    profile = compile_profile(facts(selection=TurnSelection(skills=("weekly-report", "ghost"))))
    assert {s.name: s.selected for s in profile.skills} == {"contract-review": False, "weekly-report": True}
    assert "ghost" in caplog.text
    assert profile.version != compile_profile(facts()).version


def test_knowledge_selection_narrows_and_is_bound():
    unbound = compile_profile(facts())
    assert [(k.id, k.bases) for k in unbound.knowledge] == [("k1", ("*",)), ("k2", ("ds1", "ds2"))]
    assert unbound.bound_knowledge is None
    bound = compile_profile(facts(selection=TurnSelection(knowledge_bases=("k2.ds2", "k9.x", "k2.ds2", "k2.ds7"))))
    assert [(k.id, k.bases) for k in bound.knowledge] == [("k2", ("ds2",))]
    assert bound.bound_knowledge == ("k2.ds2", "k2.ds7", "k9.x"), "选中但查不了的也要记下，由知识工具说明原因"
    whole = compile_profile(facts(selection=TurnSelection(knowledge_bases=("k1.a", "k2.*"))))
    assert [(k.id, k.bases) for k in whole.knowledge] == [("k1", ("a",)), ("k2", ("ds1", "ds2"))]
    disabled = compile_profile(facts(knowledge=(KnowledgeEntry("k1", "制度库", "knowflow", enabled=False),)))
    assert disabled.knowledge == ()


def test_pins_narrow_to_their_bases_and_whole_bases_win():
    """@ 的文件与标签按库合起来；点名的库并进本轮可查的库（目录也只列它们）；同一个库也 @ 了整库的，整库优先。"""
    picked = TurnSelection(pins=(PinnedScope(base="k2.ds1", documents=("ds1/b", "ds1/a")),
                                 PinnedScope(base="k2.ds1", tags=("合同",)),
                                 PinnedScope(base="k2.ds2", documents=("ds2/c",)),
                                 PinnedScope(base="k1.x")))
    profile = compile_profile(facts(selection=picked))
    assert profile.bound_knowledge == ("k2.ds1", "k2.ds2"), "只 @ 了文件时，只查这些文件所在的库"
    assert [(k.id, k.bases) for k in profile.knowledge] == [("k2", ("ds1", "ds2"))]
    assert profile.pins == (PinnedScope(base="k2.ds1", documents=("ds1/a", "ds1/b"), tags=("合同",)),
                            PinnedScope(base="k2.ds2", documents=("ds2/c",))), "什么都没点到的去掉"
    whole = compile_profile(facts(selection=TurnSelection(knowledge_bases=("k2.ds2",), pins=picked.pins)))
    assert whole.bound_knowledge == ("k2.ds1", "k2.ds2")
    assert [p.base for p in whole.pins] == ["k2.ds1"], "@ 了整个 ds2，ds2 里的点名不再收窄"
    everything = compile_profile(facts(selection=TurnSelection(knowledge_bases=("k2.*",), pins=picked.pins)))
    assert everything.pins == ()
    assert compile_profile(facts(selection=picked)).version != compile_profile(facts()).version


def test_snapshots_from_before_knowledge_bases_still_load():
    """2026-10-20 以前的快照：知识源没有 bases，绑定范围写的是知识源 id。读得出来，版本号不变。"""
    payload = compile_profile(facts()).payload()
    old = {**payload, "knowledge": [{"id": "k1", "name": "制度库", "kind": "knowflow"}], "bound_knowledge": ["k1"]}
    loaded = type(compile_profile(facts())).model_validate(old)
    assert loaded.knowledge[0].bases == () and loaded.bound_knowledge == ("k1",)


def test_non_member_gets_nothing_and_every_tool_denied():
    profile = compile_profile(facts(member=False))
    assert profile.member is False and profile.role is None
    assert profile.tools == () and profile.skills == () and profile.knowledge == ()
    assert profile.denied_tools == ("crm_write", "erp_read", "kw_now")


def test_snapshot_round_trips_and_is_immutable():
    profile = compile_profile(facts())
    again = type(profile).model_validate(profile.payload())
    assert again == profile and again.version == profile.version
    try:
        profile.role = "owner"  # type: ignore[misc]
    except Exception:  # noqa: BLE001  pydantic frozen 抛 ValidationError
        return
    raise AssertionError("快照应不可变")


def test_package_exports_lazily():
    import knowwork_server.profile as package

    assert package.compile_profile is compile_profile
    try:
        package.no_such_name  # noqa: B018
    except AttributeError:
        return
    raise AssertionError("未知名字应抛 AttributeError")
