"""审计（基础审计 90 天，区分人和 Agent 的操作）。

- 建表：AUDIT_TABLES（挂在 db.base.Base 上），行级安全：AUDIT_RLS_TABLES = ("audit_log",)；
- 装配：`audit = build_audit(global_sessionmaker, default_workspace_id=DEFAULT_WORKSPACE_ID)`，
  启动 `audit.start()`、停止 `await audit.close()`；
- 人的操作：`audit.install(public_app)` 挂中间件；认证依赖经 context.note_principal 登记调用者；
- Agent 的操作：`build_tool_service(..., audit=audit.tool_hook)`；
- 查询：`include_router(build_router(audit.query, access, scoped_principal(identity, AUDIT_SCOPE)))`；
- 清理：worker 每日 `await audit.purge(retention_days=90)`。

本包的 __init__ 不急着导入表与写入器：identity、conversations 等只需要 audit.context 来做标注，
不应因此把审计表挂到元数据上。下面的名字按需加载。
"""

from importlib import import_module
from typing import Any

_EXPORTS = {
    "AUDIT_TABLES": ".models", "AUDIT_RLS_TABLES": ".models", "AuditLog": ".models",
    "AuditEvent": ".events", "AuditSink": ".sink", "DatabaseAuditSink": ".sink", "FanoutSink": ".sink",
    "safe_record": ".sink", "AuditWriter": ".writer", "AuditMiddleware": ".middleware",
    "AuditQuery": ".query", "AuditFilter": ".query", "purge_expired": ".retention",
    "AuditModule": ".assembly", "build_audit": ".assembly", "build_router": ".api", "AUDIT_SCOPE": ".api",
    "tool_audit_hook": ".agent",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(import_module(module, __name__), name)


