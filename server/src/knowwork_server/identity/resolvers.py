"""按登录方式 id 找提供方：内置的与扩展登记的依次问，第一个给出的为准。

账号密码类（password_logins.py）与跳转类（sso.py 的 RedirectLogins）共用。某个取法出了意料外的错只跳过它（记日志），
别的登录方式照常能用；SsoFailure 是「这个方式找到了但现在用不了」（例如密钥解不开），照旧往外抛，算这次登录失败。
"""

import inspect
import logging
from collections.abc import Awaitable, Callable

from .errors import SsoFailure

log = logging.getLogger(__name__)


class Resolvers[T]:
    def __init__(self) -> None:
        self._resolvers: list[Callable[[str], Awaitable[T | None] | T | None]] = []

    def add(self, resolver: Callable[[str], Awaitable[T | None] | T | None]) -> None:
        self._resolvers.append(resolver)

    async def resolve(self, method_id: str) -> T | None:
        for resolver in self._resolvers:
            try:
                found = resolver(method_id)
                provider = await found if inspect.isawaitable(found) else found
            except SsoFailure:
                raise
            except Exception:
                log.exception("login method resolver failed for %s", method_id)
                continue
            if provider is not None:
                return provider
        return None
