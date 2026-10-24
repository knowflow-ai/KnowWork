"""SKILL.md frontmatter 解析与技能门的安装期规则。"""

import pytest

from knowwork_server.skills.claims import check_claims, specialized_tools
from knowwork_server.skills.errors import PackageInvalid, SkillConflict
from knowwork_server.skills.manifest import parse_skill_md

BASIC = frozenset({"skill", "kw_now"})


def skill_md(front: str, body: str = "按步骤抽取合同字段。") -> str:
    return f"---\n{front.strip()}\n---\n\n{body}\n"


def test_minimal_manifest():
    manifest = parse_skill_md(skill_md("name: contract-extract\ndescription: 把合同按字段抽成表"))
    assert manifest.name == "contract-extract"
    assert manifest.description == "把合同按字段抽成表"
    assert manifest.version == ""
    assert manifest.allowed_tools == ()
    assert manifest.completion_tools == ()
    assert manifest.env == ()
    assert manifest.body.startswith("按步骤抽取")
    assert manifest.model_invocable and manifest.user_invocable


def test_tools_accept_space_separated_and_list():
    text = skill_md("""
name: contract-extract
description: 抽取
version: 1.2.0
allowed-tools: extract_records kw_now, xlsx_render
completion-tools:
  - xlsx_render
""")
    manifest = parse_skill_md(text)
    assert manifest.version == "1.2.0"
    assert manifest.allowed_tools == ("extract_records", "kw_now", "xlsx_render")
    assert manifest.completion_tools == ("xlsx_render",)


def test_env_block_three_columns():
    text = skill_md("""
name: crm-sync
description: 同步客户
env:
  CRM_TOKEN required 访问 CRM 的令牌
  CRM_REGION optional 区域，默认华东
  - LEGACY_FLAG 没写标记按可选
""")
    env = parse_skill_md(text).env
    assert [(e.name, e.required, e.description) for e in env] == [
        ("CRM_TOKEN", True, "访问 CRM 的令牌"),
        ("CRM_REGION", False, "区域，默认华东"),
        ("LEGACY_FLAG", False, "没写标记按可选"),
    ]


def test_env_block_followed_by_other_keys():
    text = skill_md("name: crm-sync\nenv:\n  CRM_TOKEN required 令牌\ndescription: 同步客户")
    manifest = parse_skill_md(text)
    assert manifest.description == "同步客户"
    assert manifest.env[0].name == "CRM_TOKEN"


@pytest.mark.parametrize("text,code", [
    ("没有 frontmatter", "missing_frontmatter"),
    ("---\nname: a\n", "invalid_frontmatter"),
    ("---\n- just\n- a list\n---\nbody", "invalid_frontmatter"),
    ("---\nname: [unclosed\n---\nbody", "invalid_frontmatter"),
    (skill_md("description: 缺名字"), "missing_metadata"),
    (skill_md("name: Contract_Extract\ndescription: x"), "invalid_skill_name"),
    (skill_md("name: a--b\ndescription: x"), "invalid_skill_name"),
    (skill_md("name: ok\n"), "missing_metadata"),
    (skill_md("name: ok\ndescription: x", body="  "), "empty_skill_body"),
    (skill_md("name: ok\ndescription: x\nversion: 'has space'"), "invalid_version"),
    (skill_md("name: ok\ndescription: x\nallowed-tools: 3bad"), "invalid_tools"),
    (skill_md("name: ok\ndescription: x\nallowed-tools: {a: 1}"), "invalid_tools"),
    (skill_md("name: ok\ndescription: x\nallowed-tools: a\ncompletion-tools: b"), "completion_not_allowed"),
    (skill_md("name: ok\ndescription: x\nenv: []"), "invalid_env"),
    (skill_md("name: ok\ndescription: x\nenv:\n  lower required x"), "invalid_env"),
    (skill_md("name: ok\ndescription: x\nenv:\n  A required x\n  A optional y"), "invalid_env"),
    (skill_md("name: ok\ndescription: x\nuser-invocable: maybe"), "invalid_invocation_policy"),
    (skill_md("name: ok\na: &a [x, x]\nb: &b [*a, *a]\ndescription: *b"), "invalid_frontmatter"),
    (skill_md("name: [a, b]\ndescription: x"), "invalid_metadata"),
    (skill_md("name: ok\ndescription: {k: v}"), "invalid_metadata"),
])
def test_rejections(text, code):
    with pytest.raises(PackageInvalid) as caught:
        parse_skill_md(text)
    assert caught.value.code == code


def test_invocation_flags():
    manifest = parse_skill_md(skill_md("name: ok\ndescription: x\ndisable-model-invocation: yes\nuser-invocable: false"))
    assert manifest.model_invocable is False
    assert manifest.user_invocable is False


def test_crlf_and_bom():
    text = "﻿---\r\nname: ok\r\ndescription: x\r\n---\r\nbody\r\n"
    assert parse_skill_md(text).name == "ok"


def test_specialized_tools_exclude_basic():
    manifest = parse_skill_md(skill_md("name: ok\ndescription: x\nallowed-tools: kw_now extract_records\n"
                                       "completion-tools: extract_records"))
    assert specialized_tools(manifest, BASIC) == ("extract_records",)


def test_unlocking_specialized_tools_requires_completion_tools():
    manifest = parse_skill_md(skill_md("name: ok\ndescription: x\nallowed-tools: extract_records"))
    with pytest.raises(PackageInvalid) as caught:
        check_claims(manifest, BASIC, {})
    assert caught.value.code == "completion_tools_required"


def test_basic_only_skill_needs_no_completion():
    manifest = parse_skill_md(skill_md("name: ok\ndescription: x\nallowed-tools: kw_now"))
    assert check_claims(manifest, BASIC, {}) == ()


def test_a_tool_is_claimed_by_one_skill_only():
    manifest = parse_skill_md(skill_md("name: second\ndescription: x\nallowed-tools: extract_records\n"
                                       "completion-tools: extract_records"))
    with pytest.raises(SkillConflict) as caught:
        check_claims(manifest, BASIC, {"extract_records": "first"})
    assert caught.value.code == "tool_already_claimed"
    # 同一技能更新自己的草稿不算冲突
    assert check_claims(manifest, BASIC, {"extract_records": "second"}) == ("extract_records",)
