"""身份接口的请求体与响应体。"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .api_keys import ApiKeyInfo
from .passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginBody(_Strict):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class InviteBody(_Strict):
    email: str = Field(min_length=3, max_length=320)


class AcceptInviteBody(_Strict):
    token: str = Field(min_length=16, max_length=128)
    name: str = Field(default="", max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


class CreateApiKeyBody(_Strict):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(min_length=1, max_length=32)
    expires_at: AwareDatetime | None = Field(default=None, alias="expiresAt")
    workspace_id: str | None = Field(default=None, alias="workspaceId", max_length=64)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


def api_key_view(info: ApiKeyInfo) -> dict:
    return {
        "id": info.id,
        "name": info.name,
        "prefix": info.prefix,
        "scopes": list(info.scopes),
        "workspaceId": info.workspace_id,
        "createdAt": info.created_at.isoformat(),
        "expiresAt": info.expires_at.isoformat() if info.expires_at else None,
        "lastUsedAt": info.last_used_at.isoformat() if info.last_used_at else None,
        "revoked": info.revoked_at is not None,
    }
