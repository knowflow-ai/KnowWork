"""账号生命周期钩子。身份模块不认识工作空间：邀请上记的空间与角色原样交给装配方，由它决定新账号进哪个空间
（凭邀请注册的进邀请所在的空间；没有邀请的进默认空间）。"""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Placement:
    """邀请定好的去处：哪个工作空间、什么角色。"""

    workspace_id: str
    role: str


UserCreatedHook = Callable[[str, Placement | None], Awaitable[None]]


async def notify_user_created(hook: UserCreatedHook | None, user_id: str, placement: Placement | None = None) -> None:
    """账号已经建好并提交；钩子失败只记日志（管理员可以手动加入空间），不让注册失败。"""
    if hook is None:
        return
    try:
        await hook(user_id, placement)
    except Exception:
        log.exception("user-created hook failed for %s", user_id)
