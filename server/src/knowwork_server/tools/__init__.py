"""业务服务侧的工具桥：给宿主的内部工具接口、工具注册表、动作回执。

主线整合：内部应用 `include_router(build_internal_router(service))`；打开宿主会话时
用 `service.issue_session_token(workspace_id, session_id, user_id=...)` 签发令牌，放进宿主建会话请求的 `tools.token`，
并在过期前经宿主 `PUT /v1/sessions/{id}/tool-credential` 续期。
"""

from sqlalchemy.ext.asyncio import async_sessionmaker

from .receipts import ReceiptStore
from .registry import ToolRegistry, builtin_registry
from .service import ToolAuditHook, ToolExecution, ToolNotes, ToolService
from .tokens import ToolTokenSigner


def build_tool_service(master_key: str, sessionmaker: async_sessionmaker, *, registry: ToolRegistry | None = None,
                       token_ttl_seconds: int = 900, audit: ToolAuditHook | None = None,
                       notes: ToolNotes | None = None) -> ToolService:
    return ToolService(registry or builtin_registry(), ToolTokenSigner.from_master_key(master_key),
                       ReceiptStore(sessionmaker), token_ttl_seconds=token_ttl_seconds, audit=audit, notes=notes)


__all__ = ["ToolAuditHook", "ToolExecution", "ToolNotes", "ToolService", "build_tool_service"]
