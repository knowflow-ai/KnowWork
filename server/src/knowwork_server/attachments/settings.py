"""附件配置（环境变量前缀 KNOWWORK_，与主配置同前缀，单独成类以免和其他功能线的配置互相牵扯）。"""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIB = 1024 * 1024


class AttachmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWWORK_", extra="ignore", frozen=True)

    # MinerU 3.x 的 API 地址，例如 http://mineru:8000；为空即「未配置文档解析服务」
    mineru_base_url: str = ""
    # VLM 推理服务地址（sglang / vllm，常见是 30000 端口），MinerU 的 vlm、hybrid 后端去访问它；由 MinerU 那台机器访问，
    # 和 MinerU 装在一起时常写 http://127.0.0.1:30000。不配时要它的后端退回 pipeline
    mineru_server_url: str = ""
    mineru_backend: str = "pipeline"
    mineru_lang: str = "ch"
    mineru_request_timeout_seconds: float = Field(default=60.0, gt=0, le=600)
    # 一次解析（提交 + 轮询 + 取结果）的总时限；超过按「服务超时」处理，可重试
    mineru_parse_timeout_seconds: float = Field(default=600.0, gt=0, le=6 * 3600)
    mineru_poll_interval_seconds: float = Field(default=2.0, gt=0, le=60)
    # MinerU 同时最多接几个解析：所有 worker 进程共用这个名额（附件与知识库一起算），按 MinerU 的 max_concurrent_requests 设
    mineru_max_concurrency: int = Field(default=3, ge=1, le=64)

    attachment_max_bytes: int = Field(default=20 * MIB, gt=0, le=512 * MIB)
    attachment_max_files_per_request: int = Field(default=10, ge=1, le=100)
    # 总尝试次数：3 = 首次 + 服务超时时最多重试 2 次
    attachment_parse_max_attempts: int = Field(default=3, ge=1, le=10)
    attachment_parse_retry_delay_seconds: float = Field(default=30.0, ge=0, le=3600)
    # 解析任务的租约：执行中每 30 秒续一次，worker 崩溃后最多这么久被别的 worker 接手
    attachment_parse_lease_seconds: float = Field(default=120.0, gt=0, le=24 * 3600)

    @field_validator("mineru_base_url", "mineru_server_url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("MinerU 与 VLM 服务的地址必须是 http(s) URL")
        return value

    @property
    def mineru_configured(self) -> bool:
        return bool(self.mineru_base_url)
