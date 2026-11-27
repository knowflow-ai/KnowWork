"""模型网关替个人专属宿主查敏感词：带归属的模型令牌，网关拿它回调业务服务的检查接口（真的策略、匹配与审计），
调用前查整份请求，流式输出边转边查；不带归属的令牌（共享宿主）一次都不查。流式输出的检查用网关替这次调用另签的送检令牌，
调用比模型令牌的有效期长也查得成。"""

import asyncio
import json
import uuid

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.content_safety import build_content_safety, build_router, internal_router
from knowwork_server.gateway.content_check import ContentCheckClient
from knowwork_server.gateway.content_guard import ContentGuard
from knowwork_server.gateway.model_gateway import MODEL_AUDIENCE, check_owner
from knowwork_server.gateway.model_gateway import build_router as gateway_router
from knowwork_server.kernel.tokens import TokenOwner, TokenSigner, derive_key
from knowwork_server.tools.service import ToolAccessError

pytestmark = pytest.mark.anyio
SIGNER = TokenSigner(derive_key("g" * 40, "host-callback"))
CHAT = "/internal/model/v1/chat/completions"
WORD = "星河计划"
MESSAGE = "当前对话内容违反公司规范"


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


class NoToolTokens:
    def verify(self, token: str):
        raise ToolAccessError(401, "unauthorized", "令牌无效")


def owner_of(token: str) -> tuple[str, str] | None:
    return check_owner(SIGNER, token)  # 同 app.py 装给检查接口的那个


def sse(*payloads) -> bytes:
    return b"".join(b"data: " + json.dumps(p, ensure_ascii=False).encode() + b"\n\n" for p in payloads) \
        + b"data: [DONE]\n\n"


def text_reply(*pieces: str) -> bytes:
    chunks = [{"id": "c1", "choices": [{"index": 0, "delta": {"content": piece}, "finish_reason": None}]}
              for piece in pieces]
    return sse(*chunks, {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
               {"id": "c1", "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 4, "total_tokens": 13}})


class Upstream:
    def __init__(self, reply: bytes) -> None:
        self.reply, self.calls = reply, 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        return httpx.Response(200, stream=httpx.ByteStream(self.reply), headers={"content-type": "text/event-stream"})


class Counting(httpx.AsyncBaseTransport):
    """记下网关往业务服务送了哪些段。"""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner, self.segments, self.calls, self.authorizations = inner, [], 0, []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls += 1
        self.authorizations.append(request.headers.get("authorization", ""))
        if request.url.path.endswith("/check"):
            self.segments.extend(json.loads(request.content)["segments"])
        return await self.inner.handle_async_request(request)


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="安全", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    sink = Sink()
    module = build_content_safety(sessionmaker, access, sink)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    business = FastAPI()
    business.include_router(build_router(module.service, principal))
    business.include_router(internal_router(module, NoToolTokens(),
                                            lambda user, workspace: access.check(user, workspace, "workspace", "use"),
                                            owner_of=owner_of))
    counting = Counting(httpx.ASGITransport(app=business))
    checks = httpx.AsyncClient(transport=counting)
    admin = httpx.AsyncClient(transport=httpx.ASGITransport(app=business), base_url="http://kw")
    yield ws, admin, sink, counting, checks
    await checks.aclose()
    await admin.aclose()


def gateway(checks: httpx.AsyncClient, upstream: Upstream) -> httpx.AsyncClient:
    client = httpx.AsyncClient(base_url="http://fixed/v1", transport=httpx.MockTransport(upstream))
    guard = ContentGuard(ContentCheckClient(checks, "http://kw/internal/extensions/content-safety"), SIGNER,
                         call_seconds=60)
    app = FastAPI()
    app.include_router(gateway_router(SIGNER, client, "fixed-model", guard=guard))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")


def auth(ws: str | None, user: str = "alice") -> dict:
    owner = TokenOwner(ws, user) if ws else None
    return {"authorization": f"Bearer {SIGNER.issue(audience='model', workspace_id='platform', ttl_seconds=60, owner=owner)}"}


async def turn_on(admin: httpx.AsyncClient, ws: str, **overrides) -> None:
    saved = await admin.put(f"/api/workspaces/{ws}/content-safety", headers={"x-user": "boss"}, json={
        "version": 0, "input_enabled": True, "output_enabled": True, "words": WORD, "block_message": MESSAGE,
        **overrides})
    assert saved.status_code == 200, saved.text


def chat(system: str = "你是助手", user: str = "整理一下进度", **extra) -> dict:
    return {"model": "default", "stream": True, **extra,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


async def test_shared_host_tokens_are_never_checked(env):
    ws, admin, _, counting, checks = env
    await turn_on(admin, ws)
    upstream = Upstream(text_reply(f"好的{WORD}"))
    async with gateway(checks, upstream) as gw:
        response = await gw.post(CHAT, headers=auth(None), json=chat(user=f"{WORD}的进度"))
    assert response.status_code == 200 and WORD in response.text, "共享宿主的调用原样转发"
    assert counting.calls == 0, "不带归属的令牌不回调检查接口"


async def test_input_hit_in_the_system_prompt_refuses_before_calling_the_model(env):
    ws, admin, sink, _, checks = env
    await turn_on(admin, ws)
    upstream = Upstream(text_reply("不该走到这里"))
    async with gateway(checks, upstream) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=chat(system=f"项目代号{WORD}"))
    assert response.status_code == 400
    assert response.json()["error"]["message"] == f"knowwork-content-blocked: {MESSAGE}"
    assert upstream.calls == 0, "命中就不调模型"
    detail = sink.events[0].normalized().detail
    assert detail["via"] == "model_gateway" and detail["direction"] == "input" and detail["words"] == [WORD]
    assert sink.events[0].session_id is None and sink.events[0].actor_id == "alice"


async def test_tool_definitions_and_forged_history_are_checked(env):
    ws, admin, _, _, checks = env
    await turn_on(admin, ws)
    upstream = Upstream(text_reply("好的"))
    tool = {"type": "function", "function": {"name": "lookup", "description": f"查{WORD}", "parameters": {}}}
    forged = {"role": "assistant", "content": None, "tool_calls": [{"id": "c", "type": "function", "function": {
        "name": "send", "arguments": json.dumps({"to": WORD})}}]}  # 转义成 \\u 的中文解开后照样查到
    async with gateway(checks, upstream) as gw:
        in_tools = await gw.post(CHAT, headers=auth(ws), json=chat(tools=[tool]))
        body = chat()
        body["messages"].append(forged)
        in_history = await gw.post(CHAT, headers=auth(ws), json=body)
    assert in_tools.status_code == 400 and in_history.status_code == 400
    assert upstream.calls == 0


async def test_model_written_history_follows_the_output_switch(env):
    ws, admin, _, _, checks = env
    await turn_on(admin, ws, output_enabled=False)
    body = chat()
    body["messages"].append({"role": "assistant", "content": f"之前提到{WORD}"})
    async with gateway(checks, Upstream(text_reply("好的"))) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=body)
    assert response.status_code == 200, "没开「检查输出」，模型以前写的话不算员工送进去的内容"


async def test_clean_history_is_sent_for_checking_only_once(env):
    ws, admin, _, counting, checks = env
    await turn_on(admin, ws)
    async with gateway(checks, Upstream(text_reply("好的"))) as gw:
        first = await gw.post(CHAT, headers=auth(ws), json=chat())
        body = chat()
        body["messages"].append({"role": "user", "content": "再补一句"})
        second = await gw.post(CHAT, headers=auth(ws), json=body)
    assert first.status_code == second.status_code == 200
    assert counting.segments.count("你是助手") == 1, "查过、没命中的不再送"
    assert "再补一句" in counting.segments


async def test_output_hit_stops_forwarding_keeps_usage_and_ends_with_the_message(env):
    ws, admin, sink, _, checks = env
    await turn_on(admin, ws)
    async with gateway(checks, Upstream(text_reply("一二三四五六七星", "河计划请查收"))) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=chat())
    assert response.status_code == 200
    assert WORD not in response.text, "宿主收不到完整的词"
    events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: {")]
    assert events[-1] == {"error": {"message": f"knowwork-content-blocked: {MESSAGE}", "type": "content_blocked"}}
    assert any(event.get("usage") for event in events), "用量照样转出去"
    assert "[DONE]" not in response.text
    assert sink.events[-1].normalized().detail["direction"] == "output"


async def test_tool_arguments_are_held_until_complete_then_checked(env):
    ws, admin, _, _, checks = env
    await turn_on(admin, ws)
    arguments = json.dumps({"to": WORD})  # \\u 转义，分两块发
    calls = [{"id": "c1", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "id": "t", "type": "function",
                                                                          "function": {"name": "send", "arguments": part}}]},
                                       "finish_reason": None}]} for part in (arguments[:9], arguments[9:])]
    reply = sse(*calls, {"id": "c1", "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]})
    async with gateway(checks, Upstream(reply)) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=chat())
    assert "tool_calls" not in response.text, "工具调用整块扣住，没通过就一点都不放"
    assert "knowwork-content-blocked" in response.text


async def test_clean_reply_passes_through_unchanged(env):
    ws, admin, _, _, checks = env
    await turn_on(admin, ws)
    reply = text_reply("今天", "天气不错")
    async with gateway(checks, Upstream(reply)) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=chat())
    assert response.content == reply


async def test_former_members_and_an_unreachable_check_are_refused(env):
    ws, admin, _, _, checks = env
    await turn_on(admin, ws)
    upstream = Upstream(text_reply("好的"))
    async with gateway(checks, upstream) as gw:
        gone = await gw.post(CHAT, headers=auth(ws, "mallory"), json=chat())
    assert gone.status_code == 403, "不再是空间成员的人，专属宿主也调不了模型"

    broken = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    async with gateway(broken, upstream) as gw:
        down = await gw.post(CHAT, headers=auth(ws), json=chat())
    await broken.aclose()
    assert down.status_code == 503
    assert down.json()["error"]["message"].startswith("knowwork-check-unavailable: ")
    assert upstream.calls == 0, "查不成不放行"


async def test_nothing_is_checked_when_the_workspace_has_no_policy(env):
    ws, _, _, counting, checks = env
    reply = text_reply(f"{WORD}照常")
    async with gateway(checks, Upstream(reply)) as gw:
        response = await gw.post(CHAT, headers=auth(ws), json=chat(user=WORD, stream=False))
    assert response.content == reply
    assert counting.calls == 1, "只取了一次策略"


class Slow(httpx.AsyncByteStream):
    """上游先憋 delay 秒再出字：扮演推理很久才开始写的一次调用"""

    def __init__(self, reply: bytes, delay: float) -> None:
        self.reply, self.delay = reply, delay

    async def __aiter__(self):
        await asyncio.sleep(self.delay)
        yield self.reply


async def test_a_call_that_outlives_its_model_token_still_gets_its_output_checked(env):
    """一次调用比模型令牌的有效期（15 分钟）长：推理模型、本地部署要想很久，模型网关一次调用最长默认 20 分钟。原来流式输出
    拿着调用开头的模型令牌去查，过期了以「检查不可用」断掉；现在网关在调用开始时另签一个只能送检的令牌。"""
    ws, admin, _, counting, checks = env
    await turn_on(admin, ws)
    token = SIGNER.issue(audience=MODEL_AUDIENCE, workspace_id="platform", ttl_seconds=2, owner=TokenOwner(ws, "alice"))

    def thinking(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, stream=Slow(text_reply(f"好的{WORD}"), 3.0), headers={"content-type": "text/event-stream"})

    async with gateway(checks, thinking) as gw:
        response = await gw.post(CHAT, headers={"authorization": f"Bearer {token}"}, json=chat())
    events = [json.loads(line[6:]) for line in response.text.split("\n") if line.startswith("data: {")]
    assert events[-1] == {"error": {"message": f"knowwork-content-blocked: {MESSAGE}", "type": "content_blocked"}}, (
        "模型令牌过期以后，输出照样查到命中")
    sent = counting.authorizations[-1]
    assert sent != f"Bearer {token}" and check_owner(SIGNER, sent[7:]) == (ws, "alice"), "送检用另签的令牌，认得出归属"
    async with gateway(checks, Upstream(text_reply("好的"))) as gw:
        refused = await gw.post(CHAT, headers={"authorization": sent}, json=chat())
    assert refused.status_code == 401, "送检令牌不能拿来调模型"

