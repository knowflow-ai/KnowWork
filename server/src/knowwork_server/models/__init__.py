"""模型接入：厂商、模型、连通测试与用量。模型对空间成员一律可用（不按人授权）。

装配入口：
- build_models_service(sessionmaker, authorizer, master_key) -> ModelsService；
- api.build_router(...) 挂公开应用；gateway.model_gateway.build_router(..., resolver=catalog, http=...) 挂内部应用；
- usage.usage_from_event 把宿主 assistant/message 事件转成 UsageEntry，交给 UsageRecorder.record；
- client.ModelClient：业务服务与 worker 自己调模型（嵌入、重排、看图），给扩展用；worker 里没有管理权限，
  用 catalog_for_worker 建一个只能取路由的目录。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from .client import ModelCallError, ModelClient, Slots
from .core import Authorizer
from .crypto import SecretBox
from .recorder import DEFAULT_TIMEZONE, GroupBy, UsageRecorder, UsageSummary
from .schemas import Route
from .service import ModelCatalog
from .tables import MODELS_RLS_TABLES, MODELS_TABLES
from .usage import TurnCallBudget, UsageContext, UsageEntry, model_from_header, usage_from_event


@dataclass(frozen=True)
class ModelsService:
    catalog: ModelCatalog
    recorder: UsageRecorder


class _NoAdmin:
    """worker 进程不管理模型，只取路由：任何管理判定都不成立。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool:
        return False


def catalog_for_worker(sessionmaker: async_sessionmaker, master_key: str) -> ModelCatalog:
    return ModelCatalog(sessionmaker, _NoAdmin(), SecretBox.from_master_key(master_key))


def build_models_service(sessionmaker: async_sessionmaker, authorizer: Authorizer, master_key: str, *,
                         timezone: str = DEFAULT_TIMEZONE) -> ModelsService:
    box = SecretBox.from_master_key(master_key)
    return ModelsService(catalog=ModelCatalog(sessionmaker, authorizer, box),
                         recorder=UsageRecorder(sessionmaker, timezone=timezone))


__all__ = [
    "MODELS_RLS_TABLES",
    "MODELS_TABLES",
    "GroupBy",
    "ModelCallError",
    "ModelCatalog",
    "ModelClient",
    "ModelsService",
    "Route",
    "Slots",
    "TurnCallBudget",
    "UsageContext",
    "UsageEntry",
    "UsageRecorder",
    "UsageSummary",
    "build_models_service",
    "catalog_for_worker",
    "model_from_header",
    "usage_from_event",
]
