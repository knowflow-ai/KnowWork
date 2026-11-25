"""词库来源：管理员自己填的「自定义词表」之外，扩展可以登记现成的词表（例如一份内置的常见违规词），配置页列出来、
每个空间自己勾。开源版不登记任何来源，只有自定义词表。

来源的词随扩展的包发，进程内不变：编自动机时和这个空间的自定义词合在一起，按（策略版本，勾了哪几个）缓存。
"""

import re
from collections.abc import Callable
from dataclasses import dataclass

CUSTOM_SOURCE = "custom"  # 自定义词表（管理员填的），保留的名字，扩展不能用
_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


@dataclass(frozen=True)
class WordSource:
    id: str  # 存进策略的名字，小写字母、数字、连字符
    label: str  # 配置页上的名字，例如「内置词表」
    description: str  # 一句说明：大概多少词、哪几类
    words: Callable[[], tuple[str, ...]]  # 这份词表（规范化以后、去过重），可以缓存


class WordSources:
    """扩展在 setup_api 里登记；配置页、检查服务读。登记的先后就是配置页上的顺序。"""

    def __init__(self) -> None:
        self._sources: dict[str, WordSource] = {}

    def register(self, source: WordSource) -> None:
        if not _ID.match(source.id) or source.id == CUSTOM_SOURCE:
            raise ValueError(f"词库来源的名字不合规：{source.id!r}")
        if source.id in self._sources:
            raise ValueError(f"词库来源 {source.id} 已经登记过")
        self._sources = {**self._sources, source.id: source}

    def all(self) -> tuple[WordSource, ...]:
        return tuple(self._sources.values())

    def get(self, source_id: str) -> WordSource | None:
        return self._sources.get(source_id)

    def ids(self) -> frozenset[str]:
        return frozenset(self._sources)
