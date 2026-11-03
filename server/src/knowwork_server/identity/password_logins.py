"""账号密码类的外部登录（域账号登录等）：扩展登记「按登录方式 id 取提供方」的函数，登录路由按 id 找到它校验账号密码。

提供方只负责校验并给出外部身份（ExternalProfile），认人、建号、签发会话都走 external.py；失败抛 SsoFailure。
登录页的入口另由扩展登记到登录方式列表（methods.py，style=password）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .external import ExternalProfile
from .resolvers import Resolvers


@dataclass(frozen=True)
class PasswordProvider:
    id: str
    kind: str                # 会话记的登录方式：ldap……
    verify: Callable[[str, str], Awaitable[ExternalProfile]]
    # 目录绑定成功就说明是企业里的人：默认第一次登录就建号（进哪个空间看成员规则）
    allow_signup: bool = True


class PasswordLogins(Resolvers[PasswordProvider]):
    pass
