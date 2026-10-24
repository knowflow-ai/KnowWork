"""对外检索：REST 的列出知识库、检索、阅读，错误给中文原因；MCP 服务端每个请求都现场判身份（不带凭证连工具列表都拿不到），
三个工具和 REST 给的是同一份结果。"""

import json
from dataclasses import dataclass

import httpx
import mcp.types
import pytest
from fastapi import FastAPI, HTTPException, Request
from starlette.routing import Route

from knowwork_server.knowledge.links import LinkSigner
from knowwork_server.knowledge.mcp_server import PATH, KnowledgeMcp
from knowwork_server.knowledge.reader import KnowledgeReader
from knowwork_server.knowledge.reader_api import build_router
from knowwork_server.knowledge.schemas import SourceCreate

from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@dataclass(frozen=True)
class Actor:
    user_id: str
    workspace_id: str


def actor_from_header():
    """测试用的身份：x-user 头当作 API Key 校验通过后的用户。"""

    async def dependency(request: Request) -> Actor:
        user = request.headers.get("x-user")
        if not user:
            raise HTTPException(status_code=401, detail="需要带 knowledge:read 范围的 API Key")
        return Actor(user, "ws1")

    return dependency


@pytest.fixture
async def served(env):
    body = SourceCreate(name="合同库", base_url="http://kf.local", service_key=SERVICE_KEY,
                        config={"auth_mode": "assertion", "dataset_ids": ["ds1", "ds2"]})
    source = await env.knowledge.admin.create("alice", "ws1", body)
    reader = KnowledgeReader(env.knowledge.service, LinkSigner(b"k" * 32))
    app = FastAPI()
    app.include_router(build_router(reader, actor_from_header()))
    server = KnowledgeMcp(reader, actor_from_header())
    app.router.routes.append(Route(PATH, endpoint=server, methods=["GET", "POST", "DELETE"]))
    await server.start()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as client:
        yield client, source
    await server.stop()


async def test_rest_lists_searches_and_reads(served):
    client, source = served
    bases = (await client.get("/api/v1/knowledge-bases", headers={"x-user": "alice"})).json()
    assert {b["id"] for b in bases["knowledge_bases"]} == {f"{source.id}.ds1", f"{source.id}.ds2"}
    assert all(b["filterable"] is False and "fields" not in b for b in bases["knowledge_bases"])
    found = (await client.post("/api/v1/knowledge/search", json={"query": "条款"}, headers={"x-user": "bob"})).json()
    assert {r["doc_ref"] for r in found["results"]} == {"ds1/contract"}, "按调用者的授权收窄"
    first = found["results"][0]
    assert first["knowledge_base"] == f"{source.id}.ds1" and first["locator"]["page"] == 1 and first["span_ref"]
    read = (await client.post("/api/v1/knowledge/read", headers={"x-user": "alice"}, json={
        "source_id": source.id, "doc_ref": "ds1/contract", "max_chars": 100})).json()
    assert read["title"] == "contract.pdf" and read["passages"] and read["next_cursor"]


async def test_rest_errors_are_explained(served):
    client, _ = served
    assert (await client.get("/api/v1/knowledge-bases")).status_code == 401
    bad = await client.post("/api/v1/knowledge/search", json={"query": "  "}, headers={"x-user": "alice"})
    assert bad.status_code == 422 and "检索词长度" in bad.json()["detail"]
    nobody = await client.post("/api/v1/knowledge/search", json={"query": "条款"}, headers={"x-user": "erin"})
    assert nobody.status_code >= 400 and nobody.json()["detail"]


async def rpc(client, method, params=None, *, user="alice", rid=1):
    headers = {**MCP_HEADERS, **({"x-user": user} if user else {})}
    response = await client.post(PATH, headers=headers,
                                 json={"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    return response


async def test_mcp_checks_identity_on_every_request(served):
    client, _ = served
    denied = await rpc(client, "tools/list", user=None)
    assert denied.status_code == 401, "不带凭证连工具列表都拿不到"
    init = await rpc(client, "initialize", {"protocolVersion": mcp.types.LATEST_PROTOCOL_VERSION, "capabilities": {},
                                            "clientInfo": {"name": "测试", "version": "1"}})
    assert init.status_code == 200 and init.json()["result"]["serverInfo"]["name"] == "KnowWork 知识检索"
    listed = (await rpc(client, "tools/list", rid=2)).json()["result"]["tools"]
    assert {t["name"] for t in listed} == {"knowledge_bases", "knowledge_search", "knowledge_read"}


async def test_mcp_tools_return_the_same_results_as_rest(served):
    client, source = served
    called = (await rpc(client, "tools/call", {"name": "knowledge_search", "arguments": {"query": "条款"}},
                        user="bob", rid=3)).json()["result"]
    payload = json.loads(called["content"][0]["text"])
    assert {r["doc_ref"] for r in payload["results"]} == {"ds1/contract"}
    bases = json.loads((await rpc(client, "tools/call", {"name": "knowledge_bases", "arguments": {}},
                                  rid=4)).json()["result"]["content"][0]["text"])
    assert len(bases["knowledge_bases"]) == 2
    failed = (await rpc(client, "tools/call", {"name": "knowledge_search", "arguments": {"query": " "}},
                        rid=5)).json()["result"]
    assert failed["isError"] and "检索词长度" in failed["content"][0]["text"]
    assert source.id
