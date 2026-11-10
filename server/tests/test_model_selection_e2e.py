"""输入框选模型（端到端，真实宿主）：业务服务把启用的对话模型推给宿主，发消息带 model 时宿主按官方会话级选择换模型，
之后不带也沿用；「上次用的」从用量记录里取；不能选的模型直接拒。"""

import pytest

from knowwork_server.models.schemas import Capability, ModelCreate, ProviderCreate

from .conftest import anyio_backend, app_db, database_url, fresh_url  # noqa: F401
from .test_e2e import CONV, SseReader, run_ended, stack

pytestmark = pytest.mark.anyio
MODELS = "/api/workspaces/default/chat-models"


async def _configure(runtime, upstream_key: str) -> None:
    runtime.access.set_system_admin("root")
    catalog = runtime.models.catalog
    provider = await catalog.create_provider("root", ProviderCreate(
        name="上游", kind="openai_compatible", base_url=runtime.settings.model_upstream_base_url, api_key=upstream_key))
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="fast", name="up-fast"))
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="pro", name="up-pro",
                                                   capabilities=(Capability.CHAT, Capability.VISION)))
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="embed", name="up-embed",
                                                   capabilities=(Capability.EMBEDDING,)))
    await runtime.host_models.push()  # 改模型时后台也会推，这里等它推完再往下


async def test_choose_a_model_per_conversation(app_db):  # noqa: F811
    async with stack(app_db, ["快的回答", "专业的回答", "接着专业"]) as s:
        client, headers, runtime, upstream = s["client"], s["headers"], s["runtime"], s["upstream"]
        await _configure(runtime, s["upstream_key"])

        listed = (await client.get(MODELS, headers=headers)).json()
        assert [(m["alias"], m["default"], m["vision"]) for m in listed["items"]] == [
            ("fast", True, False), ("pro", False, True)], "只列对话模型，标出默认的"
        assert listed["selected"] == "fast", "没用过就选默认模型"

        cid = (await client.post(CONV, json={}, headers=headers)).json()["id"]
        url = f"{CONV}/{cid}/events"
        rejected = await client.post(f"{CONV}/{cid}/messages", json={"text": "你好", "model": "embed"}, headers=headers)
        assert rejected.status_code == 422, "嵌入模型不能选"

        async with SseReader(client, url, headers) as sse:
            await client.post(f"{CONV}/{cid}/messages", json={"text": "第一问", "model": "fast"}, headers=headers)
            await sse.until(run_ended())
        async with SseReader(client, url, headers) as sse:
            await client.post(f"{CONV}/{cid}/messages", json={"text": "第二问", "model": "pro"}, headers=headers)
            await sse.until(run_ended("run-2"))
        async with SseReader(client, url, headers) as sse:
            await client.post(f"{CONV}/{cid}/messages", json={"text": "第三问"}, headers=headers)
            await sse.until(run_ended("run-3"))
        assert [r["body"]["model"] for r in upstream.requests] == ["up-fast", "up-pro", "up-pro"], \
            "选了就换，之后不带沿用这段对话选的"

        await runtime.usage.drain()
        assert (await client.get(MODELS, params={"conversation_id": cid}, headers=headers)).json()["selected"] == "pro"
        assert (await client.get(MODELS, headers=headers)).json()["selected"] == "pro", "新对话默认选上次用的"
        assert (await client.get(MODELS, headers=s["outsider"])).status_code == 403
