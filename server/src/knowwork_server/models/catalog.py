"""厂商与模型的增删改查、网关路由解析。管理操作要求空间 admin（workspace:edit）。"""

from sqlalchemy import delete, func, select

from .core import CatalogCore, check_id, model_view, new_id, provider_view
from .crypto import SecretError, last4
from .errors import Conflict, Invalid, NotFound, Unavailable
from .schemas import ModelCreate, ModelUpdate, ModelView, ProviderCreate, ProviderUpdate, ProviderView, Route
from .tables import Model, ModelProvider

# 宿主请求模型时用的名字。没有哪个模型的别名正好叫它时，用空间里最早添加、已启用的对话模型，
# 这样管理员加完模型不必知道要把别名填成 default。
DEFAULT_ALIAS = "default"


async def _first_enabled_chat_model(session):
    return (await session.execute(
        select(Model, ModelProvider).join(ModelProvider, ModelProvider.id == Model.provider_id)
        .where(Model.enabled.is_(True), ModelProvider.enabled.is_(True), Model.capabilities.any("chat"))
        .order_by(Model.created_at, Model.id).limit(1))).first()


async def _lookup(session, alias: str):
    """按别名找模型与厂商；default 找不到时退到最早添加、已启用的对话模型。网关路由与能力查询共用。"""
    found = (await session.execute(
        select(Model, ModelProvider).join(ModelProvider, ModelProvider.id == Model.provider_id)
        .where(Model.alias == alias))).first()
    if found is None and alias == DEFAULT_ALIAS:
        found = await _first_enabled_chat_model(session)
    return found


class ProviderMixin(CatalogCore):
    async def create_provider(self, actor_id: str, workspace_id: str, body: ProviderCreate) -> ProviderView:
        self._require_admin(actor_id, workspace_id)
        row = ModelProvider(id=new_id("mp"), workspace_id=workspace_id, name=body.name.strip(), kind=body.kind,
                            base_url=body.base_url, enabled=body.enabled)
        self._set_key(row, body.api_key)
        async with self._tx(workspace_id) as session:
            session.add(row)
        return provider_view(row)

    async def list_providers(self, actor_id: str, workspace_id: str) -> tuple[ProviderView, ...]:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(ModelProvider).order_by(ModelProvider.created_at,
                                                                         ModelProvider.id))).all()
        return tuple(provider_view(row) for row in rows)

    async def update_provider(self, actor_id: str, workspace_id: str, provider_id: str,
                              body: ProviderUpdate) -> ProviderView:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            row = await self._provider(session, provider_id)
            if body.name is not None:
                row.name = body.name.strip()
            if body.base_url is not None:
                row.base_url = body.base_url
            if body.enabled is not None:
                row.enabled = body.enabled
            if body.api_key is not None:
                self._set_key(row, body.api_key)
        return provider_view(row)

    async def delete_provider(self, actor_id: str, workspace_id: str, provider_id: str) -> None:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            row = await self._provider(session, provider_id)
            used = await session.scalar(select(func.count()).select_from(Model).where(Model.provider_id == row.id))
            if used:
                raise Conflict("该厂商下还有模型，请先删除模型")
            await session.delete(row)

    def _set_key(self, row: ModelProvider, api_key: str | None) -> None:
        secret = (api_key or "").strip()
        if not secret:
            row.api_key_ciphertext, row.api_key_last4 = None, None
            return
        row.api_key_ciphertext = self._box.encrypt(secret, workspace_id=row.workspace_id, provider_id=row.id)
        row.api_key_last4 = last4(secret)

    async def _provider(self, session, provider_id: str) -> ModelProvider:
        check_id(provider_id, "provider_id")
        row = await session.scalar(select(ModelProvider).where(ModelProvider.id == provider_id))
        if row is None:
            raise NotFound("厂商不存在")
        return row

    def _decrypt(self, row: ModelProvider) -> str:
        if not row.api_key_ciphertext:
            return ""
        try:
            return self._box.decrypt(row.api_key_ciphertext, workspace_id=row.workspace_id, provider_id=row.id)
        except SecretError as error:
            raise Unavailable("厂商密钥无法解密，请重新填写") from error


class ModelMixin(ProviderMixin):
    async def create_model(self, actor_id: str, workspace_id: str, body: ModelCreate) -> ModelView:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            await self._provider(session, body.provider_id)
            row = Model(id=new_id("md"), workspace_id=workspace_id, provider_id=body.provider_id, alias=body.alias,
                        name=body.name.strip(), capabilities=[c.value for c in body.capabilities],
                        context_length=body.context_length, thinking=body.thinking,
                        input_price_per_mtok=body.input_price_per_mtok,
                        output_price_per_mtok=body.output_price_per_mtok, currency=body.currency,
                        enabled=body.enabled)
            session.add(row)
        return model_view(row)

    async def list_models(self, actor_id: str, workspace_id: str) -> tuple[ModelView, ...]:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(Model).order_by(Model.alias))).all()
        return tuple(model_view(row) for row in rows)

    async def update_model(self, actor_id: str, workspace_id: str, model_id: str, body: ModelUpdate) -> ModelView:
        self._require_admin(actor_id, workspace_id)
        changes = body.model_dump(exclude_unset=True)
        if any(value is None for key, value in changes.items()
               if key not in ("context_length", "thinking", "input_price_per_mtok", "output_price_per_mtok")):
            raise Invalid("该字段不能置空")
        async with self._tx(workspace_id) as session:
            row = await self._model(session, model_id)
            for key, value in changes.items():
                if key == "capabilities":
                    value = [c.value if hasattr(c, "value") else str(c) for c in value]
                setattr(row, key, value.strip() if key == "name" else value)
        return model_view(row)

    async def delete_model(self, actor_id: str, workspace_id: str, model_id: str) -> None:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            row = await self._model(session, model_id)
            await session.execute(delete(Model).where(Model.id == row.id))

    async def _model(self, session, model_id: str) -> Model:
        check_id(model_id, "model_id")
        row = await session.scalar(select(Model).where(Model.id == model_id))
        if row is None:
            raise NotFound("模型不存在")
        return row

    async def capable_models(self, workspace_id: str, capability: str) -> tuple[ModelView, ...]:
        """启用的、有这种能力的模型（厂商也要启用）。给后台任务挑模型用（知识库的嵌入、重排、看图）：
        这些调用由 worker 按别名发起，不走个人的模型授权，所以不按分组收窄；判谁能看由调用方决定。"""
        check_id(workspace_id, "workspace_id")
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(Model).join(ModelProvider, ModelProvider.id == Model.provider_id)
                                          .where(Model.enabled.is_(True), ModelProvider.enabled.is_(True))
                                          .order_by(Model.alias))).all()
        return tuple(model_view(row) for row in rows if capability in (row.capabilities or ()))

    # ---- 网关用 ----

    async def resolve(self, workspace_id: str, alias: str) -> Route | None:
        """按别名取转发路由。空间里一个模型都没配置时返回 None，由网关决定是否回退到固定上游；
        配置了模型但别名不存在、模型或厂商停用，一律 NotFound（不回退，免得静默换模型）。"""
        check_id(workspace_id, "workspace_id")
        async with self._tx(workspace_id) as session:
            found = await _lookup(session, alias)
            if found is None:
                if not await session.scalar(select(func.count()).select_from(Model)):
                    return None
                raise NotFound("模型不存在")
        model, provider = found
        if not (model.enabled and provider.enabled):
            raise NotFound("模型不存在")
        return Route(model_id=model.id, alias=model.alias, upstream_model=model.name,
                     base_url=provider.base_url, api_key=self._decrypt(provider), thinking=model.thinking)

    async def resolve_capable(self, workspace_id: str, alias: str, capability: str) -> Route:
        """内部模型客户端用：按别名取路由并核对能力。没配置、找不到、停用、不支持这种能力都抛错，不回退到别的模型
        （嵌入模型换了，向量就和已有的对不上）。default 的规则同 resolve，但只对对话、看图有意义。"""
        check_id(workspace_id, "workspace_id")
        async with self._tx(workspace_id) as session:
            found = await _lookup(session, alias)
        if found is None:
            raise NotFound(f"模型「{alias}」不存在，请在「管理 → 模型」里添加")
        model, provider = found
        if not (model.enabled and provider.enabled):
            raise NotFound(f"模型「{alias}」已停用")
        if capability not in (model.capabilities or ()):
            raise Invalid(f"模型「{alias}」没有标记「{capability}」能力")
        return Route(model_id=model.id, alias=model.alias, upstream_model=model.name,
                     base_url=provider.base_url, api_key=self._decrypt(provider), context_length=model.context_length,
                     thinking=model.thinking)

    async def default_capabilities(self, workspace_id: str) -> frozenset[str] | None:
        """宿主请求的 default 实际落到的那个模型有哪些能力（规则同 resolve）。没配模型、找不到或已停用时
        返回 None，调用方按「不知道」处理。运行配置据此决定要不要收掉看图工具。"""
        check_id(workspace_id, "workspace_id")
        async with self._tx(workspace_id) as session:
            found = await _lookup(session, DEFAULT_ALIAS)
        if found is None or not (found[0].enabled and found[1].enabled):
            return None
        return frozenset(found[0].capabilities or ())

    async def route_by_id(self, actor_id: str, workspace_id: str, model_id: str) -> tuple[Route, ModelView]:
        """连通测试用：管理员按 id 取路由，停用的也能测。"""
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            model = await self._model(session, model_id)
            provider = await self._provider(session, model.provider_id)
        route = Route(model_id=model.id, alias=model.alias, upstream_model=model.name,
                      base_url=provider.base_url, api_key=self._decrypt(provider), thinking=model.thinking)
        return route, model_view(model)
