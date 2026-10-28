"""厂商、模型：全平台共用，管理只给系统管理员；密钥只存密文、接口不回显；别名路由与按分组授权。"""

from dataclasses import asdict

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from knowwork_server.models.crypto import SecretBox
from knowwork_server.models.errors import Conflict, Denied, Invalid, NotFound, Unavailable
from knowwork_server.models.schemas import (
    ModelCreate,
    ModelUpdate,
    ProviderCreate,
    ProviderUpdate,
)
from knowwork_server.models.service import ModelCatalog

pytestmark = pytest.mark.anyio
KEY = "sk-live-0123456789abcdWXYZ"


async def _provider(catalog, **kw):
    body = {"name": "DeepSeek", "kind": "deepseek", "base_url": "https://api.example.com/v1/", "api_key": KEY}
    return await catalog.create_provider("root", ProviderCreate(**{**body, **kw}))


async def _model(catalog, provider_id, alias="default", **kw):
    return await catalog.create_model("root", ModelCreate(provider_id=provider_id, alias=alias,
                                                                name=f"{alias}-upstream", **kw))


async def test_provider_key_is_encrypted_and_never_returned(catalog, db):
    view = await _provider(catalog)
    assert view.api_key_configured and view.api_key_last4 == "WXYZ"
    assert view.base_url == "https://api.example.com/v1"
    assert KEY not in repr(asdict(view))
    listed = await catalog.list_providers("root")
    assert KEY not in repr([asdict(v) for v in listed])
    from sqlalchemy.ext.asyncio import create_async_engine
    admin = create_async_engine(db.admin_url)
    async with admin.connect() as conn:
        stored = await conn.scalar(text("SELECT api_key_ciphertext FROM model_provider"))
    await admin.dispose()
    assert stored.startswith("v1:") and KEY not in stored


async def test_update_provider_key_keep_replace_clear(catalog):
    view = await _provider(catalog)
    kept = await catalog.update_provider("root", view.id, ProviderUpdate(name="DS"))
    assert kept.api_key_last4 == "WXYZ" and kept.name == "DS"
    replaced = await catalog.update_provider("root", view.id, ProviderUpdate(api_key="sk-new-key-00001111"))
    assert replaced.api_key_last4 == "1111"
    cleared = await catalog.update_provider("root", view.id,
                                            ProviderUpdate(api_key="", enabled=False, base_url="http://10.0.0.5:8000"))
    assert not cleared.api_key_configured and cleared.api_key_last4 == "" and not cleared.enabled


@pytest.mark.parametrize("url", ["ftp://x/v1", "http://user:pw@host/v1", "https://host/v1?x=1", "notaurl"])
def test_base_url_validation(url):
    with pytest.raises(ValidationError):
        ProviderCreate(name="x", base_url=url)


@pytest.mark.parametrize("actor", ["alice", "bob", "carol", "dave", "mallory"])
async def test_management_requires_the_system_admin(catalog, actor):
    """空间所有者、管理员也不行：模型是所有空间共用的，一个空间的管理员不该改到别的空间。"""
    with pytest.raises(Denied):
        await catalog.create_provider(actor, ProviderCreate(name="x", base_url="https://h/v1"))
    with pytest.raises(Denied):
        await catalog.list_models(actor)


async def test_models_are_shared_by_every_workspace(catalog):
    provider = await _provider(catalog)
    model = await _model(catalog, provider.id, alias="qwen")
    for workspace_id in ("ws1", "ws2"):
        assert (await catalog.resolve(workspace_id, "qwen")).model_id == model.id
        assert [m.alias for m in await catalog.capable_models(workspace_id, "chat")] == ["qwen"]


async def test_chat_models_carry_a_thinking_setting_into_their_route(catalog):
    """思考：不填是跟随服务默认（不补参数），可以改成开、关，也能改回跟随；转发路由带着它，网关与内部客户端据此补参数。"""
    provider = await _provider(catalog)
    model = await _model(catalog, provider.id)
    assert model.thinking is None
    assert (await catalog.update_model("root", model.id, ModelUpdate(thinking="off"))).thinking == "off"
    assert (await catalog.resolve("ws1", model.alias)).thinking == "off"
    assert (await catalog.resolve_capable("ws1", model.alias, "chat")).thinking == "off"
    assert (await catalog.update_model("root", model.id, ModelUpdate(thinking=None))).thinking is None
    with pytest.raises(ValidationError):
        ModelUpdate(thinking="maybe")


async def test_model_crud_and_conflicts(catalog):
    provider = await _provider(catalog)
    model = await _model(catalog, provider.id, context_length=64000, input_price_per_mtok="2",
                         output_price_per_mtok="8", capabilities=("chat", "vision"))
    assert model.capabilities == ("chat", "vision") and model.context_length == 64000
    with pytest.raises(Conflict):
        await _model(catalog, provider.id)
    with pytest.raises(NotFound):
        await _model(catalog, "mp_missing", alias="other")
    updated = await catalog.update_model("root", model.id, ModelUpdate(name="v2", capabilities=("embedding",),
                                                                             context_length=None))
    assert (updated.name, updated.capabilities, updated.context_length) == ("v2", ("embedding",), None)
    with pytest.raises(Invalid):
        await catalog.update_model("root", model.id, ModelUpdate(alias=None, name=None))
    with pytest.raises(Conflict):
        await catalog.delete_provider("root", provider.id)
    assert [m.alias for m in await catalog.list_models("root")] == ["default"]
    await catalog.delete_model("root", model.id)
    with pytest.raises(NotFound):
        await catalog.delete_model("root", model.id)
    await catalog.delete_provider("root", provider.id)
    with pytest.raises(NotFound):
        await catalog.update_provider("root", provider.id, ProviderUpdate(name="x"))
    with pytest.raises(Invalid):
        await catalog.delete_model("root", "bad id!")


async def test_resolve_by_alias(catalog):
    assert await catalog.resolve("ws1", "default") is None, "没配置任何模型时返回 None，让网关回退"
    provider = await _provider(catalog)
    model = await _model(catalog, provider.id)
    route = await catalog.resolve("ws1", "default")
    assert (route.upstream_model, route.base_url, route.api_key) == ("default-upstream", "https://api.example.com/v1",
                                                                     KEY)
    assert KEY not in repr(route)
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "unknown")
    await catalog.update_model("root", model.id, ModelUpdate(enabled=False))
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "default")
    await catalog.update_model("root", model.id, ModelUpdate(enabled=True))
    await catalog.update_provider("root", provider.id, ProviderUpdate(enabled=False))
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "default")


async def test_undecryptable_key_is_unavailable(sessionmaker, access, catalog):
    provider = await _provider(catalog)
    await _model(catalog, provider.id)
    other = ModelCatalog(sessionmaker, access, SecretBox.from_master_key("z" * 40))
    with pytest.raises(Unavailable):
        await other.resolve("ws1", "default")


async def test_provider_without_key(catalog):
    provider = await _provider(catalog, api_key=None, base_url="http://vllm.internal:8000/v1")
    model = await _model(catalog, provider.id)
    assert (await catalog.resolve("ws1", "default")).api_key == ""
    route, view = await catalog.route_by_id("root", model.id)
    assert route.api_key == "" and view.id == model.id


async def test_capable_models_list_enabled_models_with_the_capability(catalog):
    """后台任务挑模型（知识库的嵌入、重排）：启用的、带这种能力的都列出来。"""
    provider = await _provider(catalog)
    await _model(catalog, provider.id, "bge", capabilities=["embedding"])
    off = await _model(catalog, provider.id, "old-bge", capabilities=["embedding"])
    await _model(catalog, provider.id, "chat", capabilities=["chat"])
    await catalog.update_model("root", off.id, ModelUpdate(enabled=False))
    assert [m.alias for m in await catalog.capable_models("ws1", "embedding")] == ["bge"]
    assert [m.alias for m in await catalog.capable_models("ws1", "chat")] == ["chat"]
    await catalog.update_provider("root", provider.id, ProviderUpdate(enabled=False))
    assert await catalog.capable_models("ws1", "embedding") == (), "厂商停用了也不列"


async def test_default_alias_falls_back_to_the_first_enabled_chat_model(catalog):
    """宿主请求的模型名是 default；没有哪个模型的别名正好叫 default 时，用最早添加、已启用的对话模型。"""
    provider = await _provider(catalog)
    embedding = await _model(catalog, provider.id, alias="bge", capabilities=["embedding"])
    first = await _model(catalog, provider.id, alias="qwen3-32b")
    await _model(catalog, provider.id, alias="later")
    assert embedding.alias == "bge"
    assert (await catalog.resolve("ws1", "default")).alias == "qwen3-32b"
    await catalog.update_model("root", first.id, ModelUpdate(enabled=False))
    assert (await catalog.resolve("ws1", "default")).alias == "later", "停用的跳过"
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "unknown"), "只有 default 会回退，别的名字照样报不存在"
    explicit = await _model(catalog, provider.id, alias="default")
    assert (await catalog.resolve("ws1", "default")).model_id == explicit.id, "显式叫 default 的优先"


async def test_default_capabilities_follow_the_model_the_host_actually_uses(catalog):
    """运行配置据此决定要不要收掉看图工具：和网关解析 default 用同一套规则。"""
    assert await catalog.default_capabilities("ws1") is None, "还没配模型"
    provider = await _provider(catalog)
    text_only = await _model(catalog, provider.id, alias="qwen3-32b", capabilities=["chat"])
    assert await catalog.default_capabilities("ws1") == frozenset({"chat"})
    await _model(catalog, provider.id, alias="default", capabilities=["chat", "vision"])
    assert await catalog.default_capabilities("ws1") == frozenset({"chat", "vision"}), "显式叫 default 的优先"
    assert text_only.alias == "qwen3-32b"
