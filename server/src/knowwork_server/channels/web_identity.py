"""网页嵌入的认证访问：接入方系统（例如客户的 BI）告诉我们来的是谁。

接入方后端用渠道的认证密钥签一个 JWT（HS256，各语言都有现成的库）交给页面，嵌入页拿它换会话令牌（web.py）。
不让接入方回调我们的接口：少一次服务器到服务器的调用，内网里的接入方服务器访问不到 KnowWork 也能用。

- 校验：只认 HS256；要有 sub（接入方系统里稳定不变的用户编号，不用会改的用户名）、exp、jti；签发到过期不超过 10 分钟
  （防止签一个长期有效的到处用）；jti 用过一次就作废（防重放，记到过期为止）；
- 对人：
  - 渠道选了组织来源（例如企业微信）：sub 就是那个来源里的用户 ID，在对照表里找（同 IM 认人），按本人的权限；
  - 没选：在这个渠道自己的身份域 `embed:<渠道 id>` 里找；没有时按 email 对空间里的人（对上后记进对照表，下次直接认）；
    再没有、渠道开了自动建，就建一个外部用户（没有邮箱和密码，只能从这个渠道进来），以成员身份进空间；
  管理员把人移出空间后不会因为再次进来被加回去；
- 组：外部用户每次进来按 groups 与渠道的组对照，同步对照里那几个用户组的成员身份（权限按组配，人员变动不用改）；
  已有成员的组在 KnowWork 里管，不动。权限只加不减，要单独收窄某个人就别让他进大组。
"""

import logging
import time
from collections.abc import Collection
from dataclasses import dataclass
from typing import Any, Protocol

from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import OctKey
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..identity.im_identities import INACTIVE, ImIdentities, resolve
from ..identity.models import USER_EXTERNAL, USER_PERSON, ExternalIdentity
from .models import Channel
from .settings import read_settings

log = logging.getLogger(__name__)

ALGORITHM = "HS256"
MAX_LIFETIME = 600  # 签发到过期最多几秒
LEEWAY = 60  # 两边时钟差多少还认
MAX_SUBJECT = 128
MAX_NAME = 100
MAX_GROUPS = 50
MAX_GROUP = 100
OWN_REALM = "embed:"  # 渠道自己的身份域：embed:<渠道 id>


class Rejected(Exception):
    """令牌不对，或者认不出、不让进；消息直接给访客看。"""


@dataclass(frozen=True)
class Claims:
    subject: str
    name: str
    email: str
    groups: tuple[str, ...]
    expires_at: int
    token_id: str


def _text(value: Any, limit: int) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def verify(token: str, secret: str, *, now: float | None = None) -> Claims:
    """校验接入方签的 JWT，取出要用的几项。"""
    if not secret:
        raise Rejected("这个入口还没配好认证密钥，请联系管理员")
    current = int(now if now is not None else time.time())
    try:
        decoded = jwt.decode(token, OctKey.import_key(secret), algorithms=[ALGORITHM])
        registry = jwt.JWTClaimsRegistry(now=current, leeway=LEEWAY, sub={"essential": True},
                                         exp={"essential": True}, jti={"essential": True})
        registry.validate(decoded.claims)
    except (JoseError, ValueError) as error:
        raise Rejected("登录信息无效或已过期，请刷新页面再试") from error
    claims = decoded.claims
    subject = _text(claims.get("sub"), MAX_SUBJECT + 1)
    token_id = _text(claims.get("jti"), 129)
    expires_at, issued_at = claims.get("exp"), claims.get("iat", current)
    if not subject or len(subject) > MAX_SUBJECT or not token_id or len(token_id) > 128:
        raise Rejected("登录信息无效，请联系管理员（sub、jti 不对）")
    if not isinstance(expires_at, int) or not isinstance(issued_at, int) or expires_at - issued_at > MAX_LIFETIME \
            or expires_at - current > MAX_LIFETIME + LEEWAY:
        raise Rejected("登录信息有效期太长，请联系管理员（不超过 10 分钟）")
    raw_groups = claims.get("groups") or []
    groups = tuple(dict.fromkeys(
        _text(group, MAX_GROUP) for group in (raw_groups if isinstance(raw_groups, list) else [])[:MAX_GROUPS]
        if _text(group, MAX_GROUP)))
    return Claims(subject=subject, name=_text(claims.get("name"), MAX_NAME),
                  email=_text(claims.get("email"), 320).lower(), groups=groups, expires_at=expires_at,
                  token_id=token_id)


class People(Protocol):
    """UserService 里用到的部分。"""

    async def get(self, user_id: str) -> Any: ...

    async def find_by_email(self, email: str) -> Any: ...

    async def create_external_account(self, name: str) -> Any: ...

    async def rename(self, user_id: str, name: str) -> None: ...


class Membership(Protocol):
    """AccessService 里用到的部分。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    async def join_workspace(self, user_id: str, workspace_id: str) -> bool: ...

    async def sync_managed_groups(self, workspace_id: str, user_id: str, *, managed: Collection[str],
                                  wanted: Collection[str]) -> None: ...


class EmbedPeople:
    def __init__(self, sessionmaker: async_sessionmaker, people: People, access: Membership,
                 identities: ImIdentities | None = None) -> None:
        """sessionmaker 是不声明工作空间的那种（身份表是全局的）。"""
        self._sessionmaker = sessionmaker
        self._people = people
        self._access = access
        self._identities = identities

    async def person(self, channel: Channel, claims: Claims) -> str:
        """这个人在 KnowWork 里是谁（用户 id）；认不出或不让进抛 Rejected。"""
        settings = read_settings(channel.settings)
        realm, own = await self._realm(channel, settings.identity_source)
        found = await resolve(self._sessionmaker, realm, {"subject": claims.subject})
        if found.reason == INACTIVE:
            raise Rejected("你的账号已停用，请联系管理员")
        user_id = found.user_id
        if user_id is None and not own:
            raise Rejected("组织来源里还找不到你，请联系管理员同步通讯录")
        if user_id is None:
            user_id = await self._match(channel, realm, claims, settings.auto_create)
        if not self._access.check(user_id, channel.workspace_id, "workspace", "use"):
            raise Rejected("你还不是这个空间的成员，请联系管理员")
        user = await self._people.get(user_id)
        if user is None or not user.active:
            raise Rejected("你的账号已停用，请联系管理员")
        if user.kind == USER_EXTERNAL:
            if claims.name:
                await self._people.rename(user_id, claims.name)
            if settings.group_map:
                wanted = {settings.group_map[group] for group in claims.groups if group in settings.group_map}
                await self._access.sync_managed_groups(channel.workspace_id, user_id,
                                                       managed=set(settings.group_map.values()), wanted=wanted)
        return user_id

    async def _realm(self, channel: Channel, source_id: str | None) -> tuple[str, bool]:
        if not source_id:
            return f"{OWN_REALM}{channel.id}", True
        source = await self._identities.get(source_id) if self._identities is not None else None
        if source is None:
            raise Rejected("认人用的组织来源不在了，请联系管理员")
        return source.realm, False

    async def _match(self, channel: Channel, realm: str, claims: Claims, auto_create: bool) -> str:
        """对照表里没有：按邮箱对空间里的人；再没有就按设置建外部用户。对上、建好的都记进对照表。
        同一个人同时打开几个页面时都会走到这里：按（身份域，用户编号）加事务级的 advisory 锁排队，后进锁的再查一次对照表，
        免得建出几个外部用户（锁随这个事务提交或回滚放开）。"""
        async with self._sessionmaker() as session, session.begin():
            await session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                                  {"key": f"{realm}\x1f{claims.subject}"})
            found = await resolve(self._sessionmaker, realm, {"subject": claims.subject})
            if found.user_id is not None:
                return found.user_id
            user_id = await self._pick(channel, claims, auto_create)
            await session.execute(insert(ExternalIdentity).values(realm=realm, subject=claims.subject, user_id=user_id)
                                  .on_conflict_do_nothing())
        log.info("embed channel %s matched %s to user %s", channel.id, claims.subject, user_id)
        return user_id

    async def _pick(self, channel: Channel, claims: Claims, auto_create: bool) -> str:
        """按邮箱对空间里的人（只认成员、在用的、普通账号）；对不上按设置建外部用户并加进空间。"""
        if claims.email:
            user = await self._people.find_by_email(claims.email)
            if (user is not None and user.active and user.kind == USER_PERSON
                    and self._access.check(user.id, channel.workspace_id, "workspace", "use")):
                return user.id
        if not auto_create:
            raise Rejected("还没有对上 KnowWork 的账号，请联系管理员开通")
        user = await self._people.create_external_account(claims.name or f"外部用户 {claims.subject[:12]}")
        await self._access.join_workspace(user.id, channel.workspace_id)
        return user.id
