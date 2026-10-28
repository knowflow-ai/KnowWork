"""内部模型客户端：按别名与能力取路由，嵌入分批且按顺序，重排认两种返回格式，看图走对话接口；
失败分可重试与不可重试，提示里不带上游原文与密钥；后台与交互各有并发额度。"""

import asyncio
import json

import httpx
import pytest

from knowwork_server.models.client import ModelCallError, ModelClient, Slots
from knowwork_server.models.schemas import ModelCreate, ModelUpdate, ProviderCreate

pytestmark = pytest.mark.anyio
KEY = "sk-provider-secret-key"


class Upstream:
    def __init__(self, handler):
        self.handler, self.requests = handler, []

    def __call__(self, request: httpx.Request):
        body = json.loads(request.content)
        self.requests.append({"path": request.url.path, "auth": request.headers.get("authorization"), "body": body})
        return self.handler(request.url.path, body)


async def setup(catalog):
    provider = await catalog.create_provider("root", ProviderCreate(name="硅基", base_url="https://up/v1",
                                                                          api_key=KEY))
    for alias, name, capabilities in (("emb", "bge-m3", ["embedding"]), ("rr", "bge-reranker", ["rerank"]),
                                      ("vl", "qwen-vl", ["chat", "vision"]), ("chat", "ds", ["chat"])):
        await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias=alias, name=name,
                                                             capabilities=capabilities))


def client(catalog, handler, slots=None):
    upstream = Upstream(handler)
    return ModelClient(catalog, httpx.AsyncClient(transport=httpx.MockTransport(upstream)), slots or Slots()), upstream


def embeddings(path, body):
    data = [{"index": i, "embedding": [float(i), float(len(text))]} for i, text in enumerate(body["input"])]
    return httpx.Response(200, json={"data": list(reversed(data))})  # 故意乱序


async def test_embeddings_are_batched_and_kept_in_order(catalog):
    await setup(catalog)
    models, upstream = client(catalog, embeddings)
    texts = [f"第{i}段" for i in range(70)]
    vectors = await models.embed("ws1", "emb", texts)
    assert [v[0] for v in vectors] == [float(i % 32) for i in range(70)], "每批内按 index 排回原顺序"
    assert [len(r["body"]["input"]) for r in upstream.requests] == [32, 32, 6]
    assert upstream.requests[0]["auth"] == f"Bearer {KEY}" and upstream.requests[0]["body"]["model"] == "bge-m3"


async def test_input_limit_is_the_context_length_of_the_embedding_model(catalog):
    await setup(catalog)
    provider = (await catalog.list_providers("root"))[0]
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="emb-zh", name="bge-large-zh",
                                                         capabilities=["embedding"], context_length=512))
    models, _ = client(catalog, embeddings)
    assert await models.input_limit("ws1", "emb-zh") == 512
    assert await models.input_limit("ws1", "emb") is None, "没填就是不知道"
    with pytest.raises(ModelCallError) as caught:
        await models.input_limit("ws1", "chat")
    assert not caught.value.retryable, "不是嵌入模型"


@pytest.mark.parametrize("payload", [
    {"results": [{"index": 1, "relevance_score": 0.2}, {"index": 0, "relevance_score": 0.9}]},
    [{"index": 0, "score": 0.9}, {"index": 1, "score": 0.2}],
])
async def test_rerank_understands_both_response_shapes(catalog, payload):
    await setup(catalog)
    models, upstream = client(catalog, lambda path, body: httpx.Response(200, json=payload))
    assert await models.rerank("ws1", "rr", "付款期限", ["30 天", "无关"]) == [0.9, 0.2]
    assert upstream.requests[0]["path"] == "/v1/rerank"
    assert await models.rerank("ws1", "rr", "q", []) == [], "没有文档不发请求"


async def test_image_description_goes_through_chat_completions(catalog):
    await setup(catalog)
    reply = {"choices": [{"message": {"content": "一张付款流程图"}}]}
    models, upstream = client(catalog, lambda path, body: httpx.Response(200, json=reply))
    assert await models.describe_image("ws1", "vl", b"\x89PNG", "image/png", "描述这张图") == "一张付款流程图"
    content = upstream.requests[0]["body"]["messages"][0]["content"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_chat_follows_the_models_thinking_setting(catalog):
    """后台调对话模型（自动打标等）和对话一样按模型的思考设置补参数。"""
    await setup(catalog)
    [chat] = [m for m in await catalog.list_models("root") if m.alias == "chat"]
    await catalog.update_model("root", chat.id, ModelUpdate(thinking="off"))
    models, upstream = client(catalog, lambda path, body: httpx.Response(200, json={
        "choices": [{"message": {"content": "好"}}]}))
    assert await models.chat("ws1", "chat", [{"role": "user", "content": "打个标签"}]) == "好"
    assert upstream.requests[0]["body"]["chat_template_kwargs"] == {"enable_thinking": False}


async def test_chat_passes_the_requested_output_format_and_nothing_by_default(catalog):
    """要结构化输出的调用方（扩展部署的抽取服务）可以带 response_format；不带时请求里没有这一项。"""
    await setup(catalog)
    models, upstream = client(catalog, lambda path, body: httpx.Response(200, json={
        "choices": [{"message": {"content": "{}"}}]}))
    await models.chat("ws1", "chat", [{"role": "user", "content": "抽事件"}], response_format={"type": "json_object"})
    await models.chat("ws1", "chat", [{"role": "user", "content": "打个标签"}])
    assert upstream.requests[0]["body"]["response_format"] == {"type": "json_object"}
    assert "response_format" not in upstream.requests[1]["body"]


async def test_models_must_exist_be_enabled_and_carry_the_capability(catalog):
    await setup(catalog)
    models, upstream = client(catalog, embeddings)
    for alias, call in (("rr", models.embed("ws1", "rr", ["x"])), ("nope", models.embed("ws1", "nope", ["x"])),
                        ("chat", models.describe_image("ws1", "chat", b"x", "image/png", "p"))):
        with pytest.raises(ModelCallError) as caught:
            await call
        assert (caught.value.code, caught.value.retryable) == ("model_not_configured", False), alias
    assert upstream.requests == [], "不回退到别的模型"


@pytest.mark.parametrize(("response", "code", "retryable"), [
    (httpx.Response(401, text=f"bad key {KEY}"), "model_auth", False),
    (httpx.Response(429, text="slow down"), "model_rate_limited", True),
    (httpx.Response(503, text="/var/internal"), "model_unavailable", True),
    (httpx.Response(200, json={"data": []}), "model_bad_response", False),
    (httpx.Response(200, text="<html>"), "model_bad_response", False),
])
async def test_failures_are_classified_without_upstream_details(catalog, response, code, retryable):
    await setup(catalog)
    models, _ = client(catalog, lambda path, body: response)
    with pytest.raises(ModelCallError) as caught:
        await models.embed("ws1", "emb", ["x"])
    assert (caught.value.code, caught.value.retryable) == (code, retryable)
    assert KEY not in caught.value.message and "/var" not in caught.value.message


async def test_timeouts_and_connection_errors_are_retryable(catalog):
    await setup(catalog)

    def boom(path, body):
        raise httpx.ReadTimeout("slow")

    models, _ = client(catalog, boom)
    with pytest.raises(ModelCallError) as caught:
        await models.rerank("ws1", "rr", "q", ["a"])
    assert (caught.value.code, caught.value.retryable) == ("model_timeout", True)


async def test_background_calls_cannot_take_interactive_slots(catalog):
    await setup(catalog)
    gate = asyncio.Event()
    running = {"background": 0, "peak": 0}

    async def slow(request: httpx.Request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            running["background"] += 1
            running["peak"] = max(running["peak"], running["background"])
            await gate.wait()
            running["background"] -= 1
            return embeddings("/embeddings", body)
        return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 1.0}]})

    models = ModelClient(catalog, httpx.AsyncClient(transport=httpx.MockTransport(slow)),
                         Slots(background=2, interactive=2))
    batch = [asyncio.create_task(models.embed("ws1", "emb", ["x"])) for _ in range(5)]
    # 每次调用先查模型目录，机器忙时要多等一会儿才走到发请求：等到两个在跑，再多等一下确认不会超过两个
    for _ in range(200):
        if running["peak"] >= 2:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert running["peak"] == 2, "后台只占两份额度"
    assert await asyncio.wait_for(models.rerank("ws1", "rr", "q", ["a"]), 2) == [1.0], "交互调用不被批量任务堵住"
    gate.set()
    await asyncio.gather(*batch)
