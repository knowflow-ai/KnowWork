"""请求内的审计标注：业务代码往当前请求的那条审计记录里补信息，不必认识审计模块的其余部分。

审计中间件在请求开始时放一个 AuditNote，结束时读出来合进审计事件。没有中间件（例如单元测试、
内部端口）时 annotate 什么都不做，业务行为不受影响。

- note_principal：认证依赖解析出调用者后登记，中间件据此填操作者与凭证 id，不必重新解析令牌；
- annotate：补 action、资源、detail 摘要，或标记 record=True 让读请求也记一条（OIDC 回调这种登录走 GET）。
"""

import contextvars
from dataclasses import dataclass, field
from typing import Any

from ..auth.principal import Principal


@dataclass
class AuditNote:
    """一次请求内可变的标注槽，只在请求所在的上下文里活着。"""

    principal: Principal | None = None
    actor_id: str | None = None
    action: str | None = None
    resource_type: str | None = None
    resource_id: str | None = None
    record: bool = False
    detail: dict[str, Any] = field(default_factory=dict)


_current: contextvars.ContextVar[AuditNote | None] = contextvars.ContextVar("knowwork_audit_note", default=None)


def begin() -> tuple[AuditNote, contextvars.Token]:
    note = AuditNote()
    return note, _current.set(note)


def end(token: contextvars.Token) -> None:
    _current.reset(token)


def current() -> AuditNote | None:
    return _current.get()


def note_principal(principal: Principal) -> None:
    note = _current.get()
    if note is not None:
        note.principal = principal


def annotate(*, actor_id: str | None = None, action: str | None = None, resource_type: str | None = None,
             resource_id: str | None = None, record: bool | None = None, **detail: Any) -> None:
    note = _current.get()
    if note is None:
        return
    note.actor_id = actor_id or note.actor_id
    note.action = action or note.action
    note.resource_type = resource_type or note.resource_type
    note.resource_id = resource_id or note.resource_id
    note.record = note.record if record is None else record
    note.detail = {**note.detail, **detail}
