"""静态约束：上层模块只依赖内核合同，不直接引用宿主客户端；合同模型带版本号。"""

import ast
from pathlib import Path

import pytest

from knowwork_server.kernel import contract

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "knowwork_server"
UPPER_LAYERS = ("conversations", "gateway", "identity", "access", "tools", "knowledge", "skills", "profile",
                "workflows", "debugview", "system")
FORBIDDEN = "host_client"


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.add(module)
            names.update(f"{module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize("layer", UPPER_LAYERS)
def test_upper_layers_do_not_import_host_client(layer):
    root = PACKAGE / layer
    offenders = [str(p.relative_to(PACKAGE)) for p in root.rglob("*.py")
                 if any(FORBIDDEN in name.split(".") for name in imported_modules(p))] if root.exists() else []
    assert offenders == [], f"{layer} 直接引用了宿主客户端实现：{offenders}"


def test_contract_models_carry_schema_version():
    models = [contract.SkillGate(required_tool="t"), contract.SessionPolicy(), contract.SessionSpec(session_id="s"),
              contract.TurnInput(text="x"), contract.KernelEvent(epoch="e", cursor=1, kind="k"),
              contract.PendingApproval(id="a", tool_name="t"), contract.CancelResult(settled=True, status="idle")]
    assert {m.schema_version for m in models} == {contract.SCHEMA_VERSION} == {"kernel.v1"}


def test_api_layer_does_not_reach_into_pump_or_tables():
    source = (PACKAGE / "conversations" / "api.py").read_text(encoding="utf-8")
    assert "_pump" not in source and "RunEvent" not in source and "sqlalchemy" not in source


def test_conversations_only_use_profile_values():
    """会话层只依赖运行配置的值与错误：不把配置表挂到元数据上，也不反向依赖编译实现。"""
    allowed = {"knowwork_server.profile.snapshot", "knowwork_server.profile.errors"}
    offenders = []
    for path in (PACKAGE / "conversations").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "profile." in line and line.lstrip().startswith("from ..profile"):
                module = "knowwork_server.profile." + line.split("from ..profile.")[1].split()[0]
                if module not in allowed:
                    offenders.append(f"{path.name}: {module}")
    assert offenders == []
