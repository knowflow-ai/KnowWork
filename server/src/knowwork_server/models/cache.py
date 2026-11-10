"""模型目录的进程内快照：按别名找模型的热路径（网关每次调模型、每轮编译运行配置、每条用量记录）不再各开一个事务。

2026-11-09 压测：每轮对话约 5 个事务、10 来条查询花在按别名查模型上。厂商与模型全平台只有一份、表很小，
整份读进来按别名查；只在本进程的管理接口里改（catalog.py），改完提交后作废，下一次读重新加载。
别的进程看不到这边的作废，靠 TTL 兜底：worker（catalog_for_worker）不缓存；以后业务服务多副本时，
副本之间最多差一个 TTL。密钥只存密文，用的时候照旧现解。
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .tables import Model, ModelProvider

DEFAULT_TTL_SECONDS = 10.0


@dataclass(frozen=True)
class ProviderEntry:
    id: str
    workspace_id: str
    base_url: str
    api_key_ciphertext: str | None
    enabled: bool


@dataclass(frozen=True)
class ModelEntry:
    id: str
    provider_id: str
    alias: str
    name: str
    capabilities: tuple[str, ...]
    context_length: int | None
    max_output_tokens: int | None
    thinking: str | None
    show_thinking: bool
    input_price_per_mtok: Decimal | None
    output_price_per_mtok: Decimal | None
    enabled: bool
    created_at: datetime


@dataclass(frozen=True)
class CatalogSnapshot:
    models: tuple[ModelEntry, ...]  # 按 (created_at, id) 排好
    providers: Mapping[str, ProviderEntry]

    def by_alias(self, alias: str) -> tuple[ModelEntry, ProviderEntry] | None:
        """别名精确匹配（停用的也找得到，由调用方判）。"""
        model = next((m for m in self.models if m.alias == alias), None)
        return None if model is None else self._with_provider(model)

    def first_enabled_chat(self) -> tuple[ModelEntry, ProviderEntry] | None:
        """最早添加、模型与厂商都启用的对话模型。"""
        for model in self.models:
            found = self._with_provider(model)
            if found is not None and model.enabled and found[1].enabled and "chat" in model.capabilities:
                return found
        return None

    def _with_provider(self, model: ModelEntry) -> tuple[ModelEntry, ProviderEntry] | None:
        provider = self.providers.get(model.provider_id)
        return None if provider is None else (model, provider)


async def load_snapshot(session: AsyncSession) -> CatalogSnapshot:
    """在已声明平台空间的事务里读整份目录。"""
    providers = (await session.scalars(select(ModelProvider))).all()
    models = (await session.scalars(select(Model).order_by(Model.created_at, Model.id))).all()
    return CatalogSnapshot(
        models=tuple(ModelEntry(
            id=m.id, provider_id=m.provider_id, alias=m.alias, name=m.name, capabilities=tuple(m.capabilities or ()),
            context_length=m.context_length, max_output_tokens=m.max_output_tokens, thinking=m.thinking,
            show_thinking=m.show_thinking,
            input_price_per_mtok=m.input_price_per_mtok, output_price_per_mtok=m.output_price_per_mtok,
            enabled=m.enabled, created_at=m.created_at) for m in models),
        providers={p.id: ProviderEntry(id=p.id, workspace_id=p.workspace_id, base_url=p.base_url,
                                       api_key_ciphertext=p.api_key_ciphertext, enabled=p.enabled)
                   for p in providers})


class SnapshotCache:
    """作废按版本号：读之前记下版本，加载期间有人改了（版本变了），这份存下也不算数，下次再读。
    ttl 为 0 时每次都重新加载（等于不缓存）。同时来的读者共用一次加载。"""

    def __init__(self, load: Callable[[], Awaitable[CatalogSnapshot]], *, ttl: float = DEFAULT_TTL_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._load = load
        self._ttl = ttl
        self._clock = clock
        self._version = 0
        self._held: tuple[int, float, CatalogSnapshot] | None = None
        self._loading = asyncio.Lock()

    def invalidate(self) -> None:
        self._version += 1

    async def get(self) -> CatalogSnapshot:
        if self._ttl <= 0:
            return await self._load()
        if (fresh := self._fresh()) is not None:
            return fresh
        async with self._loading:
            if (fresh := self._fresh()) is not None:  # 等锁期间别人已经加载好
                return fresh
            version, loaded_at = self._version, self._clock()
            snapshot = await self._load()
            self._held = (version, loaded_at, snapshot)
            return snapshot

    def _fresh(self) -> CatalogSnapshot | None:
        held = self._held
        if held is None:
            return None
        version, loaded_at, snapshot = held
        return snapshot if version == self._version and self._clock() - loaded_at < self._ttl else None
