"""worker 进程：跑所有后台任务（通用任务框架 jobs）。

以前这里是空壳，附件解析的消费者从没被启动过，需要 MinerU 解析的附件一直排着。现在：
- 附件解析（attachment.parse）注册进任务执行器；按授权启用的扩展经 setup_worker 注册自己的任务类型；
- 一条连接 LISTEN 任务通知，新任务入队立即领取；没有通知时按 KNOWWORK_JOB_IDLE_SECONDS 轮询兜底；
- 解析结果要放进会话的沙箱容器，所以 worker 和 api 一样要连 Docker（compose 的沙箱叠加文件给两者都挂了）；
- SIGTERM 后不再领取，手上的任务在宽限期内退回排队（KNOWWORK_JOB_GRACE_SECONDS）；
- 每 15 秒写一次心跳（jobs/heartbeat.py），系统设置里的运行面板读它。
用法：`python -m knowwork_server.ops.worker`
"""

import asyncio
import contextlib
import logging
import signal
from dataclasses import dataclass

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from ..access.members import DEFAULT_WORKSPACE_ID
from ..attachments import Attachments, build_attachments
from ..config import Settings, get_settings
from ..db.base import make_engine, make_sessionmaker, make_workspace_sessionmaker
from ..docparse import DocumentParsers
from ..extensions import Extensions, WorkerContext, installed_extensions
from ..jobs import JobRunner, listen
from ..jobs.gate import build_gate
from ..jobs.heartbeat import Heartbeat
from ..jobs.settings import JobSettings
from ..models import ModelClient, catalog_for_worker
from ..sandbox import SandboxModule, SandboxSettings, build_sandbox
from ..sandbox.unavailable import UnavailableSandbox
from ..storage import namespaced_stores, session_stores

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Worker:
    engine: AsyncEngine
    runner: JobRunner
    attachments: Attachments
    sandbox: SandboxModule | None
    settings: JobSettings
    extensions: Extensions
    http: httpx.AsyncClient  # 内部模型客户端用
    heartbeat: Heartbeat
    redis: Redis | None = None  # 闸门用的 Redis 客户端，关停时关掉

    async def serve(self, stop: asyncio.Event) -> None:
        listener = asyncio.create_task(listen(self.engine, self.runner.on_notify, stop))
        # 心跳在手上的任务退完（宽限期）之后才停：停机过程中面板上照样看得到这个 worker 在跑什么
        drained = asyncio.Event()
        beating = asyncio.create_task(self.heartbeat.run(drained))
        try:
            # 开源版只有默认空间；多空间时这里换成从工作空间表读
            await self.runner.run_forever(lambda: [DEFAULT_WORKSPACE_ID], stop,
                                          idle_seconds=self.settings.job_idle_seconds,
                                          grace_seconds=self.settings.job_grace_seconds)
        finally:
            stop.set()
            drained.set()
            await asyncio.gather(listener, beating, return_exceptions=True)

    async def aclose(self) -> None:
        for extension in self.extensions.active:
            try:
                await extension.aclose()
            except Exception:  # 一个扩展关不干净不影响别的资源释放
                log.exception("closing extension %s failed", extension.name)
        await self.attachments.aclose()
        await self.http.aclose()
        if self.redis is not None:
            await self.redis.aclose()
        if self.sandbox is not None:
            await self.sandbox.aclose()
        await self.engine.dispose()


def build_worker(settings: Settings, jobs: JobSettings | None = None, *,
                 extensions: Extensions | None = None) -> Worker:
    jobs = jobs or JobSettings()
    engine = make_engine(settings.database_url)
    scoped = make_workspace_sessionmaker(engine, DEFAULT_WORKSPACE_ID)
    sandbox = build_sandbox(SandboxSettings())
    stores = session_stores(settings)
    session_sandbox = sandbox.sandbox if sandbox else UnavailableSandbox()
    gate, redis = build_gate(jobs.redis_url)
    # 文档解析服务的配置在管理后台里改，worker 每次解析前取（几秒缓存），不用重启
    parsers = DocumentParsers(make_sessionmaker(engine), settings.master_key.get_secret_value())
    attachments = build_attachments(scoped, stores, session_sandbox, parsers=parsers, gate=gate)
    if sandbox is not None:
        sandbox.on_created(attachments.service.restage)  # 这边新建了会话容器，也把已就绪的附件放进去
    runner = JobRunner(scoped, pools=jobs.pools)
    attachments.parse_job.register(runner)
    http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=120.0))
    models = ModelClient(catalog_for_worker(scoped, settings.master_key.get_secret_value()), http)
    context = WorkerContext(settings=settings, engine=engine, sessionmaker=scoped, runner=runner, stores=stores,
                            sandbox=session_sandbox, models=models, gate=gate, mineru=attachments.mineru,
                            stores_for=lambda namespace: namespaced_stores(settings, namespace))
    extensions = _setup_extensions(extensions or installed_extensions(), context)
    heartbeat = Heartbeat(scoped, runner, gate, version=settings.version)
    return Worker(engine=engine, runner=runner, attachments=attachments, sandbox=sandbox, settings=jobs,
                  extensions=extensions, http=http, heartbeat=heartbeat, redis=redis)


def _setup_extensions(extensions: Extensions, context: WorkerContext) -> Extensions:
    """逐个注册启用的扩展的任务；某个扩展报错只去掉它，附件解析与别的扩展照常。"""
    for extension in extensions.active:
        try:
            extension.setup_worker(context)
        except Exception:
            log.exception("setting up extension %s in worker failed", extension.name)
            extensions = extensions.without(extension, "扩展装配失败，详情见 worker 日志")
    return extensions


async def run(stop: asyncio.Event | None = None) -> None:
    settings = get_settings()  # 配置不完整立即失败，不带病运行
    stop = stop or asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stop.set)
    worker = build_worker(settings)
    log.info("worker started")
    try:
        await worker.serve(stop)
    finally:
        await worker.aclose()
    log.info("worker stopped")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())


if __name__ == "__main__":
    main()
