"""技能包里的固定工作流 workflow.yaml：模型自己编排不可靠时的退路。

技能包根目录（SKILL.md 旁边）可选放一个 workflow.yaml，安装时解析并校验，不合格整个包拒绝安装。

```yaml
version: 1
description: 合同复核
inputs:                      # 入参的 JSON Schema，根必须是 object
  type: object
  properties: {contract_id: {type: string}}
  required: [contract_id]
steps:
  - key: extract             # 小写字母开头，字母、数字、_、-，流程内唯一
    type: tool               # 确定性工具步骤：直接调工具注册表，不经模型
    tool: kw_extract
    args: {id: "${inputs.contract_id}"}
  - key: summarize
    type: agent              # 一次受限子会话：只开放 tools 里的工具
    prompt: 根据抽取结果写复核意见
    tools: [kw_search]
    input: "${steps.extract.output}"
    output_schema: {type: object, properties: {summary: {type: string}}, required: [summary]}
  - key: confirm
    type: approval           # 审批门：外部批准才继续，拒绝即以失败结束
    title: 确认复核意见
    summary: "${steps.summarize.output.summary}"
output: "${steps.summarize.output}"
```

规则：引用只能指向入参与**更前面**的步骤；JSON Schema 本身要合法（Draft 2020-12）。键用 snake_case，未知键一律拒绝。

步骤用到的工具**不要求**列进 SKILL.md 的 allowed-tools：allowed-tools 服务于对话里的技能门（专业工具一个只能被
一个技能认领），而工作流的工具由业务服务直接调用，能不能用由工具注册表按运行者此刻的权限判定。
要求列进去会让两个都用 kw_search 的工作流技能互相冲突，装不上第二个。
"""

import hashlib
import json
from typing import Annotated, Any, Literal

import yaml
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .errors import PackageInvalid
from .manifest import TOOL_NAME, SkillManifest, _NoAliasLoader
from .workflow_refs import ReferenceInvalid, find_refs

FILE = "workflow.yaml"
MAX_BYTES = 64 * 1024
MAX_STEPS = 50
MAX_RETRIES = 2
STEP_KEY = r"^[a-z][a-z0-9_-]{0,47}$"


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Step(_Frozen):
    key: str = Field(pattern=STEP_KEY)
    # 可选步骤失败时记为跳过、结果为 null，流程继续；默认失败即终止整个运行
    optional: bool = False


class ToolStep(_Step):
    type: Literal["tool"]
    tool: str = Field(pattern=TOOL_NAME.pattern)
    args: dict[str, Any] = Field(default_factory=dict)
    # 只读工具的结果默认可以跨运行复用；写入类工具永不缓存（每次运行都真实执行，靠动作回执去重）
    cache: bool = True
    output_schema: dict[str, Any] | None = None


class AgentStep(_Step):
    type: Literal["agent"]
    prompt: str = Field(min_length=1, max_length=20_000)
    tools: tuple[str, ...] = Field(default=(), max_length=32)
    input: Any = None
    output_schema: dict[str, Any]
    max_retries: int = Field(default=MAX_RETRIES, ge=0, le=MAX_RETRIES)
    timeout_seconds: int = Field(default=300, ge=10, le=1800)
    cache: bool = True


class ApprovalStep(_Step):
    type: Literal["approval"]
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=4000)


Step = Annotated[ToolStep | AgentStep | ApprovalStep, Field(discriminator="type")]


class WorkflowDefinition(_Frozen):
    version: Literal[1]
    description: str = Field(default="", max_length=2000)
    inputs: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    steps: tuple[Step, ...] = Field(min_length=1, max_length=MAX_STEPS)
    output: Any = None

    def to_json(self) -> dict[str, Any]:
        """规范化的 JSON 形态：落库、做快照、算摘要都用它。"""
        return self.model_dump(mode="json")

    @property
    def digest(self) -> str:
        return definition_digest(self.to_json())

    def step(self, key: str) -> ToolStep | AgentStep | ApprovalStep:
        return next(step for step in self.steps if step.key == key)


def definition_digest(definition: dict[str, Any]) -> str:
    canonical = json.dumps(definition, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _invalid(message: str, field: str = "") -> PackageInvalid:
    return PackageInvalid("invalid_workflow", message, file=FILE, field=field)


def _load(text: str) -> dict[str, Any]:
    if len(text.encode()) > MAX_BYTES:
        raise _invalid(f"workflow.yaml 不能超过 {MAX_BYTES} 字节")
    try:
        parsed = yaml.load(text, Loader=_NoAliasLoader)
    except yaml.YAMLError as error:
        raise _invalid("workflow.yaml 不是合法的 YAML（不允许锚点与别名）") from error
    if not isinstance(parsed, dict):
        raise _invalid("workflow.yaml 必须是键值映射")
    return parsed


def _model(raw: dict[str, Any]) -> WorkflowDefinition:
    try:
        return WorkflowDefinition.model_validate(raw)
    except ValidationError as error:
        first = error.errors()[0]
        where = ".".join(str(part) for part in first.get("loc", ()))
        raise _invalid(f"workflow.yaml 的 {where or '根'} 不合法：{first.get('msg', '')[:200]}", where) from error


def _check_schema(schema: dict[str, Any], where: str) -> None:
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise _invalid(f"{where} 不是合法的 JSON Schema：{error.message[:200]}", where) from error


def _check_refs(value: Any, earlier: set[str], input_names: set[str] | None, where: str) -> None:
    try:
        refs = find_refs(value)
    except ReferenceInvalid as error:
        raise _invalid(f"{where}：{error}", where) from error
    for ref in refs:
        if ref.root == "steps" and ref.step not in earlier:
            raise _invalid(f"{where} 引用了不存在或不在它之前的步骤 {ref.step}", where)
        if ref.root == "inputs" and input_names is not None and ref.path[0] not in input_names:
            raise _invalid(f"{where} 引用了没有声明的入参 {ref.path[0]}", where)


def _step_fields(step: ToolStep | AgentStep | ApprovalStep) -> dict[str, Any]:
    if isinstance(step, ToolStep):
        return {"args": step.args}
    if isinstance(step, AgentStep):
        return {"prompt": step.prompt, "input": step.input}
    return {"title": step.title, "summary": step.summary}


def _check_tools(step: ToolStep | AgentStep | ApprovalStep) -> None:
    names = (step.tool,) if isinstance(step, ToolStep) else step.tools if isinstance(step, AgentStep) else ()
    for name in names:
        if not TOOL_NAME.match(name):
            raise _invalid(f"步骤 {step.key} 的工具名 {name[:64]} 不合法", f"steps.{step.key}")
    if len(set(names)) != len(names):
        raise _invalid(f"步骤 {step.key} 的工具清单有重复", f"steps.{step.key}")


def _validate(definition: WorkflowDefinition) -> None:
    _check_schema(definition.inputs, "inputs")
    if definition.inputs.get("type") != "object":
        raise _invalid("inputs 的根类型必须是 object", "inputs")
    properties = definition.inputs.get("properties")
    input_names = set(properties) if isinstance(properties, dict) else None
    earlier: set[str] = set()
    for step in definition.steps:
        where = f"steps.{step.key}"
        if step.key in earlier:
            raise _invalid(f"步骤键 {step.key} 重复", where)
        _check_tools(step)
        schema = getattr(step, "output_schema", None)
        if schema is not None:
            _check_schema(schema, f"{where}.output_schema")
        _check_refs(_step_fields(step), earlier, input_names, where)
        earlier.add(step.key)
    _check_refs(definition.output, earlier, input_names, "output")


def parse_workflow(text: str, manifest: SkillManifest) -> WorkflowDefinition:
    """manifest 目前只用于将来按技能做额外约束；工具权限在运行时判定。"""
    del manifest
    definition = _model(_load(text))
    _validate(definition)
    return definition


def load_definition(snapshot: dict[str, Any]) -> WorkflowDefinition:
    """从落库的规范 JSON 还原定义（运行快照、发布清单里都存这个形态）。"""
    return WorkflowDefinition.model_validate(snapshot)
