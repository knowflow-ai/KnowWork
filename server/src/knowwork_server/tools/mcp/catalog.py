"""MCP 服务的工具清单快照：保存时测试连接并拉取工具清单。

快照存在 mcp_server.tools（JSONB 列表），会话列工具只读快照、不访问网络。

每个工具给管理员看的是一个三档（policy）：允许（直接调）、需确认（调用前请用户批准）、禁用（不给模型），按只读、写入分组
（group）。三档从原有字段推出来，不另存：禁用 = 没启用；允许 = 审批方式是不询问；其余是需确认。分组 = 风险等级是不是只读
（「高风险」并进写入）。这样升级前的设置原样换算，不用迁移数据。

每次同步：
- 按原名合并管理员之前的设置（风险等级、审批方式、是否启用）；
- 第一次登记时照对方的注解预填：标了只读的允许，其余需确认；之后同步出现的新工具一律「需确认」并标「新」，
  不照对方的标注放开（对方升级后加了写操作，不能悄悄变成直接调）；管理员改过这个工具就不再算新；
- 参数结构不合格的工具（根不是对象、不是合法的 JSON Schema、过大）记下原因但不给模型：宿主注册失败会让
  整个会话打不开，宁可少一个工具；
- 对外名字撞了（不同原名规范化后相同，理论上有哈希兜底）只保留第一个。
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..registry import Approval, Risk
from .client import RemoteTool
from .naming import annotated_risk, public_tool_name

MAX_TOOLS = 200
MAX_SCHEMA_BYTES = 64 * 1024
MAX_DESCRIPTION = 2000


@dataclass(frozen=True)
class CatalogTool:
    raw_name: str
    name: str
    description: str
    input_schema: dict[str, Any]
    annotated_risk: Risk
    risk_override: Risk | None = None
    approval_override: Approval | None = None
    enabled: bool = True
    problem: str | None = None
    is_new: bool = False  # 之后同步才出现、管理员还没看过

    @property
    def risk(self) -> Risk:
        return self.risk_override or self.annotated_risk

    @property
    def group(self) -> "Group":
        return "read" if self.risk == "read" else "write"

    @property
    def policy(self) -> "Policy":
        if not self.enabled:
            return "block"
        approval = self.approval_override or ("never" if self.risk == "read" else "ask")
        return "allow" if approval == "never" else "ask"

    @property
    def usable(self) -> bool:
        return self.enabled and self.problem is None

    def to_json(self) -> dict[str, Any]:
        return {"rawName": self.raw_name, "name": self.name, "description": self.description,
                "inputSchema": self.input_schema, "annotatedRisk": self.annotated_risk,
                "riskOverride": self.risk_override, "approvalOverride": self.approval_override,
                "enabled": self.enabled, "problem": self.problem, **({"new": True} if self.is_new else {})}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CatalogTool":
        return cls(raw_name=data["rawName"], name=data["name"], description=data.get("description", ""),
                   input_schema=data.get("inputSchema") or {}, annotated_risk=data.get("annotatedRisk", "write"),
                   risk_override=data.get("riskOverride"), approval_override=data.get("approvalOverride"),
                   enabled=data.get("enabled", True), problem=data.get("problem"), is_new=data.get("new") is True)


Policy = Literal["allow", "ask", "block"]
Group = Literal["read", "write"]
POLICIES: frozenset[str] = frozenset({"allow", "ask", "block"})
GROUPS: frozenset[str] = frozenset({"read", "write"})


def load(rows: list[dict[str, Any]] | None) -> list[CatalogTool]:
    return [CatalogTool.from_json(row) for row in rows or []]


def dump(tools: list[CatalogTool]) -> list[dict[str, Any]]:
    return [tool.to_json() for tool in tools]


def build(server_name: str, remote: list[RemoteTool], previous: list[CatalogTool], *,
          first: bool = False) -> list[CatalogTool]:
    """first：第一次登记（照对方的注解预填）；否则新出现的工具一律需确认并标「新」。"""
    kept = {tool.raw_name: tool for tool in previous}
    built: list[CatalogTool] = []
    names: set[str] = set()
    for item in remote[:MAX_TOOLS]:
        name = public_tool_name(server_name, item.name)
        if name in names:
            continue
        names.add(name)
        fresh = CatalogTool(raw_name=item.name, name=name, description=_description(item),
                            input_schema=item.input_schema if isinstance(item.input_schema, dict) else {},
                            annotated_risk=annotated_risk(item.annotations), problem=schema_problem(item.input_schema))
        old = kept.get(item.name)
        if old is not None:
            built.append(replace(fresh, risk_override=old.risk_override, approval_override=old.approval_override,
                                 enabled=old.enabled, is_new=old.is_new))
        else:
            built.append(fresh if first else replace(fresh, approval_override="ask", is_new=True))
    return built


def adopt(server_name: str, tools: list[CatalogTool]) -> list[CatalogTool]:
    """从别的服务带来工具清单与设置（收为企业 MCP，还拉不到清单时先用来源的）：换成这个服务的工具名，其余照旧。
    之后第一次拉清单时按原名合并（build 的 previous），三档、分组不丢。"""
    return [replace(tool, name=public_tool_name(server_name, tool.raw_name)) for tool in tools]


def set_policy(tools: list[CatalogTool], raw_name: str, *, policy: Policy | None = None,
               group: Group | None = None) -> list[CatalogTool]:
    """改一个工具的三档或分组（None 不改）；改过就不再算新。找不到工具时抛 KeyError。"""
    if policy not in (None, *POLICIES) or group not in (None, *GROUPS):
        raise ValueError("三档或分组不合法")
    if not any(tool.raw_name == raw_name for tool in tools):
        raise KeyError(raw_name)

    def changed(tool: CatalogTool) -> CatalogTool:
        risk_override, approval, enabled = tool.risk_override, tool.approval_override, tool.enabled
        if group is not None:
            annotated = "read" if tool.annotated_risk == "read" else "write"
            risk_override = None if group == annotated else group
            # 挪组不改三档：先把现在生效的审批方式固定下来（只读默认不询问、写入默认询问）
            approval = approval or ("never" if tool.policy == "allow" else "ask")
        if policy == "block":
            enabled = False
        elif policy is not None:
            approval, enabled = ("never" if policy == "allow" else "ask"), True
        return replace(tool, risk_override=risk_override, approval_override=approval, enabled=enabled, is_new=False)

    return [changed(tool) if tool.raw_name == raw_name else tool for tool in tools]


def schema_problem(schema: Any) -> str | None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return "参数结构的根不是对象"
    if len(json.dumps(schema, ensure_ascii=False).encode()) > MAX_SCHEMA_BYTES:
        return "参数结构过大"
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError:
        return "参数结构不是合法的 JSON Schema"
    return None


def _description(item: RemoteTool) -> str:
    text = item.description.strip() or f"MCP 工具 {item.name}"
    return text[:MAX_DESCRIPTION]
