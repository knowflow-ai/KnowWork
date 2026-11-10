"""独立的模型网关进程（ops/model_gateway.py）：和业务服务认同一把令牌签名器，按模型目录转发；
业务服务那边改了模型，经数据库通知马上作废这边的快照，不用等 TTL。"""

import asyncio
import secrets

import httpx
import pytest

from knowwork_server.access.members import PLATFORM_WORKSPACE_ID
from knowwork_server.config import Settings
from knowwork_server.db.base import make_engine, make_sessionmaker
from knowwork_server.db.migrate import upgrade_to_head
from knowwork_server.gateway.model_gateway import MODEL_AUDIENCE
from knowwork_server.kernel.tokens import host_callback_signer
from knowwork_server.models.crypto import SecretBox
from knowwork_server.models.schemas import ModelCreate, ModelUpdate, ProviderCreate
from knowwork_server.models.service import ModelCatalog
from knowwork_server.ops.model_gateway import build_app

from .conftest import anyio_backend, app_db, database_url, fresh_url  # noqa: F401
from .support import Served, UpstreamStub, free_port

pytestmark = pytest.mark.anyio
MESSAGES = [{"role": "user", "content": "你好"}]


class Admin:
    def check(self, *args) -> bool:
        return True

    def is_system_admin(self, user_id: str) -> bool:
        return True


async def _ask(client: httpx.AsyncClient, token: str) -> httpx.Response:
    return await client.post("/internal/model/v1/chat/completions", headers={"authorization": f"Bearer {token}"},
                             json={"model": "default", "messages": MESSAGES, "stream": True})


async def test_forwards_with_the_api_token_and_follows_catalog_changes(app_db):  # noqa: F811
    await upgrade_to_head(app_db.owner_url, app_role=app_db.role)
    master_key, upstream_key = secrets.token_hex(32), "sk-up-" + secrets.token_hex(4)
    stub, port = UpstreamStub(["你好呀"], upstream_key), free_port()
    settings = Settings(database_url=app_db.app_url, master_key=master_key, host_token="h" * 32,
                        model_upstream_base_url=f"http://127.0.0.1:{port}/v1", model_upstream_api_key=upstream_key,
                        model_upstream_model="fixed-model")
    # 令牌由业务服务签发（app.py 用同一个 host_callback_signer）
    token = host_callback_signer(master_key).issue(audience=MODEL_AUDIENCE, workspace_id=PLATFORM_WORKSPACE_ID,
                                                   ttl_seconds=600)
    app = build_app(settings)
    engine = make_engine(app_db.app_url)
    writer = ModelCatalog(make_sessionmaker(engine), Admin(), SecretBox.from_master_key(master_key))
    async with Served(stub.app(), port), app.router.lifespan_context(app), \
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gateway") as client:
        assert (await client.get("/health")).json() == {"ok": True}
        assert (await client.get("/ready")).status_code == 200
        assert (await _ask(client, "not-a-token")).status_code == 401

        answer = await _ask(client, token)  # 没配模型：回退到固定上游
        assert answer.status_code == 200 and answer.content.rstrip().endswith(b"data: [DONE]")
        assert stub.requests[-1]["body"]["model"] == "fixed-model"

        provider = await writer.create_provider("root", ProviderCreate(
            name="P", kind="openai_compatible", base_url=f"http://127.0.0.1:{port}/v1", api_key=upstream_key))
        model = await writer.create_model("root", ModelCreate(provider_id=provider.id, alias="default", name="m-1"))
        await _eventually(client, token, stub, "m-1")
        await writer.update_model("root", model.id, ModelUpdate(name="m-2"))  # 业务服务那边改了，这边马上跟上
        await _eventually(client, token, stub, "m-2")
    await engine.dispose()


async def _eventually(client: httpx.AsyncClient, token: str, stub: UpstreamStub, upstream_model: str) -> None:
    """通知异步送达：最多等 3 秒（远小于快照的 10 秒 TTL，等到了就说明是通知作废的）。"""
    for _ in range(30):
        response = await _ask(client, token)
        assert response.status_code == 200
        if stub.requests[-1]["body"]["model"] == upstream_model:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"网关一直没换到 {upstream_model}")
