"""运行时装配：检索工具按这个对话本轮的选择收窄——只查选了的知识库，去掉用户撤掉的检索条件。

原来装配知识模块时没把会话配置的绑定交给它，检索工具一直按「本人能查的全部」查：对话里选了知识库，
模型看到的目录收窄了，检索时却不限定（实测）。这里用真实的 build_runtime 验证整条链路。
"""

import json
import secrets

import httpx
import pytest

from knowwork_server.access.members import DEFAULT_WORKSPACE_ID
from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings
from knowwork_server.conversations.models import Conversation
from knowwork_server.db.base import make_workspace_sessionmaker
from knowwork_server.db.migrate import upgrade_to_head
from knowwork_server.knowledge import ProvidedSource
from knowwork_server.knowledge.models import BaseInfo, DocumentInfo, DocumentPage, Evidence, FieldInfo
from knowwork_server.profile.bindings import ProfileBindings
from knowwork_server.profile.snapshot import RuntimeProfile
from knowwork_server.tools.registry import ToolContext

pytestmark = pytest.mark.anyio


class Recording:
    """记下每次被要求查哪些知识库、带了哪些条件的知识源。"""

    source_id = "builtin"
    features = frozenset({"mode", "filters", "tags", "folders", "pins", "document_query"})

    def __init__(self):
        self.requests = []

    async def list_bases(self, caller):
        tags = FieldInfo(name="标签", type="text", multi=True, options=("简报", "制度"))
        return (BaseInfo(id="kb1", name="制度库", path=("财务",), fields=(tags,)), BaseInfo(id="kb2", name="合同库"))

    def base_of(self, doc_ref):
        return doc_ref.split("/", 1)[0]

    async def search(self, caller, request):
        self.requests.append(request)
        return [Evidence(source_id="builtin", kb_id=kb, doc_ref=f"{kb}/d1", span_ref=f"{kb}-s1", title="制度",
                         text="差旅按标准报销") for kb in request.scope.dataset_ids]

    async def read(self, caller, request):
        raise NotImplementedError

    async def list_documents(self, caller, scope, page):
        docs = (DocumentInfo(doc_ref="kb1/d1", title="差旅办法"), DocumentInfo(doc_ref="kb2/d2", title="采购合同"))
        return DocumentPage(items=tuple(d for d in docs if page.query in d.title and self.base_of(d.doc_ref)
                                        in scope.dataset_ids))

    async def probe(self, caller):
        return None


async def test_search_follows_the_turns_selection(fresh_url):
    await upgrade_to_head(fresh_url)
    runtime = build_runtime(Settings(database_url=fresh_url, master_key=secrets.token_hex(32), host_token="h" * 32))
    try:
        assert await runtime.access.reload()
        owner = await runtime.identity.users.create_user(email="owner@example.com", name="所有者",
                                                         password="correct horse battery")
        await runtime.access.ensure_default_workspace(owner.id)
        source = Recording()
        runtime.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))
        async with make_workspace_sessionmaker(runtime.engine, DEFAULT_WORKSPACE_ID)() as session, session.begin():
            session.add(Conversation(id="c1", workspace_id=DEFAULT_WORKSPACE_ID, user_id=owner.id))
        profile = RuntimeProfile(workspace_id=DEFAULT_WORKSPACE_ID, user_id=owner.id, role="owner", member=True,
                                 bound_knowledge=("builtin.kb1",), dropped_conditions=("部门 = 财务部",))
        await ProfileBindings(runtime.global_sessionmaker).bind(DEFAULT_WORKSPACE_ID, "c1", profile)

        search = next(t for t in runtime.knowledge.tools if t.spec.name == "knowledge_search")
        payload = json.loads(await search.handler(ToolContext(DEFAULT_WORKSPACE_ID, "c1", owner.id), {
            "query": "报销标准", "filters": [{"field": "部门", "value": "财务部"}, {"field": "年度", "value": "2025"}]}))
        [request] = source.requests
        assert request.scope.dataset_ids == ("kb1",), "只查本轮选了的知识库"
        assert [(f.field, f.value) for f in request.filters] == [("年度", "2025")], "用户撤掉的条件不交给知识源"
        assert payload["conditions"] == ["年度 = 2025"] and payload["conditions_removed"] == ["部门 = 财务部"]
    finally:
        await runtime.stop()


async def test_mention_menu_lists_what_can_really_be_mentioned(fresh_url):
    """@ 菜单的候选：能查的知识库带目录与标签（能点名的才给标签），按名字找文件带所在库；路由不被会话 id 抢走。"""
    await upgrade_to_head(fresh_url)
    runtime = build_runtime(Settings(database_url=fresh_url, master_key=secrets.token_hex(32), host_token="h" * 32,
                                     cookie_secure=False))
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=create_public_app(runtime)), base_url="http://kw")
    try:
        assert await runtime.access.reload()
        owner = await runtime.identity.users.create_user(email="owner@example.com", name="所有者",
                                                         password="correct horse battery")
        await runtime.access.ensure_default_workspace(owner.id)
        runtime.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=Recording()))
        login = await client.post("/api/auth/login", json={"email": "owner@example.com",
                                                          "password": "correct horse battery"})
        assert login.status_code == 200, login.text
        options = (await client.get("/api/conversations/mention-options")).json()
        assert [(b["id"], b["path"], b["tags"], b["pinnable"]) for b in options["bases"]] == [
            ("builtin.kb1", ["财务"], ["简报", "制度"], True), ("builtin.kb2", [], [], True)]
        assert options["skills"] == []
        found = (await client.get("/api/conversations/mention-options/documents", params={"q": "差旅"})).json()
        assert found["documents"] == [{"id": "kb1/d1", "title": "差旅办法", "base": "builtin.kb1", "base_name": "制度库"}]
        recent = (await client.get("/api/conversations/mention-options/documents")).json()
        assert [d["title"] for d in recent["documents"]] == ["差旅办法", "采购合同"], "没给名字时列最近的文件"
    finally:
        await client.aclose()
        await runtime.stop()
