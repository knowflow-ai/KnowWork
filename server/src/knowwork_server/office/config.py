"""Office 容器的配置（环境变量 KNOWWORK_OFFICE_*）。
两种连法：设了 image 就按人起 Office 容器（containers.py，部署时用）；没设就连一个固定的、从源码起的 GenOffice
（本机开发：upstream_mcp_url 加 files_root），所有人共用。在线编辑（扩展）另有自己的配置，同一个前缀。
"""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class OfficeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWWORK_OFFICE_", extra="ignore", populate_by_name=True)

    # 按人起的 Office 容器（镜像见 deploy/docker/office）。不设就用下面的固定 GenOffice（本机开发）
    image: str | None = None
    # 容器接在哪个 Docker 网络（业务服务也在里面，按容器地址连）；不设时把端口发布到 127.0.0.1（本机开发）
    network: str | None = None
    docker_host: str = ""  # 空 = 本机 Docker；格式同 KNOWWORK_SANDBOX_DOCKER_HOST
    docker_tls_cert_path: str = ""
    # 一个人的 Office 容器：空闲约 250MB，开着文档、转大文件时上去，导出 PDF 还要另起隐藏窗口
    memory_mb: int = Field(default=2048, ge=512, le=65536)
    shm_mb: int = Field(default=512, ge=64, le=8192)  # Chromium 用 /dev/shm，Docker 默认的 64MB 不够
    cpus: float = Field(default=2.0, gt=0, le=64)
    idle_minutes: int = Field(default=30, ge=1, le=24 * 60)  # 多久没用就删掉（文件在业务服务存着，不丢）
    # 同时在跑的容器最多几个，到了上限新来的人要等。部署里早先叫 KNOWWORK_OFFICE_MAX_EDITORS，照样认
    max_containers: int = Field(default=20, ge=1, le=10000, validation_alias=AliasChoices(
        "KNOWWORK_OFFICE_MAX_CONTAINERS", "KNOWWORK_OFFICE_MAX_EDITORS", "max_containers"))
    # 本机开发：固定 GenOffice 的 MCP 地址（它自带的，本机不带门卫）与放文件的目录（files_root/<对话>/...，
    # GenOffice 与业务服务在同一台机器上，看到的是同一个路径）
    upstream_mcp_url: str | None = None
    files_root: Path | None = None

    @property
    def per_user(self) -> bool:
        return bool(self.image)

    @property
    def local(self) -> bool:
        return not self.per_user and bool(self.upstream_mcp_url and self.files_root)
