"""账号生命周期钩子。身份模块不认识工作空间，建号后由装配方决定要做什么（开源版：加入默认空间）。"""

import logging
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

UserCreatedHook = Callable[[str], Awaitable[None]]


async def notify_user_created(hook: UserCreatedHook | None, user_id: str) -> None:
    """账号已经建好并提交；钩子失败只记日志（管理员可以手动加入空间），不让注册失败。"""
    if hook is None:
        return
    try:
        await hook(user_id)
    except Exception:
        log.exception("user-created hook failed for %s", user_id)
