"""后台任务配置（环境变量前缀 KNOWWORK_）。各池的执行槽数改了要重启 worker 才生效。"""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class JobSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="KNOWWORK_", extra="ignore", frozen=True)

    # 池硬隔离，互不抢占：core 文档与附件解析，enrichment 图片描述等富化，maintenance 同步、导出导入、批量操作。
    # 下游另有闸门（解析服务、模型、沙箱），下游满了时加执行槽没用。
    job_core_slots: int = Field(default=2, ge=1, le=64)
    job_enrichment_slots: int = Field(default=2, ge=1, le=64)
    job_maintenance_slots: int = Field(default=1, ge=1, le=64)
    # 没有 NOTIFY 时多久轮询一次；NOTIFY 正常时新任务几乎立即被领取
    job_idle_seconds: float = Field(default=5.0, gt=0, le=300)
    # 停机宽限期：期间手上的任务到检查点就退回排队，过了直接取消并退回
    job_grace_seconds: float = Field(default=60.0, ge=0, le=3600)
    # 下游并发闸门（jobs/gate.py）用的 Redis；为空时闸门只在进程内生效（单个 worker 时一样够用）
    redis_url: str = ""

    @property
    def pools(self) -> dict[str, int]:
        return {"core": self.job_core_slots, "enrichment": self.job_enrichment_slots,
                "maintenance": self.job_maintenance_slots}
