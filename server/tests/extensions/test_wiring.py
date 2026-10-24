"""扩展接进业务服务与 worker：启用的挂上路由、注册任务；装配报错的只去掉它；系统信息写明授权与各扩展状态。"""

from importlib.metadata import EntryPoint

import httpx
import pytest

from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings
from knowwork_server.extensions import ENTRY_GROUP, discover
from knowwork_server.ops.worker import build_worker
from knowwork_server.storage import namespaced_stores
from knowwork_server.system.info import ExtensionInfo, SystemInfoService

from .test_loader import DEMO, found, verifier

pytestmark = pytest.mark.anyio
BROKEN = EntryPoint("broken", "tests.extensions.demo:BROKEN", ENTRY_GROUP)
SETTINGS = Settings(master_key="m" * 32, host_token="h" * 32)


async def no_dsh():
    return None


async def test_enabled_extension_routes_are_mounted_and_broken_ones_dropped():
    # broken 的授权模块也是 demo，加载时一样启用，到装配路由时报错才去掉
    runtime = build_runtime(SETTINGS, extensions=discover(found(DEMO, BROKEN, verifier("granting_demo"))))
    try:
        assert [e.name for e in runtime.extensions.active] == ["demo"]
        assert [(s.name, s.reason) for s in runtime.extensions.skipped] == [
            ("broken", "扩展装配失败，详情见业务服务日志")]
        app = create_public_app(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
            assert (await http.get("/api/demo/ping")).json() == {"pong": True}
        context = runtime.extensions.active[0].contexts[-1]
        assert context.principal("knowledge") is not None and context.access is runtime.access
    finally:
        await runtime.stop()
    assert runtime.extensions.active[0].closed


async def test_unlicensed_extension_has_no_routes():
    runtime = build_runtime(SETTINGS, extensions=discover(found(DEMO)))
    try:
        app = create_public_app(runtime)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
            assert (await http.get("/api/demo/ping")).status_code == 404
    finally:
        await runtime.stop()


async def test_worker_registers_jobs_of_enabled_extensions():
    worker = build_worker(SETTINGS, extensions=discover(found(DEMO, verifier("granting_demo"))))
    try:
        assert {kind.name for kind in worker.runner.kinds} == {"attachment.parse", "demo.ping"}
    finally:
        await worker.aclose()
    plain = build_worker(SETTINGS, extensions=discover(found(DEMO)))
    try:
        assert {kind.name for kind in plain.runner.kinds} == {"attachment.parse"}
    finally:
        await plain.aclose()


async def test_system_info_reports_license_and_extensions(sessionmaker):
    extensions = discover(found(DEMO, verifier("granting_other")))
    info = await SystemInfoService("0.0.1", sessionmaker, no_dsh, extensions).info()
    assert info.license == "授权有效"
    assert info.extensions == (ExtensionInfo("demo", False, "授权里没有「demo」模块"),)


def test_extensions_get_their_own_storage_namespaces(tmp_path):
    settings = Settings(master_key="m" * 32, host_token="h" * 32, blob_dir=str(tmp_path))
    assert namespaced_stores(settings, "library")("ws1", "kb_1") is not None
    for taken in ("session-files", "sha256", "Bad", "x", "../up"):
        with pytest.raises(ValueError):
            namespaced_stores(settings, taken)
