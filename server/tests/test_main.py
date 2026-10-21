"""生产入口：一个进程起公开与内部两个端口，共享运行时，收到停止信号后一起退出。"""

import asyncio
import secrets

import httpx
import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route

from knowwork_server.__main__ import serve
from knowwork_server.config import Settings

from .support import Served, free_port

pytestmark = pytest.mark.anyio


def stub_host(rotations: list) -> Starlette:
    async def credential(request):
        rotations.append((await request.json())["token"])
        return JSONResponse({"rotated": True})

    return Starlette(routes=[Route("/v1/model-credential", credential, methods=["PUT"])])


async def test_serve_runs_both_ports_and_stops(database_url):
    rotations: list[str] = []
    host_port, public_port, internal_port = free_port(), free_port(), free_port()
    settings = Settings(database_url=database_url, master_key=secrets.token_hex(32), create_schema=True,
                        host_base_url=f"http://127.0.0.1:{host_port}", host_token="h" * 32,
                        model_upstream_api_key="sk-x",
                        public_host="127.0.0.1", public_port=public_port,
                        internal_host="127.0.0.1", internal_port=internal_port)
    stop = asyncio.Event()
    async with Served(stub_host(rotations), host_port):
        task = asyncio.create_task(serve(settings, stop))
        async with httpx.AsyncClient() as client:
            async with asyncio.timeout(15):
                while True:
                    try:
                        health = await client.get(f"http://127.0.0.1:{public_port}/health")
                        break
                    except httpx.ConnectError:
                        await asyncio.sleep(0.05)
            internal = await client.post(f"http://127.0.0.1:{internal_port}/internal/model/v1/chat/completions",
                                         json={})
            public_has_no_gateway = await client.post(
                f"http://127.0.0.1:{public_port}/internal/model/v1/chat/completions", json={})
        stop.set()
        await asyncio.wait_for(task, 15)
    assert health.json() == {"ok": True}
    assert internal.status_code == 401, "内部端口应挂着模型网关"
    assert public_has_no_gateway.status_code == 404, "公开端口不能暴露模型网关"
    assert len(rotations) == 1, "启动时应先下发一次模型令牌"


async def test_starts_without_fixed_upstream_and_disables_fallback():
    """没配固定上游密钥也能启动：模型全部来自模型目录，网关不回退，也不带空的鉴权头。"""
    from knowwork_server.app import build_runtime

    runtime = build_runtime(Settings(master_key="m" * 32, host_token="h" * 32))
    try:
        assert "authorization" not in runtime.upstream.headers
    finally:
        await runtime.stop()
