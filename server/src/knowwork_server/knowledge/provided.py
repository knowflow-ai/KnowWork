"""扩展提供的知识源。它不在 knowledge_source 表里，知识源管理页里看不到也改不了；
开放范围是它自己的全部知识库，谁能查只看知识库授权（knowledge_base:<id>.<知识库>），和配置的知识源一样走两层收窄。
扩展在业务服务启动时登记，之后只读。"""

import re
from dataclasses import dataclass

from .models import KnowledgeSource
from .store import SourceRecord

_ID = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")


@dataclass(frozen=True)
class ProvidedSource:
    id: str
    name: str
    kind: str
    source: KnowledgeSource

    def record(self, workspace_id: str) -> SourceRecord:
        return SourceRecord(id=self.id, workspace_id=workspace_id, name=self.name, kind=self.kind, base_url="",
                            config={}, secret_ciphertext=None, secret_last4="", enabled=True, created_by="system")


class ProvidedSources:
    def __init__(self) -> None:
        self._items: dict[str, ProvidedSource] = {}

    def add(self, item: ProvidedSource) -> None:
        if not _ID.fullmatch(item.id) or item.id.startswith("ks_"):
            raise ValueError(f"invalid provided knowledge source id: {item.id!r}")
        if item.id in self._items:
            raise ValueError(f"knowledge source already provided: {item.id}")
        self._items = {**self._items, item.id: item}

    def get(self, source_id: str) -> ProvidedSource | None:
        return self._items.get(source_id)

    def all(self) -> tuple[ProvidedSource, ...]:
        return tuple(self._items.values())
