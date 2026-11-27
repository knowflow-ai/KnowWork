"""业务服务配置，全部来自环境变量（前缀 KNOWWORK_）。

部署主密钥二选一：`KNOWWORK_MASTER_KEY`（直接给值）或 `KNOWWORK_MASTER_KEY_FILE`（指向 bootstrap 生成的只读文件）。
宿主令牌不需要手填：未显式给出时由主密钥经 HKDF 按用途派生。
"""

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

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
    # 每个进程处理请求的数据库连接池：常驻几条、忙时最多再多开几条（默认 5 + 10，与改成可配置之前一样）。
    # 并发高时可以调大，注意 api、worker 每个进程各一份，加起来不能超过 PostgreSQL 的 max_connections
    db_pool_size: int = Field(default=5, ge=1, le=200)
    db_max_overflow: int = Field(default=10, ge=0, le=500)
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
    # 编辑器端口（可选）：扩展提供的在线编辑器要一个和本站分开的来源（编辑器的路径写死在站点根上，渲染的又是
    # 不可信的文档，不该和本站同源），网关给它单独的端口或子域名再转到这里。不设就不开；设了但没有扩展提供编辑器也不开。
    editor_host: str = "0.0.0.0"
    editor_port: int | None = Field(default=None, ge=1, le=65535)
    # 独立的模型网关进程（ops/model_gateway.py）：宿主调模型经它，不记状态，忙不过来就多开几个 worker 进程
    model_gateway_host: str = "0.0.0.0"
    model_gateway_port: int = Field(default=8002, ge=1, le=65535)
    model_gateway_workers: int = Field(default=1, ge=1, le=32)
    # worker 连业务服务内部端口的地址：扩展经它替用户发起后台对话（conversations/background.py）
    internal_url: str = "http://127.0.0.1:8001"

    host_base_url: str = "http://runtime-host:7300"
    host_token: SecretStr = Field(min_length=32)

    # 个人专属宿主：用了上传插件的人，云端对话跑在他自己的宿主里（hosts 包）。off 不开（上传的插件只在桌面端的
    # 本机对话里生效）；docker 每人一个容器（部署用，要能连 Docker，同会话沙箱）；process 直接起进程（只给本机开发，
    # 插件代码没有隔离）
    plugin_hosts: Literal["off", "docker", "process"] = "off"
    plugin_host_max: int = Field(default=50, ge=1, le=2000)
    plugin_host_idle_seconds: int = Field(default=600, ge=30, le=86400)
    # 每人的持久数据（会话日志、插件存的数据）多久没用就删掉；0 只删已经不在那个空间的人。删了以后他在专属宿主上的
    # 旧对话还能翻看，但接不着聊了（hosts/cleanup.py）
    plugin_host_retention_days: int = Field(default=90, ge=0, le=3650)
    # docker：镜像（同共享宿主的镜像）、宿主那张内部网络的名字、每个容器的内存上限
    plugin_host_image: str = ""
    plugin_host_network: str = ""
    plugin_host_memory_mb: int = Field(default=768, ge=256, le=16384)
    # 业务服务不在 Docker 里跑时打开（本机试容器的起法）：把专属宿主的端口发布到本机回环口再连
    plugin_host_publish: bool = False
    # process：runtime-host 的目录、放各人 DSH_HOME 与解开的插件的目录、Node 所在目录（不给用 PATH 里的）
    plugin_host_runtime_dir: str = ""
    plugin_host_dir: str = "./data/plugin-hosts"
    plugin_host_node_bin: str = ""
    # 专属宿主回调业务服务、调模型的地址；不给时回调用 internal_url、模型走业务服务内部端口上的同一个接口
    plugin_host_callback_url: str = ""
    plugin_host_model_base_url: str = ""

    # 模型网关的上游（OpenAI 兼容）。第一阶段一个工作空间一个默认模型。
    model_upstream_base_url: str = "https://api.deepseek.com/v1"
    model_upstream_api_key: SecretStr = SecretStr("")
    model_upstream_model: str = "deepseek-chat"
    # 宿主请求模型时用的名字（宿主的 KNOWWORK_MODEL）；没在会话里看到 request/header 时按它记用量。
    host_model_alias: str = "default"
    model_token_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    # 单次模型调用：总时长上限、两个流式块之间的空闲上限；每轮最多几次模型调用（0 表示不限）。
    # 每轮调用次数默认不限，同 DSH 官方（一轮做到模型自己停下为止）：原来默认 50，长一点的活（边改文档边跑命令）
    # 做到第 51 步就被取消，界面上像是被人停了。要兜底的部署自己设
    # 一次模型调用从发出到流结束的上限（模型读输入也算）。2026-11-25 从 300 放大：推理模型、本地部署出字慢，很长的
    # 回答要好几分钟；卡住的调用由开始出字后的空闲超时（下一行）截断，不靠它
    model_call_timeout_seconds: float = Field(default=1200.0, gt=0, le=3600)
    model_idle_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    model_max_calls_per_turn: int = Field(default=0, ge=0, le=10000)
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
    # 网页嵌入的限次计数（channels/limits.py）用的 Redis，同 worker 的 KNOWWORK_REDIS_URL；为空时只在进程内计（本机开发）
    redis_url: str = ""
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
