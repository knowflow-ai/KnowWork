"""输入框选模型：可选清单（启用的对话模型，标出默认的）、改了模型通知、推给宿主（合并、推完再推）、默认选哪个。"""

import asyncio

import pytest

from knowwork_server.kernel.wire import models_body
from knowwork_server.models.api import _selected
from knowwork_server.models.host_sync import HostModelSync, host_models_of
from knowwork_server.models.schemas import (
    Capability,
    ChatModel,
    ModelCreate,
    ModelUpdate,
    ProviderCreate,
    ProviderUpdate,
)

pytestmark = pytest.mark.anyio


async def _provider(catalog, name="P"):
    return await catalog.create_provider("root", ProviderCreate(name=name, kind="deepseek",
                                                                base_url="https://api.example.com/v1", api_key="k"))


async def test_lists_enabled_chat_models_and_marks_the_default(catalog):
    changes = []
    catalog.on_change(lambda: changes.append(1))
    first, second = await _provider(catalog), await _provider(catalog, "Q")
    await catalog.create_model("root", ModelCreate(provider_id=first.id, alias="fast", name="f"))
    await catalog.create_model("root", ModelCreate(provider_id=first.id, alias="eye", name="e",
                                                   capabilities=(Capability.CHAT, Capability.VISION)))
    await catalog.create_model("root", ModelCreate(provider_id=first.id, alias="vec", name="v",
                                                   capabilities=(Capability.EMBEDDING,)))
    await catalog.create_model("root", ModelCreate(provider_id=first.id, alias="off", name="o", enabled=False))
    await catalog.create_model("root", ModelCreate(provider_id=second.id, alias="gone", name="g"))
    await catalog.update_provider("root", second.id, ProviderUpdate(enabled=False))
    assert await catalog.chat_models() == (ChatModel("fast", "f", False, True), ChatModel("eye", "e", True, False))
    assert len(changes) == 8, "每次改厂商或模型都通知（建 2 个厂商、5 个模型、改 1 次厂商）"
    assert await catalog.capabilities_of("ws1", "eye") == frozenset({"chat", "vision"})
    assert await catalog.capabilities_of("ws1", None) == frozenset({"chat"}), "没选按 default"
    assert await catalog.capabilities_of("ws1", "off") is None
    named = await catalog.create_model("root", ModelCreate(provider_id=first.id, alias="default", name="d"))
    assert [m.alias for m in await catalog.chat_models() if m.default] == ["default"], "显式叫 default 的就是默认"
    await catalog.update_model("root", named.id, ModelUpdate(enabled=False))
    assert [m.alias for m in await catalog.chat_models() if m.default] == [], "default 停用了就没有默认"


class Catalog:
    def __init__(self):
        self.models = (ChatModel("fast", "f", False, True),)

    async def chat_models(self):
        return self.models


async def test_pushes_the_whole_list_and_coalesces_background_changes():
    catalog, pushed, gate = Catalog(), [], asyncio.Event()

    async def push(models):
        pushed.append(tuple(m.alias for m in models))
        await gate.wait()

    sync = HostModelSync(catalog, push)
    assert await sync.choosable("fast") and not await sync.choosable("nope")
    sync.changed()
    await asyncio.sleep(0)
    catalog.models = (*catalog.models, ChatModel("pro", "p", False, False))
    sync.changed()
    sync.changed()  # 推的过程中又改了两次：推完只再推一次
    gate.set()
    for _ in range(20):
        await asyncio.sleep(0)
    assert pushed == [("fast",), ("fast", "pro")]
    await sync.aclose()


async def test_device_hosts_get_the_same_list_the_server_host_is_pushed():
    """本机宿主推不过去，设备来取：取到的和推给我们宿主的是同一份、同一个请求体（kernel/wire.py）。"""
    catalog, pushed = Catalog(), []
    catalog.models = (*catalog.models, ChatModel("eye", "看图", True, False))

    async def push(models):
        pushed.append(models_body(host_models_of(models)))

    sync = HostModelSync(catalog, push)
    await sync.push()
    assert models_body(await sync.host_models()) == pushed[0] == {
        "models": [{"id": "fast", "name": "f", "vision": False}, {"id": "eye", "name": "看图", "vision": True}]}


async def test_quiet_push_swallows_host_errors():
    async def push(models):
        raise ConnectionError("host down")

    await HostModelSync(Catalog(), push).push_quietly()


def test_selected_prefers_last_used_then_default():
    models = (ChatModel("fast", "f", False, True), ChatModel("pro", "p", False, False))
    assert _selected(models, "pro") == "pro"
    assert _selected(models, "default") == "fast", "以前一律记成 default，算默认模型"
    assert _selected(models, "gone") == "fast"
    assert _selected((ChatModel("pro", "p", False, False),), None) == "pro", "没有默认就选第一个"
    assert _selected((), "pro") is None
