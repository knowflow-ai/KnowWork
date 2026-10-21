"""业务服务配置，全部来自环境变量（前缀 KNOWWORK_）。

部署主密钥二选一：`KNOWWORK_MASTER_KEY`（直接给值）或 `KNOWWORK_MASTER_KEY_FILE`（指向 bootstrap 生成的只读文件）。
宿主令牌不需要手填：未显式给出时由主密钥经 HKDF 按用途派生。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .db.migrate import validate_role_name
from .kernel.tokens import derive_key

MIN_MASTER_KEY_LENGTH = 32
HOST_TOKEN_PURPOSE = "host-token"


def derive_secret(master_key: str, purpose: str) -> str:
    """按用途派生一个文本形式的子密钥（64 位十六进制）。bootstrap 与业务服务用同一函数，两侧一致。"""
    return derive_key(master_key, purpose).hex()


def _plain(value: Any) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
    return text.strip() or None


def _read_key_file(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as error:
        raise ValueError("主密钥文件无法读取") from error


def _resolve_master_key(data: dict[str, Any]) -> str:
    direct = _plain(data.get("master_key"))
    key_file = _plain(data.get("master_key_file"))
    if direct and key_file:
        raise ValueError("主密钥的环境变量与文件路径只能二选一")
    if key_file:
        direct = _read_key_file(key_file)
    if not direct:
        raise ValueError("缺少部署主密钥（KNOWWORK_MASTER_KEY 或 KNOWWORK_MASTER_KEY_FILE）")
    if len(direct) < MIN_MASTER_KEY_LENGTH:
        raise ValueError(f"部署主密钥至少 {MIN_MASTER_KEY_LENGTH} 个字符")
    return direct


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWWORK_", extra="ignore", hide_input_in_errors=True)

    database_url: str = "postgresql+asyncpg://knowwork@127.0.0.1:5432/knowwork"
    # 启动时把库升级到最新迁移（默认）；create_schema 只给测试用，直接按模型建表、不记迁移版本。
    auto_migrate: bool = True
    create_schema: bool = False
    # 迁移账号（表的 owner）。不设则沿用 database_url；生产应分开，否则运行账号是 owner、能关掉行级安全。
    migration_database_url: str = ""
    # 运行账号的角色名：设了就在迁移结束后给它授增删改查与序列权限（含以后新建的表）。
    app_db_role: str = ""
    # 迁移取到全局锁之后，DDL 等待业务连接释放表锁的上限
    migration_lock_timeout_seconds: float = Field(default=30.0, gt=0, le=3600)
    # 部署主密钥：各用途的子密钥由它经 HKDF 派生，不直接使用。
    master_key: SecretStr = Field(min_length=MIN_MASTER_KEY_LENGTH)
    master_key_file: Path | None = None

    # 公开端口对浏览器与 SDK；内部端口只给宿主回调，只监听容器内网，网关不转发。
    public_host: str = "0.0.0.0"
    public_port: int = Field(default=8000, ge=1, le=65535)
    internal_host: str = "0.0.0.0"
    internal_port: int = Field(default=8001, ge=1, le=65535)

    host_base_url: str = "http://runtime-host:7300"
    host_token: SecretStr = Field(min_length=32)

    # 模型网关的上游（OpenAI 兼容）。第一阶段一个工作空间一个默认模型。
    model_upstream_base_url: str = "https://api.deepseek.com/v1"
    model_upstream_api_key: SecretStr = SecretStr("")
    model_upstream_model: str = "deepseek-chat"
    # 宿主请求模型时用的名字（宿主的 KNOWWORK_MODEL）；没在会话里看到 request/header 时按它记用量。
    host_model_alias: str = "default"
    model_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    # 单次模型调用：总时长上限、两个流式块之间的空闲上限；每轮最多几次模型调用（0 表示不限）
    model_call_timeout_seconds: float = Field(default=300.0, gt=0, le=3600)
    model_idle_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    model_max_calls_per_turn: int = Field(default=50, ge=0, le=1000)
    model_probe_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    usage_timezone: str = "Asia/Shanghai"

    # 对象存储（技能包、产物）：配了 S3 端点就用 S3 兼容服务（RustFS），否则落本地目录（开发用）。
    s3_endpoint_url: str = ""
    s3_bucket: str = "knowwork"
    s3_access_key: SecretStr = SecretStr("")
    s3_secret_key: SecretStr = SecretStr("")
    blob_dir: str = "./data/blobs"

    # 审计日志保留天数，由 worker 每天清理一次
    audit_retention_days: int = Field(default=90, ge=1, le=3650)

    # 浏览器访问的外部地址：邀请链接与 OIDC 回调据此拼出。
    public_base_url: str = "http://localhost"
    # 会话 Cookie 只走 HTTPS；本机 http 调试时才关掉。
    cookie_secure: bool = True
    tool_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    # 工具令牌续期：单次签发 / 轮换的超时与一轮内的并发上限。
    tool_token_call_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    tool_token_renew_concurrency: int = Field(default=8, ge=1, le=64)

    # 部署的版本号，系统信息页显示；镜像构建或 compose 传入，本机开发是 dev。
    version: str = "dev"

    # OIDC 单点登录：issuer 为空即不启用。
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: SecretStr = SecretStr("")
    oidc_redirect_uri: str = ""

    @model_validator(mode="before")
    @classmethod
    def _fill_secrets(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        master = _resolve_master_key(data)
        return {
            **data,
            "master_key": master,
            "host_token": _plain(data.get("host_token")) or derive_secret(master, HOST_TOKEN_PURPOSE),
        }

    @field_validator("app_db_role")
    @classmethod
    def _valid_role(cls, value: str) -> str:
        return validate_role_name(value) if value else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
