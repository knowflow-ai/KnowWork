"""/health 与 /ready：存活只看进程，就绪要数据库与宿主都通；失败不泄露内部原因。"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.db.base import make_engine
from knowwork_server.ops.health import Check, build_router, database_check, host_check, run_checks

pytestmark = pytest.mark.anyio


def _ok() -> Check:
    async def probe() -> bool:
        return True
    return Check("ok", probe)


def _named(name: str, result=None, error: Exception | None = None, delay: float = 0.0) -> Check:
    async def probe() -> bool:
        await asyncio.sleep(delay)
        if error is not None:
            raise error
        return bool(result)
    return Check(name, probe)


async def _get(app: FastAPI, path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path)


def _app(*checks: Check, timeout: float = 1.0) -> FastAPI:
    app = FastAPI()
    app.include_router(build_router(checks, timeout_seconds=timeout))
    return app


async def test_health_always_ok_even_if_dependencies_down():
    response = await _get(_app(_named("database", result=False)), "/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


async def test_ready_when_all_checks_pass():
    response = await _get(_app(_ok(), _named("host", result=True)), "/ready")
    assert response.status_code == 200
    assert response.json() == {"ready": True, "checks": {"ok": "ok", "host": "ok"}}


async def test_not_ready_hides_error_details():
    app = _app(_named("database", error=RuntimeError("password=secret at 10.0.0.3")), _named("host", result=True))
    response = await _get(app, "/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "checks": {"database": "fail", "host": "ok"}}
    assert "secret" not in response.text


async def test_slow_check_times_out():
    response = await _get(_app(_named("host", result=True, delay=2.0), timeout=0.05), "/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["host"] == "fail"


async def test_router_prefix():
    app = FastAPI()
    app.include_router(build_router((_ok(),), prefix="/api"))
    assert (await _get(app, "/api/ready")).status_code == 200


def test_rejects_duplicate_or_empty_names():
    with pytest.raises(ValueError):
        build_router((_ok(), _ok()))
    with pytest.raises(ValueError):
        Check("", _ok().probe)


async def test_run_checks_runs_concurrently():
    checks = tuple(_named(f"c{i}", result=True, delay=0.2) for i in range(5))
    loop = asyncio.get_running_loop()
    started = loop.time()
    results = await run_checks(checks, timeout_seconds=1.0)
    assert loop.time() - started < 0.6
    assert set(results.values()) == {"ok"}


async def test_database_check_against_real_postgres(database_url):
    engine = make_engine(database_url)
    try:
        assert await database_check(engine).probe() is True
    finally:
        await engine.dispose()


async def test_database_check_unreachable():
    engine = make_engine("postgresql+asyncpg://nobody@127.0.0.1:1/none")
    try:
        with pytest.raises(OSError):
            await database_check(engine).probe()
    finally:
        await engine.dispose()


def _host_transport(status: int, payload) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(status, json=payload)
    return httpx.MockTransport(handler)


@pytest.mark.parametrize(("status", "payload", "expected"), [
    (200, {"ok": True, "epoch": "e"}, True),
    (200, {"ok": False}, False),
    (200, ["ok"], False),
    (503, {"ok": True}, False),
])
async def test_host_check(status, payload, expected):
    async with httpx.AsyncClient(transport=_host_transport(status, payload), base_url="http://host") as client:
        assert await host_check(client).probe() is expected
