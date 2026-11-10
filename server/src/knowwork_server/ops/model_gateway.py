"""模型网关进程：只跑宿主调模型的那个接口（gateway/model_gateway.py），不起事件泵、IM 长连接、调度这些。

为什么单独一个进程：宿主每一步调模型、模型吐出的每一块都经网关转发，原来和事件泵、实时推送挤在业务服务一个进程
（一个事件循环、一个核）里。2026-11-09 压测（模拟模型、100～200 个会话同时发问）：拆出来后业务服务每轮的 CPU
少了一到三成，网关自己每轮 8～14 毫秒；同一台机器上别的负载越重，好处越小（多出来的进程抢不到空闲的核）。

网关不记状态：宿主的令牌用主密钥派生的同一把签名器校验（kernel/tokens.host_callback_signer），模型目录读快照，
所以可以开多个 worker 进程（KNOWWORK_MODEL_GATEWAY_WORKERS）。管理员在业务服务里改了模型，改的事务里 NOTIFY，
这边每个 worker 都 LISTEN 着、收到就作废快照（models/core.follow_changes）；通知断了也最多过一个快照 TTL（10 秒）。
业务服务内部端口上的同一个接口照旧保留，给本机开发与单进程部署用。

用法：`python -m knowwork_server.ops.model_gateway`，宿主的 KNOWWORK_MODEL_BASE_URL 指到
`http://<这里>:8002/internal/model/v1`。
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI

from ..config import Settings, get_settings
from ..db.base import make_engine, make_held_engine, make_sessionmaker
from ..gateway.model_gateway import fixed_upstream, router_from_settings
from ..kernel.tokens import host_callback_signer
from ..models import catalog_for_gateway
from . import logredact
from .health import build_router as health_router
from .health import database_check

# 模型目录读快照，十秒左右才查一次库；连接池不用大
POOL_SIZE, MAX_OVERFLOW = 2, 2


def build_app(settings: Settings) -> FastAPI:
    master_key = settings.master_key.get_secret_value()
    engine = make_engine(settings.database_url, pool_size=POOL_SIZE, max_overflow=MAX_OVERFLOW)
    catalog = catalog_for_gateway(make_sessionmaker(engine), master_key)
    upstream = fixed_upstream(settings)
    routed = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))

    held = make_held_engine(settings.database_url)  # LISTEN 一直占着一条连接，不进连接池

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        follower = asyncio.create_task(catalog.follow_changes(held, stop))
        try:
            yield
        finally:
            stop.set()
            with contextlib.suppress(asyncio.CancelledError):
                await follower
            await upstream.aclose()
            await routed.aclose()
            await engine.dispose()
            await held.dispose()

    app = FastAPI(title="KnowWork model gateway", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.include_router(health_router((database_check(engine),)))
    app.include_router(router_from_settings(settings, host_callback_signer(master_key), catalog,
                                            upstream=upstream, http=routed))
    return app


def create_app() -> FastAPI:
    """给 uvicorn 的工厂：开多个 worker 时每个子进程各调一次，日志配置也要在这里做。"""
    _logging()
    return build_app(get_settings())


def _logging() -> None:
    logging.basicConfig(level=logging.INFO,
                        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":%(message)r}')
    logredact.install()


def main() -> None:
    settings = get_settings()
    # 用导入路径起：开多个 worker 时 uvicorn 要在子进程里重新建应用（python -m 下 __name__ 是 __main__，不能拿它拼）。
    # 事件循环由 uvicorn 选（装了 uvloop 就用）
    uvicorn.run("knowwork_server.ops.model_gateway:create_app", factory=True, host=settings.model_gateway_host,
                port=settings.model_gateway_port, workers=settings.model_gateway_workers, log_config=None)


if __name__ == "__main__":
    main()
