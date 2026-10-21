"""沙箱配置（环境变量前缀 KNOWWORK_SANDBOX_），口径照抄 WeKnora 的 Docker 后端。

- 默认关闭：`KNOWWORK_SANDBOX_DOCKER_ENABLED=true` 才开（对应 WeKnora 的 WEKNORA_SANDBOX_DOCKER_ENABLED）。
  能创建容器就等于能控制这个 daemon，本机 docker.sock 等同宿主机 root，所以要部署方明确打开；
- daemon 地址留空时按 DOCKER_HOST → 当前 docker context → unix:///var/run/docker.sock 检测；
  远端 tcp:// 必须配 TLS 证书目录（ca.pem、cert.pem、key.pem）；
- 网络只允许 bridge（可出网）或 none（断网），默认 bridge；allow_internet=false 时一律 none。
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .docker_host import validate_docker_host, validate_remote_tls

GIB = 1024 * 1024 * 1024
MIB = 1024 * 1024
# 容器姿态的版本号：入口命令、能力集等改了就加一，旧容器按「配置对不上」重建。
POSTURE_VERSION = "4"  # 3：入口建 /sessions/<会话 id> 链接；4：预建 tmp、output
DEFAULT_IMAGE = "ghcr.io/knowwork/knowwork-sandbox:latest"
NETWORK_MODES = ("bridge", "none")


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWWORK_SANDBOX_", extra="ignore", hide_input_in_errors=True)

    docker_enabled: bool = False
    image: str = DEFAULT_IMAGE
    docker_host: str = ""
    docker_tls_cert_path: str = ""
    http_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    pull_timeout_seconds: float = Field(default=600.0, gt=0, le=3600)

    cpus: float = Field(default=2.0, gt=0, le=64)
    memory_bytes: int = Field(default=2 * GIB, ge=64 * MIB)
    pids_limit: int = Field(default=512, ge=16, le=65536)
    network_mode: str = "bridge"
    allow_internet: bool = True
    runtime: str = Field(default="", pattern=r"^([A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$")
    # 命令与文件操作默认的执行账号：root（WeKnora 口径），也可指定 uid[:gid]（镜像里保留 uid 1000 的 user）
    exec_user: str = Field(default="root", pattern=r"^(root|\d{1,10}(:\d{1,10})?)$")

    idle_ttl_seconds: int = Field(default=1800, ge=1)
    sweep_min_interval_seconds: float = Field(default=60.0, ge=0)
    orphan_grace_seconds: float = Field(default=600.0, ge=0)

    exec_default_timeout_seconds: int = Field(default=60, ge=1, le=3600)
    exec_max_timeout_seconds: int = Field(default=600, ge=1, le=3600)
    max_input_bytes: int = Field(default=20 * MIB, ge=1)
    max_write_bytes: int = Field(default=1 * MIB, ge=1)
    max_concurrent_execs: int = Field(default=32, ge=1, le=512)

    @field_validator("network_mode")
    @classmethod
    def _network_mode(cls, value: str) -> str:
        mode = value.strip().lower()
        if mode not in NETWORK_MODES:
            raise ValueError("network_mode 只能是 bridge 或 none（不接受 host、container: 或自定义网络名）")
        return mode

    @field_validator("docker_host")
    @classmethod
    def _docker_host(cls, value: str) -> str:
        return validate_docker_host(value)

    @model_validator(mode="after")
    def _consistent(self) -> "SandboxSettings":
        if self.exec_default_timeout_seconds > self.exec_max_timeout_seconds:
            raise ValueError("exec_default_timeout_seconds 不能大于 exec_max_timeout_seconds")
        validate_remote_tls(self.docker_host, self.docker_tls_cert_path)
        if self.docker_enabled and not self.image.strip():
            raise ValueError("启用 Docker 沙箱时必须配置镜像 KNOWWORK_SANDBOX_IMAGE")
        return self

    @property
    def effective_network_mode(self) -> str:
        return self.network_mode if self.allow_internet else "none"


@dataclass(frozen=True)
class ContainerPosture:
    """决定容器长什么样的全部参数；它的摘要写在容器 label 上，对不上就重建。"""

    image: str
    memory_bytes: int
    nano_cpus: int
    pids_limit: int
    network_mode: str
    runtime: str

    @classmethod
    def of(cls, settings: SandboxSettings) -> "ContainerPosture":
        return cls(image=settings.image.strip(), memory_bytes=settings.memory_bytes,
                   nano_cpus=int(settings.cpus * 1_000_000_000), pids_limit=settings.pids_limit,
                   network_mode=settings.effective_network_mode, runtime=settings.runtime)

    @property
    def digest(self) -> str:
        payload = json.dumps({"v": POSTURE_VERSION, **asdict(self)}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]
