"""存活与就绪探针。

- `/health`：进程能响应就算活着，不碰任何依赖（给容器重启策略用，依赖抖动不该触发重启）；
- `/ready`：数据库与运行时宿主都通才算就绪（给安装脚本与网关用）。任一检查失败或超时即 503，
  响应只给每项 ok / fail，具体原因只进日志，不返回调用方。
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 3.0
OK = "ok"
FAIL = "fail"


@dataclass(frozen=True)
class Check:
    name: str
    probe: Callable[[], Awaitable[bool]]

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("检查项需要名字")


def database_check(engine: AsyncEngine) -> Check:
    async def probe() -> bool:
        async with engine.connect() as connection:
            return (await connection.scalar(text("SELECT 1"))) == 1
    return Check("database", probe)


def host_check(client: httpx.AsyncClient) -> Check:
    """宿主 `/health` 返回 `{"ok": true}` 才算通；client 的 base_url 指向宿主。"""
    async def probe() -> bool:
        response = await client.get("/health")
        if response.status_code != 200:
            return False
        payload = response.json()
        return isinstance(payload, dict) and payload.get("ok") is True
    return Check("host", probe)


def host_dsh_version(client: httpx.AsyncClient):
    """宿主 `/health` 里带的 DSH 版本号（系统信息页用）；取不到返回 None。"""
    async def read() -> str | None:
        response = await client.get("/health")
        payload = response.json() if response.status_code == 200 else None
        version = payload.get("dsh") if isinstance(payload, dict) else None
        return version if isinstance(version, str) else None
    return read


async def _run_one(check: Check, timeout_seconds: float) -> str:
    try:
        passed = await asyncio.wait_for(check.probe(), timeout=timeout_seconds)
    except Exception:  # 探针失败一律视为未就绪，原因只进日志
        log.warning("readiness check %s failed", check.name, exc_info=True)
        return FAIL
    return OK if passed is True else FAIL


async def run_checks(checks: Sequence[Check], *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, str]:
    results = await asyncio.gather(*(_run_one(check, timeout_seconds) for check in checks))
    return {check.name: result for check, result in zip(checks, results, strict=True)}


def build_router(checks: Sequence[Check], *, timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
                 prefix: str = "") -> APIRouter:
    frozen = tuple(checks)
    names = [check.name for check in frozen]
    if len(set(names)) != len(names):
        raise ValueError("检查项名字重复")
    router = APIRouter(prefix=prefix, tags=["ops"])

    @router.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @router.get("/ready")
    async def ready() -> JSONResponse:
        results = await run_checks(frozen, timeout_seconds=timeout_seconds)
        passed = all(value == OK for value in results.values())
        return JSONResponse({"ready": passed, "checks": results}, status_code=200 if passed else 503)

    return router
