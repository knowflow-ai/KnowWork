"""登录方式列表：登录页按它显示入口（GET /api/auth/methods）。

每种登录方式登记一个「来源」，列表时现取：OIDC 看配置，以后后台配置的 OAuth2、扩展提供的域账号与企业微信
看各自的表。某个来源出错时只跳过它，登录页照常能用账号密码登录。
"""

import inspect
import logging
import re
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)

STYLES = frozenset({"redirect", "password"})
_ID = re.compile(r"^[A-Za-z0-9_.\-]{1,64}$")


@dataclass(frozen=True)
class LoginMethod:
    id: str
    kind: str              # oidc、oauth2、ldap、wecom……前端按它选图标
    label: str             # 按钮上的名字：「使用{label}登录」
    style: str             # redirect 跳转到对方登录；password 在登录页输账号密码（域账号）
    url: str               # 跳转登录的入口，或账号密码提交的地址；只允许本站路径
    # 在哪些客户端的内置浏览器里自动走这个方式（按浏览器标识里的片段认，例如企业微信的 wxwork）
    in_client_agents: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.style not in STYLES:
            raise ValueError(f"未知的登录方式样式：{self.style}")
        if not _ID.match(self.id):
            raise ValueError("登录方式 id 只能是字母、数字和 _.-")
        # 只放本站路径：登录页照着跳，不能借它把人带到别的站点
        if not self.url.startswith("/") or self.url.startswith("//"):
            raise ValueError("登录方式的地址必须是本站路径")

    def view(self) -> dict:
        return {"id": self.id, "kind": self.kind, "label": self.label, "style": self.style, "url": self.url,
                "inClientAgents": list(self.in_client_agents)}


MethodSource = Callable[[], Sequence[LoginMethod] | Awaitable[Sequence[LoginMethod]]]


class LoginMethods:
    def __init__(self) -> None:
        self._sources: list[MethodSource] = []

    def add(self, source: MethodSource) -> None:
        self._sources.append(source)

    async def list(self) -> list[LoginMethod]:
        found: list[LoginMethod] = []
        for source in self._sources:
            try:
                result = source()
                found.extend(await result if inspect.isawaitable(result) else result)
            except Exception:
                log.exception("login method source failed")
        return found
