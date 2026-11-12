"""渠道可以挂的对象（扩展登记，例如专家）：网页渠道挂上一个，就照它来答。

开源这边只认 `<类型>:<id>` 的形状（channel.settings.target），具体是什么、怎么答由登记它的扩展说了算：
- 名字、回答方式、能查的资料（`<类型>:<id>`，例如 knowledge_base:x、data_source:y）、开场白、快捷提问；
- 从渠道开的会话挂到它上面（attach），之后这段对话的范围、说明都由扩展自己登记的对话范围与每轮上下文给；
- 它改了（例如专家换了能查的资料）、删了，扩展调 changed：挂着它的渠道重新同步渠道账号的授权（公开访问时访客用渠道
  账号查），删了的入口跟着关掉（channels/admin.py、channels/web.py）。
扩展在业务服务启动时登记（ApiContext.channel_targets），之后只读。
"""

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)

TARGET_PATTERN = r"^[a-z_]{1,32}:[A-Za-z0-9_.\-]{1,64}$"


@dataclass(frozen=True)
class Target:
    kind: str  # 登记它的那一类，例如 expert
    id: str
    name: str
    answer_mode: str
    resources: tuple[str, ...] = ()  # 能查的资料，<类型>:<id>；渠道只同步它管的那几类（answer_modes.channel_types）
    greeting: str = ""  # 开场白、快捷提问：网页渠道自己没填时用它的
    starters: tuple[str, ...] = ()

    @property
    def ref(self) -> str:
        return f"{self.kind}:{self.id}"


class TargetKind(Protocol):
    """扩展登记的一类可挂对象。"""

    kind: str
    label: str  # 界面上的叫法，例如「专家」

    async def list(self, workspace_id: str) -> Sequence[Target]: ...

    async def get(self, workspace_id: str, target_id: str) -> Target | None: ...

    async def attach(self, workspace_id: str, conversation_id: str, target_id: str, user_id: str) -> None: ...


# 对象改了、删了：(workspace_id, ref)
Listener = Callable[[str, str], Awaitable[None]]


class ChannelTargets:
    def __init__(self) -> None:
        self._kinds: dict[str, TargetKind] = {}
        self._listeners: list[Listener] = []

    def add(self, kind: TargetKind) -> None:
        self._kinds[kind.kind] = kind

    def label(self, kind: str) -> str | None:
        found = self._kinds.get(kind)
        return found.label if found else None

    async def options(self, workspace_id: str) -> list[Target]:
        """配渠道时能挂的：各类按登记的先后，同一类按扩展给的顺序。"""
        out: list[Target] = []
        for kind in self._kinds.values():
            out.extend(await kind.list(workspace_id))
        return out

    async def get(self, workspace_id: str, ref: str) -> Target | None:
        """ref 指的对象；类型没人登记、对象不在了是 None。"""
        kind, _, target_id = ref.partition(":")
        found = self._kinds.get(kind)
        return await found.get(workspace_id, target_id) if found and target_id else None

    async def attach(self, workspace_id: str, conversation_id: str, ref: str, user_id: str) -> None:
        kind, _, target_id = ref.partition(":")
        found = self._kinds.get(kind)
        if found is None:
            raise LookupError(f"no channel target kind {kind!r}")
        await found.attach(workspace_id, conversation_id, target_id, user_id)

    def listen(self, listener: Listener) -> None:
        self._listeners.append(listener)

    async def changed(self, workspace_id: str, ref: str) -> None:
        """扩展在对象改了、删了以后调。同步失败只记日志、不往回抛：对象那边已经改成了，渠道下次保存、对象下次改时还会同步。"""
        for listener in self._listeners:
            try:
                await listener(workspace_id, ref)
            except Exception:
                log.exception("channel target %s changed but channels did not follow", ref)
