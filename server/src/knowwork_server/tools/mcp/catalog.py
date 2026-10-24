"""MCP 服务的工具清单快照：保存时测试连接并拉取工具清单。

快照存在 mcp_server.tools（JSONB 列表），会话列工具只读快照、不访问网络。每次同步：
- 按原名合并管理员之前的设置（风险等级、审批方式、是否启用），服务端新增的工具默认启用；
- 参数结构不合格的工具（根不是对象、不是合法的 JSON Schema、过大）记下原因但不给模型：宿主注册失败会让
  整个会话打不开，宁可少一个工具；
- 对外名字撞了（不同原名规范化后相同，理论上有哈希兜底）只保留第一个。
"""

import json
from dataclasses import dataclass, replace
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ..registry import APPROVALS, RISKS, Approval, Risk
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

    @property
    def risk(self) -> Risk:
        return self.risk_override or self.annotated_risk

    @property
    def usable(self) -> bool:
        return self.enabled and self.problem is None

    def to_json(self) -> dict[str, Any]:
        return {"rawName": self.raw_name, "name": self.name, "description": self.description,
                "inputSchema": self.input_schema, "annotatedRisk": self.annotated_risk,
                "riskOverride": self.risk_override, "approvalOverride": self.approval_override,
                "enabled": self.enabled, "problem": self.problem}

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "CatalogTool":
        return cls(raw_name=data["rawName"], name=data["name"], description=data.get("description", ""),
                   input_schema=data.get("inputSchema") or {}, annotated_risk=data.get("annotatedRisk", "write"),
                   risk_override=data.get("riskOverride"), approval_override=data.get("approvalOverride"),
                   enabled=data.get("enabled", True), problem=data.get("problem"))


Auto = Literal["auto"]
AUTO: Auto = "auto"


def load(rows: list[dict[str, Any]] | None) -> list[CatalogTool]:
    return [CatalogTool.from_json(row) for row in rows or []]


def dump(tools: list[CatalogTool]) -> list[dict[str, Any]]:
    return [tool.to_json() for tool in tools]


def build(server_name: str, remote: list[RemoteTool], previous: list[CatalogTool]) -> list[CatalogTool]:
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
        built.append(fresh if old is None else replace(fresh, risk_override=old.risk_override,
                                                        approval_override=old.approval_override,
                                                        enabled=old.enabled))
    return built


def configure(tools: list[CatalogTool], raw_name: str, *, risk: Risk | Auto | None = None,
              approval: Approval | Auto | None = None, enabled: bool | None = None) -> list[CatalogTool]:
    """改一个工具的设置：None 表示不改，"auto" 表示恢复为按注解与风险等级推导。找不到工具时抛 KeyError。"""
    if risk not in (None, AUTO, *RISKS) or approval not in (None, AUTO, *APPROVALS):
        raise ValueError("风险等级或审批方式不合法")
    if not any(tool.raw_name == raw_name for tool in tools):
        raise KeyError(raw_name)

    def pick(value, current):
        return current if value is None else None if value == AUTO else value

    return [replace(tool, risk_override=pick(risk, tool.risk_override),
                    approval_override=pick(approval, tool.approval_override),
                    enabled=tool.enabled if enabled is None else enabled)
            if tool.raw_name == raw_name else tool for tool in tools]


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
