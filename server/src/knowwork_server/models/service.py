"""ModelCatalog：厂商、模型、模型分组的统一入口，以及「用户能用哪些模型」的判定。

模型分组是 RBAC 资源「model_group:<id>」。用户对某分组有 use 权限，才能用分组里的模型；
不在任何被授权分组里的模型，对他不可用（fail-closed）。
"""

from collections import defaultdict

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from .catalog import ModelMixin
from .core import RESOURCE_TYPE, check_id, group_view, model_view, new_id
from .errors import Denied, Invalid, NotFound
from .schemas import GroupCreate, GroupUpdate, GroupView, ModelView
from .tables import Model, ModelGroup, ModelGroupItem, ModelProvider


class ModelCatalog(ModelMixin):
    async def create_group(self, actor_id: str, workspace_id: str, body: GroupCreate) -> GroupView:
        self._require_admin(actor_id, workspace_id)
        row = ModelGroup(id=new_id("mg"), workspace_id=workspace_id, name=body.name.strip(),
                         description=body.description)
        async with self._tx(workspace_id) as session:
            session.add(row)
            await session.flush()
            model_ids = await self._set_items(session, workspace_id, row.id, body.model_ids)
        return group_view(row, model_ids)

    async def list_groups(self, actor_id: str, workspace_id: str) -> tuple[GroupView, ...]:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            rows = (await session.scalars(select(ModelGroup).order_by(ModelGroup.name))).all()
            items = (await session.execute(select(ModelGroupItem.group_id, ModelGroupItem.model_id)
                                           .order_by(ModelGroupItem.model_id))).all()
        members: dict[str, list[str]] = defaultdict(list)
        for group_id, model_id in items:
            members[group_id].append(model_id)
        return tuple(group_view(row, tuple(members[row.id])) for row in rows)

    async def update_group(self, actor_id: str, workspace_id: str, group_id: str, body: GroupUpdate) -> GroupView:
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            row = await self._group(session, group_id)
            if body.name is not None:
                row.name = body.name.strip()
            if body.description is not None:
                row.description = body.description
            if body.model_ids is not None:
                model_ids = await self._set_items(session, workspace_id, row.id, body.model_ids)
            else:
                model_ids = tuple((await session.scalars(select(ModelGroupItem.model_id)
                                                         .where(ModelGroupItem.group_id == row.id)
                                                         .order_by(ModelGroupItem.model_id))).all())
        return group_view(row, model_ids)

    async def delete_group(self, actor_id: str, workspace_id: str, group_id: str) -> None:
        """删分组不清 casbin 授权：分组 id 随机且不复用，残留授权指向不存在的资源，不放宽任何权限。"""
        self._require_admin(actor_id, workspace_id)
        async with self._tx(workspace_id) as session:
            row = await self._group(session, group_id)
            await session.delete(row)

    async def _group(self, session, group_id: str) -> ModelGroup:
        check_id(group_id, "group_id")
        row = await session.scalar(select(ModelGroup).where(ModelGroup.id == group_id))
        if row is None:
            raise NotFound("模型分组不存在")
        return row

    async def _set_items(self, session, workspace_id: str, group_id: str,
                         model_ids: tuple[str, ...]) -> tuple[str, ...]:
        wanted = tuple(sorted({check_id(m, "model_id") for m in model_ids}))
        if wanted:
            found = set((await session.scalars(select(Model.id).where(Model.id.in_(wanted)))).all())
            if found != set(wanted):
                raise Invalid("分组里有不存在的模型")
        await session.execute(delete(ModelGroupItem).where(ModelGroupItem.group_id == group_id))
        if wanted:
            await session.execute(insert(ModelGroupItem).values(
                [{"group_id": group_id, "model_id": m, "workspace_id": workspace_id} for m in wanted]))
        return wanted

    # ---- 使用侧 ----

    async def usable_models(self, user_id: str, workspace_id: str) -> tuple[ModelView, ...]:
        """用户能用的、已启用的模型：至少在一个他有 use 权限的分组里。"""
        check_id(workspace_id, "workspace_id")
        visible = self._auth.visible_resources(user_id, workspace_id, RESOURCE_TYPE, "use")
        if not visible.all_ and not visible.ids:
            return ()
        async with self._tx(workspace_id) as session:
            stmt = (select(Model, ModelGroupItem.group_id)
                    .join(ModelGroupItem, ModelGroupItem.model_id == Model.id)
                    .join(ModelProvider, ModelProvider.id == Model.provider_id)
                    .where(Model.enabled.is_(True), ModelProvider.enabled.is_(True)).order_by(Model.alias))
            if not visible.all_:
                stmt = stmt.where(ModelGroupItem.group_id.in_(visible.ids))
            rows = (await session.execute(stmt)).all()
        seen: dict[str, ModelView] = {}
        for model, _group in rows:
            seen.setdefault(model.id, model_view(model))
        return tuple(seen.values())

    async def ensure_usable(self, user_id: str, workspace_id: str, alias: str) -> ModelView:
        """会话选模型时调用：不可用一律 Denied，不区分「不存在」与「没授权」。"""
        for model in await self.usable_models(user_id, workspace_id):
            if model.alias == alias:
                return model
        raise Denied("无权使用该模型")
