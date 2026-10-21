"""模型接入：厂商、模型、模型分组（RBAC 资源 model_group）、连通测试与用量。

装配入口：
- build_models_service(sessionmaker, authorizer, master_key) -> ModelsService；
- api.build_router(...) 挂公开应用；gateway.model_gateway.build_router(..., resolver=catalog, http=...) 挂内部应用；
- usage.usage_from_event 把宿主 assistant/message 事件转成 UsageEntry，交给 UsageRecorder.record。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

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


def build_models_service(sessionmaker: async_sessionmaker, authorizer: Authorizer, master_key: str, *,
                         timezone: str = DEFAULT_TIMEZONE) -> ModelsService:
    box = SecretBox.from_master_key(master_key)
    return ModelsService(catalog=ModelCatalog(sessionmaker, authorizer, box),
                         recorder=UsageRecorder(sessionmaker, timezone=timezone))


__all__ = [
    "MODELS_RLS_TABLES", "MODELS_TABLES", "GroupBy", "ModelCatalog", "ModelsService", "Route", "TurnCallBudget",
    "UsageContext", "UsageEntry", "UsageRecorder", "UsageSummary", "build_models_service", "model_from_header",
    "usage_from_event",
]
