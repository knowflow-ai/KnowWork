"""人的操作的审计：纯 ASGI 中间件，对公开应用的每个写请求（POST/PUT/PATCH/DELETE）记一条。

只记写请求、记路由模板而不是原始路径、不读请求体。另外：
- 不在中间件里自己解码令牌（免得再写一遍认证）：操作者由认证依赖经 note_principal 登记；
- 结果分三种：2xx/3xx 成功，401/403/429 拒绝（越权、未登录、限流），其余失败；403 是越权，不算失败；
- 写审计失败只记日志不吞掉，出了问题能被发现；
- 不用 BaseHTTPMiddleware（它会缓冲流式响应、拆开上下文变量）。

读请求不记；业务代码可以用 annotate(record=True) 让某个读请求也记（OIDC 回调这种用 GET 完成的登录）。
没有匹配到路由的请求（404 路径）不记，免得扫描器把审计表刷满。
"""

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import context
from .actions import WRITE_METHODS, action_for
from .events import AuditEvent
from .sink import AuditSink, safe_record

log = logging.getLogger(__name__)

REQUEST_ID_HEADER = b"x-request-id"
_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")
DENIED_STATUSES = frozenset({401, 403, 429})


def result_of(status: int) -> str:
    if status < 400:
        return "success"
    return "denied" if status in DENIED_STATUSES else "failed"


def _request_id(scope: Scope) -> str:
    for name, value in scope.get("headers") or ():
        if name == REQUEST_ID_HEADER:
            text = value.decode("latin-1")
            if _REQUEST_ID.fullmatch(text):
                return text
    return uuid.uuid4().hex


class AuditMiddleware:
    def __init__(self, app: ASGIApp, *, sink: AuditSink, default_workspace_id: str,
                 workspace_of: Callable[[dict[str, Any]], str | None] | None = None) -> None:
        self.app = app
        self.sink = sink
        self.default_workspace_id = default_workspace_id
        self.workspace_of = workspace_of or (lambda params: params.get("workspace_id"))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = _request_id(scope)
        status = {"code": 500}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                headers = [h for h in message.get("headers", []) if h[0].lower() != REQUEST_ID_HEADER]
                message = {**message, "headers": [*headers, (REQUEST_ID_HEADER, request_id.encode())]}
            await send(message)

        note, token = context.begin()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            await self._record(scope, note, 500, request_id)
            raise
        finally:
            context.end(token)
        await self._record(scope, note, status["code"], request_id)

    async def _record(self, scope: Scope, note: context.AuditNote, status: int, request_id: str) -> None:
        try:
            event = self._event(scope, note, status, request_id)
        except Exception:
            log.exception("audit event build failed for %s %s", scope.get("method"), scope.get("path"))
            return
        if event is not None:
            await safe_record(self.sink, event)

    def _event(self, scope: Scope, note: context.AuditNote, status: int, request_id: str) -> AuditEvent | None:
        method = scope.get("method", "")
        route = getattr(scope.get("route"), "path", None)
        if route is None or (method not in WRITE_METHODS and not note.record):
            return None
        params = dict(scope.get("path_params") or {})
        spec = action_for(method, route)
        principal = note.principal
        actor_id = principal.user_id if principal is not None else note.actor_id
        credential_type = principal.auth_method if principal is not None else None
        credential_id = (principal.api_key_id or principal.session_id) if principal is not None else None
        detail = {"method": method, "route": route, **_query_detail(scope, spec.query_detail), **note.detail}
        client = scope.get("client")
        return AuditEvent(
            workspace_id=self.workspace_of(params) or self.default_workspace_id, actor_type="human",
            actor_id=actor_id, credential_type=credential_type, credential_id=credential_id,
            action=note.action or spec.action, resource_type=note.resource_type or spec.resource_type,
            resource_id=note.resource_id or (params.get(spec.id_param) if spec.id_param else None),
            result=result_of(status), status_code=status, ip=client[0] if client else None,
            request_id=request_id, session_id=params.get("conversation_id"), detail=detail,
        )


def _query_detail(scope: Scope, names: tuple[str, ...]) -> dict[str, str]:
    if not names:
        return {}
    query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
    return {name: query[name][0] for name in names if query.get(name)}
