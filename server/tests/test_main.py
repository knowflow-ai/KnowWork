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


async def _serve_once(url: str, **overrides) -> dict:
    """起生产入口、等公开端口通、调一下两个端口、再停掉；返回各处的结果。"""
    rotations: list[str] = []
    host_port, public_port, internal_port = free_port(), free_port(), free_port()
    settings = Settings(database_url=url, master_key=secrets.token_hex(32), create_schema=True,
                        host_base_url=f"http://127.0.0.1:{host_port}", host_token="h" * 32,
                        model_upstream_api_key="sk-x",
                        public_host="127.0.0.1", public_port=public_port,
                        internal_host="127.0.0.1", internal_port=internal_port, **overrides)
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
    return {"health": health, "internal": internal, "public_has_no_gateway": public_has_no_gateway,
            "rotations": rotations}


# 用全新的空库：共用测试库里有渠道测试留下的、启用着的企业微信渠道，生产入口会按真实平台去连它们（连到外网）
async def test_serve_runs_both_ports_and_stops(fresh_url):
    result = await _serve_once(fresh_url)
    assert result["health"].json() == {"ok": True}
    assert result["internal"].status_code == 401, "内部端口应挂着模型网关"
    assert result["public_has_no_gateway"].status_code == 404, "公开端口不能暴露模型网关"
    assert len(result["rotations"]) == 1, "启动时应先下发一次模型令牌"


async def test_long_held_connections_stay_out_of_the_request_pool(fresh_url):
    """权限规则的 LISTEN、按机器人的 advisory 锁一直占着连接，不能从处理请求的连接池里拿：池子只有 1 条也要能起、能停。
    以前它们都占请求池（默认 5 + 10 = 15 条），启用 14 个机器人再加 LISTEN 就占满，启动时写机器人状态等不到连接，
    公开端口一直不开（2026-11-03 发 v0.0.5 时 CI 撞上）。"""
    result = await _serve_once(fresh_url, db_pool_size=1, db_max_overflow=0)
    assert result["health"].json() == {"ok": True}


async def test_serve_stops_by_itself_when_the_license_ends(fresh_url, monkeypatch):
    """授权运行中到期、没续上：不等停止信号，两个端口自己停下，返回 True（main 据此以 EXIT_LICENSE_ENDED 退出）。"""
    import knowwork_server.__main__ as entry

    async def license_ends(extensions, stop):
        await asyncio.sleep(0.3)
        stop.set()
        return True

    monkeypatch.setattr(entry, "stop_when_license_ends", license_ends)
    host_port, public_port, internal_port = free_port(), free_port(), free_port()
    settings = Settings(database_url=fresh_url, master_key=secrets.token_hex(32), create_schema=True,
                        host_base_url=f"http://127.0.0.1:{host_port}", host_token="h" * 32,
                        model_upstream_api_key="sk-x", public_host="127.0.0.1", public_port=public_port,
                        internal_host="127.0.0.1", internal_port=internal_port)
    async with Served(stub_host([]), host_port):
        assert await asyncio.wait_for(serve(settings, asyncio.Event()), 20) is True
        async with httpx.AsyncClient(trust_env=False) as client:  # 本机设了代理时会由代理回话，看不出端口已经关了
            with pytest.raises(httpx.ConnectError):
                await client.get(f"http://127.0.0.1:{public_port}/health")


def test_main_exits_with_the_license_code_when_the_license_ends(monkeypatch):
    import knowwork_server.__main__ as entry
    from knowwork_server.extensions.expiry import EXIT_LICENSE_ENDED

    async def ended(settings, stop):
        return True

    monkeypatch.setattr(entry, "serve", ended)
    monkeypatch.setattr(entry, "get_settings", lambda: None)
    with pytest.raises(SystemExit) as raised:
        entry.main()
    assert raised.value.code == EXIT_LICENSE_ENDED


async def test_starts_without_fixed_upstream_and_disables_fallback():
    """没配固定上游密钥也能启动：模型全部来自模型目录，网关不回退，也不带空的鉴权头。"""
    from knowwork_server.app import build_runtime

    runtime = build_runtime(Settings(master_key="m" * 32, host_token="h" * 32))
    try:
        assert "authorization" not in runtime.upstream.headers
    finally:
        await runtime.stop()
