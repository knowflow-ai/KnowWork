"""IM 机器人认人：按组织来源的身份域，把平台给的用户 ID 对到本地用户（方案 docs 里的 IM 认人）。

对照表就是 external_identity：组织同步、用这个来源登录过的人都在里面，登录与同步写的是同一条（external.py）。
组织来源由扩展登记成「身份来源」（id、平台、身份域、免登入口），渠道选一个来认人。

和平台无关：机器人适配器把消息里平台给的各种用户 ID 放进 IncomingMessage.ids（企业微信 userid；飞书 user_id、
open_id、union_id；钉钉 userid、unionId），这里按顺序先拿每个值对主 ID（subject），再按同名的键对别名（aliases），
不写平台分支。接新平台只要组织同步把别名按同样的键存、适配器把 ID 收齐。
"""

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import USER_ACTIVE, ExternalIdentity, User

log = logging.getLogger(__name__)

UNKNOWN = "unknown"  # 对照表里没有：没同步到、也没用这个来源登录过
INACTIVE = "inactive"  # 有，但在这个来源里已离职，或账号停用了


@dataclass(frozen=True)
class ImIdentitySource:
    id: str  # 组织来源 id
    platform: str  # wecom（以后 feishu、dingtalk），和渠道的平台一致才能选
    name: str
    realm: str  # 对照表的身份域
    login_url: str = ""  # 免登入口（本站路径）；来源没开「用它登录」时为空


SourceProvider = Callable[[], Sequence[ImIdentitySource] | Awaitable[Sequence[ImIdentitySource]]]


class ImIdentities:
    """身份来源的登记点：扩展 add 一个提供方，列表与查找时现取（来源在后台改了马上生效）。"""

    def __init__(self) -> None:
        self._providers: list[SourceProvider] = []

    def add(self, provider: SourceProvider) -> None:
        self._providers.append(provider)

    async def list(self, platform: str | None = None) -> list[ImIdentitySource]:
        found: list[ImIdentitySource] = []
        for provider in self._providers:
            try:
                result = provider()
                found.extend(await result if inspect.isawaitable(result) else result)
            except Exception:
                log.exception("im identity source provider failed")
        return [source for source in found if platform is None or source.platform == platform]

    async def get(self, source_id: str) -> ImIdentitySource | None:
        return next((source for source in await self.list() if source.id == source_id), None)


@dataclass(frozen=True)
class Resolved:
    user_id: str | None
    reason: str = ""  # 对不上时：UNKNOWN / INACTIVE


async def resolve(sessionmaker: async_sessionmaker, realm: str, ids: Mapping[str, str]) -> Resolved:
    """在身份域里找这个人。sessionmaker 是不声明工作空间的那种（身份表是全局的）。"""
    given = [(key, value) for key, value in ids.items() if value]
    if not given:
        return Resolved(None, UNKNOWN)
    async with sessionmaker() as session:
        row = None
        for _, value in given:
            row = await session.scalar(select(ExternalIdentity).where(
                ExternalIdentity.realm == realm, ExternalIdentity.subject == value))
            if row is not None:
                break
        if row is None:
            for key, value in given:
                row = await session.scalar(select(ExternalIdentity).where(
                    ExternalIdentity.realm == realm, ExternalIdentity.aliases.contains({key: value})).limit(1))
                if row is not None:
                    break
        if row is None:
            return Resolved(None, UNKNOWN)
        user = await session.get(User, row.user_id)
    if not row.active or user is None or user.status != USER_ACTIVE:
        return Resolved(None, INACTIVE)
    return Resolved(row.user_id)
