"""厂商、模型、分组：管理要求空间 admin；密钥只存密文、接口不回显；别名路由与按分组授权。"""

from dataclasses import asdict

import pytest
from pydantic import ValidationError
from sqlalchemy import text

from knowwork_server.access.engine import VisibleSet
from knowwork_server.models.crypto import SecretBox
from knowwork_server.models.errors import Conflict, Denied, Invalid, NotFound, Unavailable
from knowwork_server.models.schemas import (
    GroupCreate,
    GroupUpdate,
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
    return await catalog.create_provider("bob", "ws1", ProviderCreate(**{**body, **kw}))


async def _model(catalog, provider_id, alias="default", **kw):
    return await catalog.create_model("bob", "ws1", ModelCreate(provider_id=provider_id, alias=alias,
                                                                name=f"{alias}-upstream", **kw))


async def test_provider_key_is_encrypted_and_never_returned(catalog, db):
    view = await _provider(catalog)
    assert view.api_key_configured and view.api_key_last4 == "WXYZ"
    assert view.base_url == "https://api.example.com/v1"
    assert KEY not in repr(asdict(view))
    listed = await catalog.list_providers("alice", "ws1")
    assert KEY not in repr([asdict(v) for v in listed])
    from sqlalchemy.ext.asyncio import create_async_engine
    admin = create_async_engine(db.admin_url)
    async with admin.connect() as conn:
        stored = await conn.scalar(text("SELECT api_key_ciphertext FROM model_provider"))
    await admin.dispose()
    assert stored.startswith("v1:") and KEY not in stored


async def test_update_provider_key_keep_replace_clear(catalog):
    view = await _provider(catalog)
    kept = await catalog.update_provider("bob", "ws1", view.id, ProviderUpdate(name="DS"))
    assert kept.api_key_last4 == "WXYZ" and kept.name == "DS"
    replaced = await catalog.update_provider("bob", "ws1", view.id, ProviderUpdate(api_key="sk-new-key-00001111"))
    assert replaced.api_key_last4 == "1111"
    cleared = await catalog.update_provider("bob", "ws1", view.id,
                                            ProviderUpdate(api_key="", enabled=False, base_url="http://10.0.0.5:8000"))
    assert not cleared.api_key_configured and cleared.api_key_last4 == "" and not cleared.enabled


@pytest.mark.parametrize("url", ["ftp://x/v1", "http://user:pw@host/v1", "https://host/v1?x=1", "notaurl"])
def test_base_url_validation(url):
    with pytest.raises(ValidationError):
        ProviderCreate(name="x", base_url=url)


@pytest.mark.parametrize("actor", ["carol", "dave", "mallory"])
async def test_management_requires_workspace_admin(catalog, actor):
    with pytest.raises(Denied):
        await catalog.create_provider(actor, "ws1", ProviderCreate(name="x", base_url="https://h/v1"))
    with pytest.raises(Denied):
        await catalog.list_models(actor, "ws1")
    with pytest.raises(Denied):
        await catalog.list_groups(actor, "ws1")


async def test_model_crud_and_conflicts(catalog):
    provider = await _provider(catalog)
    model = await _model(catalog, provider.id, context_length=64000, input_price_per_mtok="2",
                         output_price_per_mtok="8", capabilities=("chat", "vision"))
    assert model.capabilities == ("chat", "vision") and model.context_length == 64000
    with pytest.raises(Conflict):
        await _model(catalog, provider.id)
    with pytest.raises(NotFound):
        await _model(catalog, "mp_missing", alias="other")
    updated = await catalog.update_model("bob", "ws1", model.id, ModelUpdate(name="v2", capabilities=("embedding",),
                                                                             context_length=None))
    assert (updated.name, updated.capabilities, updated.context_length) == ("v2", ("embedding",), None)
    with pytest.raises(Invalid):
        await catalog.update_model("bob", "ws1", model.id, ModelUpdate(alias=None, name=None))
    with pytest.raises(Conflict):
        await catalog.delete_provider("bob", "ws1", provider.id)
    assert [m.alias for m in await catalog.list_models("bob", "ws1")] == ["default"]
    await catalog.delete_model("bob", "ws1", model.id)
    with pytest.raises(NotFound):
        await catalog.delete_model("bob", "ws1", model.id)
    await catalog.delete_provider("bob", "ws1", provider.id)
    with pytest.raises(NotFound):
        await catalog.update_provider("bob", "ws1", provider.id, ProviderUpdate(name="x"))
    with pytest.raises(Invalid):
        await catalog.delete_model("bob", "ws1", "bad id!")


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
    await catalog.update_model("bob", "ws1", model.id, ModelUpdate(enabled=False))
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "default")
    await catalog.update_model("bob", "ws1", model.id, ModelUpdate(enabled=True))
    await catalog.update_provider("bob", "ws1", provider.id, ProviderUpdate(enabled=False))
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "default")


async def test_resolve_is_isolated_per_workspace(catalog, access):
    await access.create_workspace(workspace_id="ws2", name="二", owner_user_id="erin")
    await _model(catalog, (await _provider(catalog)).id)
    assert await catalog.resolve("ws2", "default") is None


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
    route, view = await catalog.route_by_id("bob", "ws1", model.id)
    assert route.api_key == "" and view.id == model.id


async def test_groups_crud(catalog):
    provider = await _provider(catalog)
    a, b = await _model(catalog, provider.id, "a"), await _model(catalog, provider.id, "b")
    group = await catalog.create_group("bob", "ws1", GroupCreate(name="通用", model_ids=(b.id, a.id, a.id)))
    assert group.model_ids == tuple(sorted((a.id, b.id)))
    with pytest.raises(Conflict):
        await catalog.create_group("bob", "ws1", GroupCreate(name="通用"))
    with pytest.raises(Invalid):
        await catalog.create_group("bob", "ws1", GroupCreate(name="坏", model_ids=("md_missing",)))
    renamed = await catalog.update_group("bob", "ws1", group.id, GroupUpdate(name="改名", description="说明"))
    assert renamed.model_ids == group.model_ids and renamed.description == "说明"
    shrunk = await catalog.update_group("bob", "ws1", group.id, GroupUpdate(model_ids=(a.id,)))
    assert shrunk.model_ids == (a.id,)
    assert [(g.name, g.model_ids) for g in await catalog.list_groups("bob", "ws1")] == [("改名", (a.id,))]
    await catalog.delete_model("bob", "ws1", a.id)
    assert (await catalog.list_groups("bob", "ws1"))[0].model_ids == ()
    await catalog.delete_group("bob", "ws1", group.id)
    with pytest.raises(NotFound):
        await catalog.delete_group("bob", "ws1", group.id)


async def test_usable_models_follow_group_grants(catalog, access):
    provider = await _provider(catalog)
    grouped = await _model(catalog, provider.id, "grouped")
    await _model(catalog, provider.id, "loose")
    await catalog.create_group("bob", "ws1", GroupCreate(name="g", model_ids=(grouped.id,)))
    # 空间角色默认对全部模型分组有 use：成员能用分组里的模型，不在任何分组里的模型谁都不能用
    assert [m.alias for m in await catalog.usable_models("dave", "ws1")] == ["grouped"]
    assert (await catalog.ensure_usable("dave", "ws1", "grouped")).id == grouped.id
    with pytest.raises(Denied):
        await catalog.ensure_usable("dave", "ws1", "loose")
    assert await catalog.usable_models("mallory", "ws1") == (), "非成员什么都用不了"
    with pytest.raises(Denied):
        await catalog.ensure_usable("mallory", "ws1", "grouped")
    await catalog.update_provider("bob", "ws1", provider.id, ProviderUpdate(enabled=False))
    assert await catalog.usable_models("dave", "ws1") == ()


class _OnlyGroups:
    """只授权指定分组的判定器：模拟管理员撤掉默认通配、只给某个组授权的情形。"""

    def __init__(self, inner, ids):
        self.inner, self.ids = inner, frozenset(ids)

    def check(self, *args):
        return self.inner.check(*args)

    def visible_resources(self, user_id, workspace_id, resource_type, action):
        return VisibleSet(all_=False, ids=self.ids) if user_id == "dave" else VisibleSet(False, frozenset())


async def test_unauthorized_group_is_rejected(sessionmaker, access, catalog):
    provider = await _provider(catalog)
    a, b = await _model(catalog, provider.id, "a"), await _model(catalog, provider.id, "b")
    granted = await catalog.create_group("bob", "ws1", GroupCreate(name="授权", model_ids=(a.id,)))
    await catalog.create_group("bob", "ws1", GroupCreate(name="未授权", model_ids=(b.id,)))
    scoped = ModelCatalog(sessionmaker, _OnlyGroups(access, {granted.id}), SecretBox.from_master_key("m" * 40))
    assert [m.alias for m in await scoped.usable_models("dave", "ws1")] == ["a"]
    with pytest.raises(Denied):
        await scoped.ensure_usable("dave", "ws1", "b")
    assert await scoped.usable_models("carol", "ws1") == ()


async def test_default_alias_falls_back_to_the_first_enabled_chat_model(catalog):
    """宿主请求的模型名是 default；没有哪个模型的别名正好叫 default 时，用最早添加、已启用的对话模型。"""
    provider = await _provider(catalog)
    embedding = await _model(catalog, provider.id, alias="bge", capabilities=["embedding"])
    first = await _model(catalog, provider.id, alias="qwen235b")
    await _model(catalog, provider.id, alias="later")
    assert embedding.alias == "bge"
    assert (await catalog.resolve("ws1", "default")).alias == "qwen235b"
    await catalog.update_model("bob", "ws1", first.id, ModelUpdate(enabled=False))
    assert (await catalog.resolve("ws1", "default")).alias == "later", "停用的跳过"
    with pytest.raises(NotFound):
        await catalog.resolve("ws1", "unknown"), "只有 default 会回退，别的名字照样报不存在"
    explicit = await _model(catalog, provider.id, alias="default")
    assert (await catalog.resolve("ws1", "default")).model_id == explicit.id, "显式叫 default 的优先"


async def test_default_capabilities_follow_the_model_the_host_actually_uses(catalog):
    """运行配置据此决定要不要收掉看图工具：和网关解析 default 用同一套规则。"""
    assert await catalog.default_capabilities("ws1") is None, "还没配模型"
    provider = await _provider(catalog)
    text_only = await _model(catalog, provider.id, alias="qwen235b", capabilities=["chat"])
    assert await catalog.default_capabilities("ws1") == frozenset({"chat"})
    await _model(catalog, provider.id, alias="default", capabilities=["chat", "vision"])
    assert await catalog.default_capabilities("ws1") == frozenset({"chat", "vision"}), "显式叫 default 的优先"
    assert text_only.alias == "qwen235b"
