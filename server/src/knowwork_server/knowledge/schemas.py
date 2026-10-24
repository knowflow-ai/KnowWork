"""接口的入参与出参。出参按权限分两套模型：「可用」只看到摘要，「可编辑」才看到连接配置；
两套都不含任何密钥明文，只有「已配置」与末四位。
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from .assertion import MIN_KEY_CHARS
from .knowflow_mapping import KnowFlowConfig, validate_base_url


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _name(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if not 1 <= len(cleaned) <= 100:
        raise ValueError("名称长度须为 1 到 100 个字符")
    return cleaned


def _service_key(value: SecretStr | None) -> SecretStr | None:
    if value is not None and len(value.get_secret_value()) < MIN_KEY_CHARS:
        raise ValueError(f"服务密钥至少 {MIN_KEY_CHARS} 个字符")
    return value


# 统一账号方式的 KnowFlow API Key
AccountKey = Annotated[SecretStr, Field(min_length=8, max_length=512)]


class SourceCreate(_Input):
    name: str
    kind: Literal["knowflow"] = "knowflow"
    base_url: str = Field(max_length=500)
    config: KnowFlowConfig = Field(default_factory=KnowFlowConfig)
    service_key: SecretStr | None = None   # 签名断言方式
    account_key: AccountKey | None = None  # 统一账号方式

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return _name(value)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_base_url(value) if value is not None else None

    @field_validator("service_key")
    @classmethod
    def _check_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _service_key(value)


class SourcePatch(_Input):
    name: str | None = None
    base_url: str | None = Field(default=None, max_length=500)
    config: KnowFlowConfig | None = None
    service_key: SecretStr | None = None
    account_key: AccountKey | None = None
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str | None) -> str | None:
        return _name(value)

    @field_validator("base_url")
    @classmethod
    def _check_url(cls, value: str | None) -> str | None:
        return validate_base_url(value) if value is not None else None

    @field_validator("service_key")
    @classmethod
    def _check_key(cls, value: SecretStr | None) -> SecretStr | None:
        return _service_key(value)


class SecretStatus(BaseModel):
    configured: bool
    last4: str = ""


class SourceSummary(BaseModel):
    """有「可用」权限的成员看到的样子。"""

    id: str
    name: str
    kind: str
    enabled: bool
    auth_mode: str


class SourceDetail(SourceSummary):
    """有「可编辑」权限的成员看到的样子。"""

    base_url: str
    config: KnowFlowConfig
    service_key: SecretStatus  # 签名断言方式的服务密钥
    account_key: SecretStatus  # 统一账号方式的 KnowFlow API Key
    created_by: str
    can_edit: bool = True


class ProbeResult(BaseModel):
    ok: bool
    message: str


class BaseView(BaseModel):
    id: str     # 全局 id：<知识源id>.<知识库id>，授权与本轮选择都用它
    kb_id: str  # 知识源内的 id
    name: str


class SourceBases(BaseModel):
    """一个知识源下的知识库，给授权页的树与「管理 → 知识库」用。列不出来时 bases 为空、error 写明原因。"""

    source_id: str
    source_name: str
    auth_mode: str
    enabled: bool
    bases: list[BaseView]
    error: str | None = None
