"""输入校验（pydantic）与对外视图（不可变）。视图里永远没有密钥明文。"""

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

Name = Annotated[str, Field(min_length=1, max_length=100)]
Alias = Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:\-]{1,64}$")]
Price = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=6)]


class Capability(StrEnum):
    CHAT = "chat"
    EMBEDDING = "embedding"
    RERANK = "rerank"
    VISION = "vision"


def _check_base_url(value: str) -> str:
    parts = urlsplit(value.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        raise ValueError("API 地址须为 http(s)://主机[:端口]/路径，且不能带账号密码")
    if parts.query or parts.fragment:
        raise ValueError("API 地址不能带查询串或锚点")
    return value.strip().rstrip("/")


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderCreate(_Input):
    name: Name
    kind: Annotated[str, Field(pattern=r"^[a-z0-9_\-]{1,32}$")] = "openai_compatible"
    base_url: Annotated[str, Field(min_length=8, max_length=500)]
    api_key: Annotated[str | None, Field(default=None, max_length=1000)] = None
    enabled: bool = True

    _url = field_validator("base_url")(_check_base_url)


class ProviderUpdate(_Input):
    """api_key：不传表示不改，传空串表示清除。"""

    name: Name | None = None
    base_url: Annotated[str | None, Field(default=None, min_length=8, max_length=500)] = None
    api_key: Annotated[str | None, Field(default=None, max_length=1000)] = None
    enabled: bool | None = None

    @field_validator("base_url")
    @classmethod
    def _url(cls, value: str | None) -> str | None:
        return None if value is None else _check_base_url(value)


class ModelCreate(_Input):
    provider_id: Annotated[str, Field(min_length=1, max_length=64)]
    alias: Alias
    name: Annotated[str, Field(min_length=1, max_length=200)]
    capabilities: Annotated[tuple[Capability, ...], Field(min_length=1, max_length=4)] = (Capability.CHAT,)
    context_length: Annotated[int | None, Field(default=None, ge=1, le=10_000_000)] = None
    input_price_per_mtok: Price | None = None
    output_price_per_mtok: Price | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "CNY"
    enabled: bool = True


class ModelUpdate(_Input):
    alias: Alias | None = None
    name: Annotated[str | None, Field(default=None, min_length=1, max_length=200)] = None
    capabilities: Annotated[tuple[Capability, ...] | None, Field(default=None, min_length=1, max_length=4)] = None
    context_length: Annotated[int | None, Field(default=None, ge=1, le=10_000_000)] = None
    input_price_per_mtok: Price | None = None
    output_price_per_mtok: Price | None = None
    currency: Annotated[str | None, Field(default=None, pattern=r"^[A-Z]{3}$")] = None
    enabled: bool | None = None


class GroupCreate(_Input):
    name: Name
    description: Annotated[str, Field(max_length=500)] = ""
    model_ids: Annotated[tuple[str, ...], Field(max_length=200)] = ()


class GroupUpdate(_Input):
    name: Name | None = None
    description: Annotated[str | None, Field(default=None, max_length=500)] = None
    model_ids: Annotated[tuple[str, ...] | None, Field(default=None, max_length=200)] = None


@dataclass(frozen=True)
class ProviderView:
    id: str
    name: str
    kind: str
    base_url: str
    api_key_configured: bool
    api_key_last4: str
    enabled: bool


@dataclass(frozen=True)
class ModelView:
    id: str
    provider_id: str
    alias: str
    name: str
    capabilities: tuple[str, ...]
    context_length: int | None
    input_price_per_mtok: Decimal | None
    output_price_per_mtok: Decimal | None
    currency: str
    enabled: bool


@dataclass(frozen=True)
class GroupView:
    id: str
    name: str
    description: str
    model_ids: tuple[str, ...]


@dataclass(frozen=True)
class Route:
    """网关转发一次调用所需的全部信息。api_key 只在内存里，不落日志、不出接口。"""

    model_id: str
    alias: str
    upstream_model: str
    base_url: str
    api_key: str

    def __repr__(self) -> str:  # 防止异常栈或日志意外带出密钥
        return f"Route(alias={self.alias!r}, upstream_model={self.upstream_model!r}, base_url={self.base_url!r})"
