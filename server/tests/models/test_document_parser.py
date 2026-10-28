"""文档解析服务（MinerU）的运行时配置：没配时用环境变量；界面里配了以它为准，停用即「没配」；令牌加密、只回显
末四位；改完缓存失效；测试连接分清连不上、令牌不对、不是 MinerU；接口只给空间管理员；MinerU 客户端按配置换。"""

import httpx
import pytest
from fastapi import FastAPI, HTTPException, Request
from pydantic import ValidationError
from sqlalchemy import select

from knowwork_server.access.members import PLATFORM_WORKSPACE_ID
from knowwork_server.access.rls import set_workspace
from knowwork_server.attachments.mineru import MinerUOptions, MinerUProvider
from knowwork_server.attachments.settings import AttachmentSettings
from knowwork_server.auth.principal import Principal
from knowwork_server.docparse import DocumentParserRow, DocumentParsers, ParserSave, ParserTest, build_router

from .conftest import MASTER_KEY

pytestmark = pytest.mark.anyio
ENV = AttachmentSettings(mineru_base_url="http://env-mineru:8000", mineru_max_concurrency=2)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def openapi(paths=("/tasks", "/file_parse"), status=200, token=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if token and request.headers.get("authorization") != f"Bearer {token}":
            return httpx.Response(401)
        return httpx.Response(status, json={"paths": {p: {} for p in paths}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_env_is_the_default_and_settings_take_over(sessionmaker, access):
    clock = Clock()
    parsers = DocumentParsers(sessionmaker, MASTER_KEY, ENV, clock=clock)
    env = await parsers.current("ws1")
    assert (env.base_url, env.source, env.max_concurrency) == ("http://env-mineru:8000", "env", 2)
    assert (await parsers.view()).source == "env"

    view = await parsers.save("root", ParserSave(base_url="http://mineru:8000/", api_key="secret-token-1234",
                                                      max_concurrency=5, server_url="http://127.0.0.1:30000/"))
    assert view.server_url == "http://127.0.0.1:30000", "VLM 推理服务的地址（MinerU 那台机器看到的）"
    assert (view.source, view.base_url, view.api_key_last4, view.max_concurrency) == (
        "settings", "http://mineru:8000", "1234", 5)
    current = await parsers.current("ws1")
    assert (current.base_url, current.api_key, current.source) == ("http://mineru:8000", "secret-token-1234",
                                                                   "settings"), "保存后马上生效（清了缓存）"
    assert current.server_url == "http://127.0.0.1:30000"
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, PLATFORM_WORKSPACE_ID)
        stored = (await session.scalars(select(DocumentParserRow))).one()
    assert "secret-token" not in stored.api_key_ciphertext, "令牌加密存"

    await parsers.save("root", ParserSave(enabled=False, base_url="http://mineru:8000"))
    assert await parsers.current("ws1") is None, "界面里停用就是没配，不回退到环境变量"
    assert (await parsers.view()).api_key_set, "不传令牌就保持原样"
    await parsers.save("root", ParserSave(base_url="http://mineru:8000", api_key=""))
    assert (await parsers.view()).api_key_set is False, "空字符串清掉令牌"


async def test_nothing_configured_and_cache(sessionmaker, access):
    clock = Clock()
    parsers = DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), clock=clock)
    assert await parsers.current("ws1") is None
    assert (await parsers.view()).source == "none"
    async with sessionmaker() as session, session.begin():  # 绕过 save（例如另一个进程改的）：缓存期内还是旧值
        await set_workspace(session, PLATFORM_WORKSPACE_ID)
        session.add(DocumentParserRow(workspace_id=PLATFORM_WORKSPACE_ID, kind="mineru", enabled=True, base_url="http://other:8000",
                                      lang="ch", max_concurrency=3))
    assert await parsers.current("ws1") is None
    clock.now += 6
    assert (await parsers.current("ws1")).base_url == "http://other:8000", "几秒后别的进程也能看到新配置"


async def test_probe_tells_what_is_wrong(sessionmaker, access):
    async def probe(http, body):
        parsers = DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), http=http)
        return await parsers.probe(body)

    assert (await probe(openapi(), ParserTest(base_url="http://mineru:8000"))).ok
    locked = await probe(openapi(token="t" * 12), ParserTest(base_url="http://mineru:8000"))
    assert (locked.ok, "令牌" in locked.message) == (False, True)
    assert (await probe(openapi(token="t" * 12), ParserTest(base_url="http://mineru:8000", api_key="t" * 12))).ok
    other = await probe(openapi(paths=("/v1/chat/completions",)), ParserTest(base_url="http://llm:8000"))
    assert (other.ok, "MinerU" in other.message) == (False, True)

    def down(request):
        raise httpx.ConnectError("refused")

    unreachable = await probe(httpx.AsyncClient(transport=httpx.MockTransport(down)),
                              ParserTest(base_url="http://nowhere:1"))
    assert (unreachable.ok, "连不上" in unreachable.message) == (False, True)
    with pytest.raises(ValueError, match="http"):
        ParserTest(base_url="mineru:8000")


async def test_provider_follows_the_current_settings(sessionmaker, access):
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url.host), request.headers.get("authorization")))
        return httpx.Response(200, json={"results": {"a.pdf": {"md_content": "# 文"}}})

    clock = Clock()
    parsers = DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), clock=clock)
    provider = MinerUProvider(parsers, transport=httpx.MockTransport(handler))
    assert await provider.get("ws1", MinerUOptions()) is None
    await parsers.save("root", ParserSave(base_url="http://first:8000", api_key="token-abcdefgh"))
    client = await provider.get("ws1", MinerUOptions())
    await client._http.get("/health")
    await parsers.save("root", ParserSave(base_url="http://second:8000"))
    client = await provider.get("ws1", MinerUOptions())
    await client._http.get("/health")
    assert seen == [("first", "Bearer token-abcdefgh"), ("second", "Bearer token-abcdefgh")], \
        "改了地址，下一次就用新地址；没传令牌时沿用原来的"
    hybrid = MinerUOptions(backend="hybrid-http-client", effort="high")
    assert (await provider.get("ws1", hybrid))._options.backend == "pipeline", "没配 VLM 服务时退回 pipeline"
    await parsers.save("root", ParserSave(base_url="http://second:8000", server_url="http://vlm:30000"))
    chosen = (await provider.get("ws1", hybrid))._options
    assert (chosen.backend, chosen.server_url) == ("hybrid-http-client", "http://vlm:30000")
    await provider.aclose()


async def test_parse_mode_can_be_forced_and_vlm_modes_need_the_vlm_server(sessionmaker, access):
    """hybrid 自动判断版面有时效果不好，可以强制整页交给 VLM（vlm-http-client），附件与知识库都按它走；
    要 VLM 服务的模式没填地址时保存就拦住，不让它悄悄退回 pipeline。"""
    with pytest.raises(ValidationError, match="VLM 服务地址"):
        ParserSave(base_url="http://m:8000", backend="vlm-http-client")
    assert ParserSave(base_url="http://m:8000", backend="hybrid-http-client", enabled=False).backend == \
        "hybrid-http-client", "停用时不拦"
    with pytest.raises(ValidationError):
        ParserSave(base_url="http://m:8000", backend="vlm-auto-engine")  # 在 MinerU 进程里跑模型的不开放
    parsers = DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), clock=Clock())
    provider = MinerUProvider(parsers, transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    view = await parsers.save("root", ParserSave(base_url="http://m:8000", server_url="http://vlm:30000",
                                                       backend="vlm-http-client"))
    assert view.backend == (await parsers.current("ws1")).backend == "vlm-http-client"
    library = MinerUOptions(backend="hybrid-http-client", effort="high", image_analysis=True)
    attachment = MinerUOptions()
    for wanted in (library, attachment):
        chosen = (await provider.get("ws1", wanted))._options
        assert (chosen.backend, chosen.server_url) == ("vlm-http-client", "http://vlm:30000")
    await parsers.save("root", ParserSave(base_url="http://m:8000", server_url="http://vlm:30000"))
    assert (await parsers.view()).backend == "auto" and (await parsers.current("ws1")).backend is None
    assert (await provider.get("ws1", library))._options.backend == "hybrid-http-client", "自动：按用途定"
    assert (await provider.get("ws1", attachment))._options.backend == "pipeline"
    await provider.aclose()


async def fake_principal(request: Request) -> Principal:
    user = request.headers.get("x-user")
    if not user:
        raise HTTPException(status_code=401)
    return Principal(user_id=user, auth_method="session", scopes=None)


async def test_one_config_serves_every_workspace(sessionmaker, access):
    """全平台共用一份：系统管理员存一次，哪个空间来取都是它。"""
    parsers = DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), clock=Clock())
    await parsers.save("root", ParserSave(base_url="http://mineru:8000"))
    for workspace_id in ("ws1", "ws2", PLATFORM_WORKSPACE_ID):
        assert (await parsers.current(workspace_id)).base_url == "http://mineru:8000"


async def test_api_is_for_the_system_admin(sessionmaker, access):
    app = FastAPI()
    app.include_router(build_router(DocumentParsers(sessionmaker, MASTER_KEY, AttachmentSettings(), http=openapi()),
                                    access, fake_principal))
    root = "/api/system/document-parser"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
        for user in ("alice", "bob", "dave"):
            assert (await http.get(root, headers={"x-user": user})).status_code == 403, "空间管理员也不行"
        saved = await http.put(root, headers={"x-user": "root"},
                               json={"base_url": "http://mineru:8000", "api_key": "abcdefghijkl", "lang": "ch"})
        assert saved.status_code == 200 and saved.json()["api_key_last4"] == "ijkl"
        assert "abcdefghijkl" not in saved.text, "令牌不回显"
        assert (await http.put(root, headers={"x-user": "root"}, json={"base_url": "ftp://x"})).status_code == 422
        tested = await http.post(f"{root}/test", headers={"x-user": "root"}, json={"base_url": "http://mineru:8000"})
        assert tested.json()["ok"] is True
