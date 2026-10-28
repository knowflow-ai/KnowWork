"""knowledge_fetch：按检索结果里的图 id 取单张图、按文档与页码取页图、取原文件，放进会话工作区交回路径；
图 id 要是这个会话结果里出现过的；判权同阅读；知识源要声明 fetch；没开沙箱时没有这个工具。"""

import json

import pytest

from knowwork_server.knowledge import ProvidedSource
from knowwork_server.knowledge.fetch_tool import FETCH_SCHEMA
from knowwork_server.knowledge.models import (
    BaseInfo,
    Evidence,
    EvidenceImage,
    FetchedFile,
    ReadResult,
)
from knowwork_server.knowledge.tools import knowledge_tools
from knowwork_server.tools.registry import ToolContext, ToolFailure

from .test_tools_and_citations import DSH_KEYWORDS, keywords

pytestmark = pytest.mark.anyio
ALICE = ToolContext("ws1", "conv-1", "alice")


class Library:
    source_id = "builtin"

    def __init__(self, features=frozenset({"fetch"})):
        self.features = features
        self.requests = []

    async def list_bases(self, caller):
        return (BaseInfo(id="kb1", name="设备手册"), BaseInfo(id="kb2", name="机密库"))

    def base_of(self, doc_ref):
        return doc_ref.split("/", 1)[0]

    async def search(self, caller, request):
        return [Evidence(source_id="builtin", kb_id="kb1", doc_ref="kb1/d1", span_ref="s1", title="接线说明",
                         text="按下图接线", images=(EvidenceImage(ref="digest-1", caption="接线图"),))]

    async def read(self, caller, request):
        return ReadResult(doc_ref=request.doc_ref, title="接线说明", text="全文")

    async def list_documents(self, caller, scope, page):
        raise NotImplementedError

    async def probe(self, caller):
        return None

    async def fetch(self, caller, request):
        self.requests.append(request)
        if request.what == "image":
            return (FetchedFile(name="接线图.png", mime="image/png", data=b"png"),)
        if request.what == "pages":
            return tuple(FetchedFile(name=f"接线说明-第{p}页.jpg", mime="image/jpeg", data=b"jpg", page=p)
                         for p in range(request.first_page, request.last_page + 1))
        return (FetchedFile(name="接线说明.xlsx", mime="application/vnd.ms-excel", data=b"xlsx"),)


class Sandbox:
    def __init__(self):
        self.files = {}

    async def put_input(self, workspace_id, session_id, name, data):
        self.files[(workspace_id, session_id, name)] = data


@pytest.fixture
async def library(env):
    source = Library()
    env.knowledge.provided.add(ProvidedSource(id="builtin", name="扩展知识库", kind="builtin", source=source))
    await env.access.grant("alice", "ws1", "user:carol", "knowledge_base:builtin.kb1", "use")
    return source


def tools(env, sandbox):
    return {t.spec.name: t for t in knowledge_tools(env.knowledge.service, env.knowledge.citations, sandbox=sandbox)}


async def call(tool, ctx, arguments):
    return json.loads(await tool.handler(ctx, arguments))


def test_schema_uses_only_the_dsh_subset():
    assert keywords(FETCH_SCHEMA) <= DSH_KEYWORDS


async def test_no_sandbox_means_no_fetch_tool(env):
    assert "knowledge_fetch" not in {t.spec.name for t in env.knowledge.tools}
    assert "knowledge_fetch" in tools(env, Sandbox())


async def test_an_image_from_the_results_goes_into_the_workspace(env, library):
    sandbox = Sandbox()
    found = tools(env, sandbox)
    carol = ToolContext("ws1", "conv-9", "carol")
    await found["knowledge_search"].handler(carol, {"query": "接线"})
    result = await call(found["knowledge_fetch"], carol, {"image": "IMG:1:0"})
    assert result["files"] == [{"path": "/workspace/input/知识库-接线图.png", "type": "image/png", "size": 3}]
    assert "read_image" in result["notes"][0] and "看不了图" in result["notes"][0]
    assert sandbox.files[("ws1", "conv-9", "知识库-接线图.png")] == b"png"
    assert (library.requests[0].doc_ref, library.requests[0].image_ref) == ("kb1/d1", "digest-1")
    with pytest.raises(ToolFailure, match="只能取这个会话检索、阅读结果里出现过的图"):
        await found["knowledge_fetch"].handler(carol, {"image": "IMG:1:3"})
    with pytest.raises(ToolFailure, match="只能取这个会话"):
        await found["knowledge_fetch"].handler(ToolContext("ws1", "conv-other", "carol"), {"image": "IMG:1:0"})


async def test_pages_and_the_original_file(env, library):
    sandbox = Sandbox()
    fetch = tools(env, sandbox)["knowledge_fetch"]
    carol = ToolContext("ws1", "conv-9", "carol")
    pages = await call(fetch, carol, {"source_id": "builtin", "doc_ref": "kb1/d1", "pages": "5-6"})
    assert [(f["path"], f["page"]) for f in pages["files"]] == [
        ("/workspace/input/知识库-接线说明-第5页.jpg", 5), ("/workspace/input/知识库-接线说明-第6页.jpg", 6)]
    original = await call(fetch, carol, {"source_id": "builtin", "doc_ref": "kb1/d1", "original": True})
    assert original["files"][0]["path"].endswith("知识库-接线说明.xlsx") and "Python" in original["notes"][0]
    for bad, says in (({"source_id": "builtin", "doc_ref": "kb1/d1", "pages": "1-9"}, "一次最多取 5 页"),
                      ({"source_id": "builtin", "doc_ref": "kb1/d1", "pages": "第五页"}, "页码或范围"),
                      ({"image": "IMG:1:0", "original": True}, "只能给一个"),
                      ({"pages": "1"}, "要给 source_id 和 doc_ref")):
        with pytest.raises(ToolFailure, match=says):
            await fetch.handler(carol, bad)


async def test_access_and_source_support_are_checked(env, library):
    fetch = tools(env, Sandbox())["knowledge_fetch"]
    carol = ToolContext("ws1", "conv-9", "carol")
    with pytest.raises(ToolFailure, match="没有使用权限"):
        await fetch.handler(carol, {"source_id": "builtin", "doc_ref": "kb2/secret", "original": True})
    library.features = frozenset()
    with pytest.raises(ToolFailure, match="不支持取文件"):
        await fetch.handler(carol, {"source_id": "builtin", "doc_ref": "kb1/d1", "original": True})
