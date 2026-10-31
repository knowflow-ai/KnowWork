"""能按次打开的检索方式（多跳、视觉）：知识源在知识库上标出哪些能开，目录与 knowledge_bases 照实给模型；
knowledge_search 带不带 multi_hop、visual 参数在会话开始时按这个会话能查的库定（没有能开的库就不带）；
参数原样交给知识源，不认的知识源照常检索、不另外说明（它们只多加召回，不收窄范围）。"""

import json

import pytest

from knowwork_server.knowledge import ProvidedSource
from knowwork_server.knowledge.catalog import turn_catalog
from knowwork_server.knowledge.models import BaseInfo, Evidence
from knowwork_server.knowledge.schemas import SourceCreate
from knowwork_server.knowledge.tools import TurnScope, knowledge_tools
from knowwork_server.tools.registry import ToolContext

from .knowflow_stub import SERVICE_KEY

pytestmark = pytest.mark.anyio
CTX = ToolContext("ws1", "conv-r", "alice")


class Routed:
    """两个库：kb1 能开多跳，kb2 什么都不能开；记下收到的检索请求。"""

    source_id = "builtin"
    features = frozenset()

    def __init__(self, routes=("multi_hop",)):
        self.routes = routes
        self.requests = []

    async def list_bases(self, caller):
        return (BaseInfo(id="kb1", name="制度库", routes=self.routes), BaseInfo(id="kb2", name="草稿库"))

    def base_of(self, doc_ref):
        return "kb1"

    async def search(self, caller, request):
        self.requests.append(request)
        return [Evidence(source_id="builtin", kb_id="kb1", doc_ref="kb1/d1", span_ref="s1", title="制度",
                         text="差旅按标准报销")]

    async def read(self, caller, request):
        raise NotImplementedError

    async def list_documents(self, caller, scope, page):
        raise NotImplementedError

    async def probe(self, caller):
        return None


def provide(env, source):
    env.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))


def search_tool(env, turn_scope=None):
    options = {"turn_scope": turn_scope} if turn_scope else {}
    [tool] = [t for t in knowledge_tools(env.knowledge.service, env.knowledge.citations, **options)
              if t.spec.name == "knowledge_search"]
    return tool


async def described(tool):
    return set((await tool.spec.tailor(CTX, tool.spec)).input_schema["properties"])


async def test_search_params_follow_what_the_session_can_open(env):
    tool = search_tool(env)
    assert {"multi_hop", "visual"} <= set(tool.spec.input_schema["properties"]), "执行时按完整定义校验"
    assert not {"multi_hop", "visual"} & await described(tool), "没有能开的库（开源版没装扩展）时宿主看不到"
    source = Routed()
    provide(env, source)
    params = await described(tool)
    assert "multi_hop" in params and "visual" not in params
    source.routes = ("multi_hop", "visual")
    assert {"multi_hop", "visual"} <= await described(tool)

    async def only_drafts(_ctx):
        return TurnScope(bases=("builtin.kb2",))

    assert not {"multi_hop", "visual"} & await described(search_tool(env, only_drafts)), "只看这个会话能查的库"


async def test_switches_reach_the_source_and_unknowing_sources_stay_quiet(env):
    source = Routed()
    provide(env, source)
    await env.knowledge.admin.create("alice", "ws1", SourceCreate(
        name="合同库", base_url="http://kf.local", service_key=SERVICE_KEY,
        config={"auth_mode": "assertion", "dataset_ids": ["ds1"]}))
    payload = json.loads(await search_tool(env).handler(CTX, {"query": "报销", "multi_hop": True, "visual": False}))
    [request] = source.requests
    assert (request.multi_hop, request.visual) == (True, False)
    assert not any("不支持" in note for note in payload["notes"]), "KnowFlow 不认这两个参数，照常检索、不另外说明"
    json.loads(await search_tool(env).handler(CTX, {"query": "报销"}))
    assert (source.requests[1].multi_hop, source.requests[1].visual) == (None, None), "不写按库的默认"


async def test_catalog_and_bases_tool_mark_the_routes(env):
    provide(env, Routed())
    caller = await env.knowledge.service.caller("ws1", "alice")
    catalog = turn_catalog(await env.knowledge.service.bases(caller))
    assert {e.id: e.routes for e in catalog.bases} == {"builtin.kb1": ("multi_hop",), "builtin.kb2": ()}
    tool = next(t for t in env.knowledge.tools if t.spec.name == "knowledge_bases")
    listed = json.loads(await tool.handler(CTX, {}))["knowledge_bases"]
    assert {b["id"]: b.get("routes") for b in listed} == {"builtin.kb1": ["multi_hop"], "builtin.kb2": None}
