"""workflow.yaml 的解析与校验，以及安装期把它并进技能包校验。"""

import io
import zipfile

import pytest

from knowwork_server.skills.errors import PackageInvalid
from knowwork_server.skills.manifest import parse_skill_md
from knowwork_server.skills.package import validate_skill_zip
from knowwork_server.skills.service import manifest_json
from knowwork_server.skills.workflow_def import load_definition, parse_workflow
from knowwork_server.skills.workflow_refs import (
    ReferenceInvalid,
    ReferenceUnresolved,
    find_refs,
    parse_ref,
    resolve,
)

SKILL_MD = """---
name: contract-review
description: 合同复核
allowed-tools: kw_extract kw_search kw_write
completion-tools: kw_write
---

按流程复核合同。
"""

GOOD = """
version: 1
description: 合同复核
inputs:
  type: object
  properties:
    contract_id: {type: string}
  required: [contract_id]
steps:
  - key: extract
    type: tool
    tool: kw_extract
    args: {id: "${inputs.contract_id}"}
  - key: summarize
    type: agent
    prompt: "复核合同 ${inputs.contract_id}"
    tools: [kw_search]
    input: "${steps.extract.output}"
    output_schema: {type: object, properties: {summary: {type: string}}, required: [summary]}
  - key: confirm
    type: approval
    title: 确认
    summary: "${steps.summarize.output.summary}"
  - key: save
    type: tool
    tool: kw_write
    args: {text: "${steps.summarize.output.summary}"}
output: "${steps.save.output}"
"""


def manifest():
    return parse_skill_md(SKILL_MD)


def test_parse_good_workflow():
    definition = parse_workflow(GOOD, manifest())
    assert [s.key for s in definition.steps] == ["extract", "summarize", "confirm", "save"]
    assert definition.steps[1].max_retries == 2 and definition.steps[1].timeout_seconds == 300
    assert load_definition(definition.to_json()) == definition
    assert len(definition.digest) == 64
    assert definition.step("confirm").title == "确认"


@pytest.mark.parametrize(("text", "needle"), [
    (GOOD.replace("${steps.extract.output}", "${steps.save.output}"), "不在它之前"),
    (GOOD.replace("${inputs.contract_id}\"}", "${inputs.missing}\"}"), "没有声明的入参"),
    (GOOD.replace("tools: [kw_search]", "tools: [kw_search, kw_search]"), "重复"),
    (GOOD.replace("tools: [kw_search]", "tools: ['9bad']"), "工具名"),
    (GOOD.replace("output_schema: {type: object,", "output_schema: {type: 12,"), "JSON Schema"),
    (GOOD.replace("version: 1", "version: 2"), "version"),
    (GOOD.replace("    title: 确认", "    title: 确认\n    color: red"), "steps"),
    (GOOD.replace("key: save", "key: extract"), "重复"),
    (GOOD.replace("key: save", "key: Save"), "steps"),
    (GOOD.replace("    tools: [kw_search]", "    tools: [kw_search]\n    max_retries: 3"), "steps"),
    (GOOD.replace("${inputs.contract_id}\"}", "${inputs.contract_id + 1}\"}"), "写法不合法"),
    (GOOD.replace("${inputs.contract_id}\"}", "${inputs.contract_id\"}"), "未闭合"),
    (GOOD.replace("type: object\n  properties", "type: array\n  properties"), "object"),
    ("- a\n- b\n", "键值映射"),
    ("a: &x [1]\nb: *x\n", "YAML"),
    ("version: 1\nsteps: []\n", "steps"),
])
def test_invalid_workflows_rejected(text, needle):
    with pytest.raises(PackageInvalid) as caught:
        parse_workflow(text, manifest())
    assert caught.value.code == "invalid_workflow"
    assert caught.value.file == "workflow.yaml"
    assert needle in caught.value.message


def test_tools_need_not_be_in_allowed_tools():
    definition = parse_workflow(GOOD.replace("tool: kw_extract", "tool: kw_elsewhere"), manifest())
    assert definition.steps[0].tool == "kw_elsewhere"


def test_oversized_workflow_rejected():
    with pytest.raises(PackageInvalid):
        parse_workflow("version: 1\n# " + "x" * 70_000, manifest())


def _zip(workflow: str | bytes | None, folder: str = "") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{folder}SKILL.md", SKILL_MD)
        if workflow is not None:
            archive.writestr(f"{folder}workflow.yaml", workflow)
    return buffer.getvalue()


def test_package_carries_workflow_into_manifest():
    package = validate_skill_zip(_zip(GOOD, folder="contract-review/"))
    assert package.workflow is not None
    described = manifest_json(package, ("kw_extract",))
    assert described["workflow"] == package.workflow.to_json()
    assert described["workflowDigest"] == package.workflow.digest
    assert "workflow" not in manifest_json(validate_skill_zip(_zip(None)), ())


def test_package_with_invalid_workflow_is_rejected():
    with pytest.raises(PackageInvalid) as caught:
        validate_skill_zip(_zip(GOOD.replace("key: save", "key: extract")))
    assert caught.value.code == "invalid_workflow"
    with pytest.raises(PackageInvalid):
        validate_skill_zip(_zip(b"\xff\xfe not utf8"))


def test_refs_resolve_whole_values_and_text():
    inputs = {"id": "c-1", "n": 3}
    outputs = {"extract": {"rows": [{"a": 1}, {"a": 2}]}}
    value = {"id": "${inputs.id}", "rows": "${steps.extract.output.rows}", "first": "${steps.extract.output.rows.1.a}",
             "text": "合同 ${inputs.id} 共 ${inputs.n} 份：${steps.extract.output.rows.0}", "plain": 7,
             "list": ["${inputs.n}"]}
    resolved = resolve(value, inputs, outputs)
    assert resolved == {"id": "c-1", "rows": [{"a": 1}, {"a": 2}], "first": 2,
                        "text": '合同 c-1 共 3 份：{"a": 1}', "plain": 7, "list": [3]}
    assert value["id"] == "${inputs.id}"  # 不修改原值
    with pytest.raises(ReferenceUnresolved):
        resolve("${steps.missing.output}", inputs, outputs)
    with pytest.raises(ReferenceUnresolved):
        resolve("${steps.extract.output.rows.9}", inputs, outputs)
    with pytest.raises(ReferenceUnresolved):
        resolve("${inputs.none}", inputs, outputs)


def test_ref_grammar():
    assert parse_ref("steps.a.output").text == "${steps.a.output}"
    assert [r.step for r in find_refs({"x": ["${steps.a.output.b}", "${inputs.c}"]})] == ["a", None]
    for bad in ("steps.a", "steps.a.result", "inputs", "env.HOME", "inputs..a", "inputs.a b"):
        with pytest.raises(ReferenceInvalid):
            parse_ref(bad)
