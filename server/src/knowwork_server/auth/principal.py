"""调用者身份（不可变）。

- 浏览器会话（账号密码 session，或外部登录 oidc、oauth2、ldap、wecom 等）不受权限范围限制，scopes 为 None，
  权限完全由 RBAC 判定；
- API Key（api_key）只能做它的 scopes 允许的事，且同样要过所属用户的 RBAC。
"""

from dataclasses import dataclass

# api_key，或会话的登录方式（session 账号密码，其余是外部登录方式的 kind，见 identity/sessions.py 的格式校验）
AuthMethod = str


@dataclass(frozen=True, slots=True)
class Principal:
    user_id: str
    auth_method: AuthMethod
    session_id: str | None = None
    api_key_id: str | None = None
    scopes: frozenset[str] | None = None
    # API Key 绑定的工作空间；为空表示不限定（仍要过成员判定）。会话身份恒为空。
    workspace_id: str | None = None

    @property
    def is_api_key(self) -> bool:
        return self.auth_method == "api_key"

    def allows(self, *required: str) -> bool:
        """API Key 必须覆盖全部所需范围；会话身份不受范围限制。"""
        if self.scopes is None:
            return True
        return all(scope in self.scopes for scope in required)
