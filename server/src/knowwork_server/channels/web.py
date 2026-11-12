"""网页渠道（channels/web.py）：嵌入页、嵌入脚本与分享链接背后的那部分。

- 发布令牌：`<空间>~<随机串>`，嵌入地址里就是它；带着空间，凭它进到空间查渠道（渠道表按空间做行级安全，没有空间查不到）。
  它本来就写在接入方网页的源码里，不是秘密：能在哪里用由「允许嵌入的网站」管（网关按它给 frame-ancestors），用多少由
  限次管。管理员重置以后旧的作废，已经发出去的会话令牌也跟着作废（会话令牌里记着它的指纹）；
- 公开访问：访客带着嵌入页随机生成的访客 id 来换会话令牌，落到渠道账号；会话位置按访客记（`v:<访客 id>`）；
- 认证访问（audience = member）：接入方后端签的 JWT 换会话令牌，落到认出来的那个人（web_identity.py），按他自己的
  权限答，会话记在他名下；会话位置按人记（`u:<用户 id>`）；
- 会话令牌只放嵌入页的内存（web_tokens.py），每次用都核对渠道还在、还开着、还是网页渠道、发布令牌与认证密钥没换过；
- 会话：从嵌入来的只能碰自己从这个渠道开的会话（principals.conversation_actor 调 owns，带会话 id 的接口一处管住），
  新建时记下会话位置，列表只列自己的，发消息前限次；网页渠道的会话固定用渠道选的回答方式与资料（对话范围）；
- 挂了对象的（例如专家，targets.py）：小窗名字、开场白、快捷提问没填时用对象的；新开的会话挂到对象上，之后按对象自己
  登记的范围与说明答；对象删了入口就关了。
"""

import re
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..access.vocab import InvalidInput, validate_id
from ..auth.principal import EMBED, Principal
from ..conversations.answer_modes import KNOWLEDGE, AnswerModes
from ..conversations.api import Actor, Throttled
from ..conversations.scopes import ConversationScope
from ..db.base import in_workspace
from ..identity.embed import EMBED_SCOPES
from ..kernel.tokens import TokenError
from .limits import Limiter
from .models import Channel
from .people import MEMBER, PUBLIC
from .settings import WebOptions, read_settings, read_web_options
from .store import WEB, ChannelStore
from .supervisor import ChannelSecrets
from .targets import ChannelTargets, Target
from .web_identity import LEEWAY, EmbedPeople, Rejected, verify
from .web_tokens import EmbedTokens, key_print


class Users(Protocol):
    async def get(self, user_id: str) -> Any: ...

KEY_SEPARATOR = "~"
KEY_RANDOM = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
OPEN_PER_MINUTE = 30  # 同一个 IP 每分钟最多换几次会话令牌（每打开一次嵌入页换一次）
IP_FACTOR = 3  # 同一个 IP 每分钟能问的是单个访客的几倍（公司出口 IP 后面有很多人）
ACT_FACTOR = 3  # 卡片上的操作（下载、改条件、下钻）每分钟能点的是能问的几倍：不过模型，但每次都重查一遍数据库
DAY = 86_400
MAX_LABEL = 100


def new_web_key(workspace_id: str) -> str:
    return f"{workspace_id}{KEY_SEPARATOR}{secrets.token_urlsafe(18)}"


def workspace_of(web_key: str) -> str | None:
    """发布令牌里的空间；格式不对为空。"""
    workspace_id, separator, random = web_key.rpartition(KEY_SEPARATOR)
    if not separator or not KEY_RANDOM.fullmatch(random):
        return None
    try:
        return validate_id(workspace_id, "workspace_id")
    except InvalidInput:
        return None


class WebChannelGone(Exception):
    """发布令牌对不上，或者渠道停用了、删了：嵌入页显示「这个入口已经关闭」。"""


@dataclass(frozen=True)
class EmbedSession:
    token: str
    expires_at: int
    workspace_id: str
    channel_id: str


@dataclass(frozen=True)
class WebConfig:
    """嵌入页、嵌入脚本不登录就读得到的配置。"""

    title: str
    greeting: str
    starters: tuple[str, ...]
    color: str
    position: str
    answer_mode: str
    access: str  # public（公开访问）、member（认证访问）


def _session_key(actor: Actor) -> str:
    return f"v:{actor.visitor}" if actor.visitor else f"u:{actor.user_id}"


def _fingerprint(channel: Channel) -> str:
    """会话令牌里记的指纹：发布令牌与认证密钥（加密后的）任一重置，已经发出去的会话令牌都作废。"""
    return key_print(f"{(channel.public_config or {}).get('web_key', '')}|{channel.secret or ''}")


def _usable(channel: Channel | None) -> bool:
    return channel is not None and channel.enabled and channel.platform == WEB


class WebChannels:
    def __init__(self, store: ChannelStore, tokens: EmbedTokens, limiter: Limiter, modes: AnswerModes | None = None,
                 *, secrets: ChannelSecrets | None = None, people: EmbedPeople | None = None,
                 users: Users | None = None, targets: ChannelTargets | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        """secrets 解开认证密钥、people 认人、users 核对账号还能不能用：认证访问要用，只做公开访问时可以不给。
        targets 是扩展登记的可挂对象（例如专家）。"""
        self._store = store
        self._tokens = tokens
        self._limiter = limiter
        self._modes = modes or AnswerModes()
        self._secrets = secrets
        self._people = people
        self._users = users
        self._targets = targets or ChannelTargets()
        self._clock = clock

    # ---- 不登录就能读的 ----

    async def channel(self, web_key: str) -> Channel | None:
        workspace_id = workspace_of(web_key)
        if workspace_id is None:
            return None
        with in_workspace(workspace_id):
            return await self._store.by_web_key(workspace_id, web_key)

    async def config(self, web_key: str) -> WebConfig | None:
        channel = await self.channel(web_key)
        if not _usable(channel):
            return None
        settings = read_settings(channel.settings)
        target = await self._target(channel)
        if settings.target and target is None:
            return None  # 挂的对象删了：入口关了
        options = read_web_options(channel.public_config)
        return WebConfig(title=options.title or (target.name if target else channel.name),
                         greeting=options.greeting or (target.greeting if target else ""),
                         starters=tuple(options.starters) or (target.starters if target else ()),
                         color=options.color, position=options.position,
                         answer_mode=target.answer_mode if target else settings.answer_mode,
                         access=channel.audience or PUBLIC)

    async def _target(self, channel: Channel) -> Target | None:
        """渠道挂的对象；没挂、或对象不在了是 None。"""
        ref = read_settings(channel.settings).target
        return await self._targets.get(channel.workspace_id, ref) if ref else None

    async def _open(self, channel: Channel | None) -> bool:
        """能换会话令牌：渠道能用，挂了对象的对象还在。"""
        if not _usable(channel):
            return False
        return not read_settings(channel.settings).target or await self._target(channel) is not None

    async def frame_ancestors(self, web_key: str) -> tuple[str, ...]:
        """允许把嵌入页嵌进去的网站；渠道不在或停用时一个都没有（只能整页打开）。"""
        channel = await self.channel(web_key)
        return tuple(read_web_options(channel.public_config).origins) if _usable(channel) else ()

    # ---- 会话令牌 ----

    async def open_public(self, web_key: str, visitor: str, client_ip: str) -> EmbedSession:
        """公开访问：访客换会话令牌，落到渠道账号。"""
        if not await self._limiter.hit(f"open:{client_ip}", OPEN_PER_MINUTE, 60):
            raise Throttled("打开得太频繁了，请稍后再试")
        channel = await self.channel(web_key)
        if not await self._open(channel) or (channel.audience or PUBLIC) != PUBLIC:
            raise WebChannelGone()
        return self._issue(channel, user_id=channel.id, visitor=visitor)

    async def open_identified(self, web_key: str, token: str, client_ip: str) -> EmbedSession:
        """认证访问：接入方签的 JWT 换会话令牌，落到认出来的那个人（web_identity.py）。令牌只能用一次。"""
        if not await self._limiter.hit(f"open:{client_ip}", OPEN_PER_MINUTE, 60):
            raise Throttled("打开得太频繁了，请稍后再试")
        channel = await self.channel(web_key)
        if (not await self._open(channel) or channel.audience != MEMBER or self._secrets is None
                or self._people is None):
            raise WebChannelGone()
        now = self._clock()
        claims = verify(token, self._secrets.open(channel).get("secret", ""), now=now)
        if not await self._limiter.once(f"jti:{channel.id}:{claims.token_id}", int(claims.expires_at - now) + LEEWAY):
            raise Rejected("这个登录信息已经用过了，请刷新页面再试")
        user_id = await self._people.person(channel, claims)
        return self._issue(channel, user_id=user_id, visitor=None)

    async def renew(self, token: str) -> EmbedSession:
        """用着就续：换一个新的，身份不变。旧的到点自己过期。"""
        principal = await self.resolve(token)
        if principal is None:
            raise WebChannelGone()
        with in_workspace(principal.workspace_id):
            channel = await self._store.get(principal.workspace_id, principal.channel_id)
        return self._issue(channel, user_id=principal.user_id, visitor=principal.visitor)

    async def resolve(self, token: str) -> Principal | None:
        """认会话令牌（identity/embed.py 登记的解析函数）：签名、过期之外，渠道还得在、开着、发布令牌没换过。"""
        try:
            claims = self._tokens.verify(token, now=self._clock())
        except TokenError:
            return None
        with in_workspace(claims.workspace_id):
            channel = await self._store.get(claims.workspace_id, claims.channel_id)
        if not _usable(channel) or _fingerprint(channel) != claims.key_print:
            return None
        if (channel.audience or PUBLIC) == PUBLIC:
            if claims.visitor is None or claims.user_id != channel.id:
                return None
        elif claims.visitor is not None or claims.user_id == channel.id or not await self._active(claims.user_id):
            return None
        return Principal(user_id=claims.user_id, auth_method=EMBED, scopes=EMBED_SCOPES,
                         workspace_id=claims.workspace_id, channel_id=channel.id, visitor=claims.visitor)

    async def _active(self, user_id: str) -> bool:
        """认证访问落到的人账号还能用（停用了、删了的不认）；是不是空间成员由调用者依赖再核对。"""
        user = await self._users.get(user_id) if self._users is not None else None
        return user is not None and user.active

    def _issue(self, channel: Channel, *, user_id: str, visitor: str | None) -> EmbedSession:
        token = self._tokens.issue(workspace_id=channel.workspace_id, channel_id=channel.id, user_id=user_id,
                                   visitor=visitor, key_print=_fingerprint(channel), now=self._clock())
        claims = self._tokens.verify(token, now=self._clock())
        return EmbedSession(token=token, expires_at=claims.expires_at, workspace_id=channel.workspace_id,
                            channel_id=channel.id)

    # ---- 会话（conversations.api.Embedded）----

    async def owns(self, actor: Actor, conversation_id: str) -> bool:
        with in_workspace(actor.workspace_id):
            if actor.visitor is not None:
                current = await self._store.conversation_of(actor.workspace_id, actor.channel_id, _session_key(actor),
                                                            actor.user_id)
                return current == conversation_id
            return await self._store.channel_of_conversation(actor.workspace_id, conversation_id) == actor.channel_id

    async def started(self, actor: Actor, conversation_id: str) -> None:
        """记下会话位置；渠道挂了对象的，把这段对话挂到对象上（之后按对象自己登记的范围与说明答）。"""
        name = f"访客 {actor.visitor[:6]}" if actor.visitor else ""  # 认证访问的人在管理页按用户 id 显示姓名
        with in_workspace(actor.workspace_id):
            await self._store.remember(actor.workspace_id, actor.channel_id, _session_key(actor), conversation_id,
                                       actor.user_id, name)
            channel = await self._store.get(actor.workspace_id, actor.channel_id)
        target = read_settings(channel.settings).target if channel is not None else None
        if target:
            await self._targets.attach(actor.workspace_id, conversation_id, target, actor.user_id)

    async def mine(self, actor: Actor) -> tuple[str, ...]:
        with in_workspace(actor.workspace_id):
            current = await self._store.conversation_of(actor.workspace_id, actor.channel_id, _session_key(actor),
                                                        actor.user_id)
        return (current,) if current else ()

    async def admit(self, actor: Actor, client_ip: str) -> None:
        """发消息前限次：每个访客（认证访问是每个人）每分钟、同一个 IP 每分钟、整个渠道每天。"""
        with in_workspace(actor.workspace_id):
            channel = await self._store.get(actor.workspace_id, actor.channel_id)
        options = read_web_options(channel.public_config) if channel is not None else WebOptions()
        who = actor.visitor or actor.user_id
        if not await self._limiter.hit(f"ask:{actor.channel_id}:{who}", options.per_minute, 60):
            raise Throttled("问得太快了，请稍等一分钟再问")
        if actor.visitor is not None and client_ip and not await self._limiter.hit(
                f"ip:{actor.channel_id}:{client_ip}", options.per_minute * IP_FACTOR, 60):
            raise Throttled("问得太快了，请稍等一分钟再问")
        if not await self._limiter.hit(f"day:{actor.channel_id}", options.per_day, DAY):
            raise Throttled("今天的提问次数已经用完了，明天再来")

    async def admit_new(self, actor: Actor, client_ip: str) -> None:
        """新开对话前限次：每新开一段宿主就建一个会话，拿着一个令牌连着新开会把宿主拖垮。按提问的次数限，不占提问的次数。"""
        options = await self._options(actor)
        await self._limit(actor, client_ip, "new", options.per_minute, "开得太快了，请稍等一分钟再试")

    async def act(self, actor: Actor, client_ip: str) -> None:
        """扩展里嵌入也能用的操作（例如问数卡片上的下载、改条件、下钻，principals.EmbedGuard）前限次：不过模型、不占提问
        的次数，但每次都要重查一遍数据库，所以每个访客（认证访问是每个人）、同一个 IP 每分钟也有上限。"""
        options = await self._options(actor)
        await self._limit(actor, client_ip, "act", options.per_minute * ACT_FACTOR, "操作太快了，请稍等一分钟再试")

    async def _options(self, actor: Actor) -> WebOptions:
        with in_workspace(actor.workspace_id):
            channel = await self._store.get(actor.workspace_id, actor.channel_id)
        return read_web_options(channel.public_config) if channel is not None else WebOptions()

    async def _limit(self, actor: Actor, client_ip: str, kind: str, limit: int, message: str) -> None:
        """按访客（认证访问是每个人）每分钟限次；公开访问另按 IP（公司出口 IP 后面有很多人，放宽几倍）。"""
        if not await self._limiter.hit(f"{kind}:{actor.channel_id}:{actor.visitor or actor.user_id}", limit, 60):
            raise Throttled(message)
        if actor.visitor is not None and client_ip and not await self._limiter.hit(
                f"{kind}-ip:{actor.channel_id}:{client_ip}", limit * IP_FACTOR, 60):
            raise Throttled(message)

    # ---- 对话范围（conversations/scopes.py 的提供者）----

    async def scope(self, workspace_id: str, conversation_id: str) -> ConversationScope | None:
        """网页渠道开的会话：固定用渠道选的回答方式与资料。不是从网页渠道来的不管。"""
        with in_workspace(workspace_id):
            channel_id = await self._store.channel_of_conversation(workspace_id, conversation_id)
            channel = await self._store.get(workspace_id, channel_id) if channel_id else None
        if channel is None or channel.platform != WEB:
            return None
        settings = read_settings(channel.settings)
        if settings.target:
            # 挂了对象的会话本该由对象自己的范围提供者认领（排在前面）；走到这里说明没挂上或对象删了：
            # 什么都不给查，不退回渠道自己的配置（认证访问那样会变成按本人的全部权限答）
            target = await self._target(channel)
            title = read_web_options(channel.public_config).title or (target.name if target else channel.name)
            return ConversationScope(label=title[:MAX_LABEL], answer_mode=settings.answer_mode, knowledge_bases=(),
                                     skills=(), mcp_servers=(), resources=())
        resource = self._modes.channel_resource(settings.answer_mode) or KNOWLEDGE
        title = read_web_options(channel.public_config).title or channel.name
        if channel.audience == MEMBER:
            # 认证访问按本人的权限：选了资料就在他能查的里面再收窄到这几个，没选就不收窄
            return ConversationScope(
                label=title[:MAX_LABEL], answer_mode=settings.answer_mode,
                knowledge_bases=tuple(settings.bases) if settings.bases and resource == KNOWLEDGE else None,
                resources=tuple(f"{resource.type}:{item}" for item in settings.resources)
                if settings.resources and resource != KNOWLEDGE else None)
        if resource == KNOWLEDGE:
            return ConversationScope(label=title[:MAX_LABEL], answer_mode=settings.answer_mode,
                                     knowledge_bases=tuple(settings.bases))
        return ConversationScope(label=title[:MAX_LABEL], answer_mode=settings.answer_mode, knowledge_bases=(),
                                 resources=tuple(f"{resource.type}:{item}" for item in settings.resources))
