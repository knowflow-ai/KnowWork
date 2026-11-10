"""渠道的管理（空间所有者和管理员）。

凭据保密的部分加密存、接口只回末四位；改配置、启停、删除后让运行管理重连这个渠道。

每个渠道有一个同 id 的渠道账号：建渠道时建好、以成员角色进空间（成员默认只有空间的使用权，别的一概没有），
选的知识库（回答方式查别的资源时是那一类，例如数据源）同步成它的「使用」授权，授权页上看得到；它在渠道用得到的这几类
资源上的授权由渠道配置管，别处加的保存时会收回。删渠道时移出空间（授权一起收回）、账号禁用不删，运行记录、用量里
还能看到是哪个机器人答的。

回答方式从登记表里选（answer_modes.py）：配渠道的人能用、写了查哪类资源的。

认人（audience = member）：私聊按提问人本人回答，要选一个同平台的组织来源（people.py）；选的资料只管群聊，可以不选。
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any, Protocol

from ..access.errors import NotFound as NotMember
from ..conversations.answer_modes import KNOWLEDGE, AnswerModes, ChannelResource, ModeContext
from ..identity.im_identities import ImIdentities
from ..knowledge.crypto import mask
from .models import Channel
from .people import MEMBER, PLATFORM_LABELS, PUBLIC
from .settings import ChannelSettings, read_settings
from .store import ChannelStore, SessionRow
from .supervisor import ChannelSecrets, ChannelSupervisor

WORKSPACE = "workspace"


class Access(Protocol):
    """AccessService 里渠道用到的部分。"""

    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...

    async def join_workspace(self, user_id: str, workspace_id: str) -> bool: ...

    async def list_grants(self, actor_id: str, workspace_id: str, *, subject: str | None = None) -> Sequence[Any]: ...

    async def grant(self, actor_id: str, workspace_id: str, subject: str, resource: str, action: str) -> Any: ...

    async def revoke(self, actor_id: str, workspace_id: str, subject: str, resource: str) -> None: ...

    async def remove_member(self, actor_id: str, workspace_id: str, user_id: str) -> None: ...


class Accounts(Protocol):
    """UserService 里渠道用到的部分。"""

    async def ensure_channel_account(self, user_id: str, name: str) -> Any: ...

    async def set_disabled(self, user_id: str, disabled: bool) -> bool: ...

    async def get_many(self, user_ids: Sequence[str]) -> dict[str, Any]: ...


class Forbidden(Exception):
    pass


class NotFound(Exception):
    pass


class Invalid(Exception):
    pass


@dataclass(frozen=True)
class ChannelInput:
    name: str
    platform: str
    enabled: bool
    bot_id: str
    bot_name: str
    secret: str | None  # 修改时不给就保留原来的
    settings: ChannelSettings
    audience: str = PUBLIC


@dataclass(frozen=True)
class ChannelView:
    id: str
    platform: str
    name: str
    enabled: bool
    bot_id: str
    bot_name: str
    secret_mask: str
    secret_set: bool
    settings: dict
    status: str
    last_error: str
    status_at: datetime | None
    sessions: int
    created_at: datetime | None
    audience: str = PUBLIC


@dataclass(frozen=True)
class SessionView:
    external_id: str
    display_name: str
    chat_type: str  # direct / group
    chat_id: str
    conversation_id: str
    title: str
    updated_at: datetime
    user_id: str = ""  # 认出的人是本人的用户 id；渠道账号回答的为空
    user_name: str = ""


@dataclass(frozen=True)
class StrangerView:
    external_id: str
    display_name: str
    reason: str  # unknown / inactive / not_member
    attempts: int
    last_at: datetime


@dataclass(frozen=True)
class AnswerModeView:
    id: str
    label: str
    description: str
    resource_type: str  # 群聊（不认人时私聊也是）按哪类资源选：knowledge_base 选知识库，别的按授权页登记的那一类选
    resource_label: str


@dataclass(frozen=True)
class IdentitySourceView:
    id: str
    name: str
    login: bool  # 开了「用它登录」：没认出的人能点免登入口自己对上


class ChannelAdmin:
    def __init__(self, store: ChannelStore, secrets: ChannelSecrets, supervisor: ChannelSupervisor,
                 access: Access, accounts: Accounts, identities: ImIdentities | None = None,
                 modes: AnswerModes | None = None) -> None:
        self._store = store
        self._secrets = secrets
        self._supervisor = supervisor
        self._access = access
        self._accounts = accounts
        self._identities = identities
        self._modes = modes or AnswerModes()

    def _admin(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise Forbidden()

    async def channels(self, actor: str, workspace_id: str) -> list[ChannelView]:
        self._admin(actor, workspace_id)
        channels = await self._store.of_workspace(workspace_id)
        counts = await self._store.session_counts(workspace_id)
        return [self._view(channel, counts.get(channel.id, 0)) for channel in channels]

    async def create(self, actor: str, workspace_id: str, data: ChannelInput) -> ChannelView:
        self._admin(actor, workspace_id)
        if not data.secret:
            raise Invalid("请填写 Secret")
        data = await self._checked(actor, workspace_id, data)
        await self._unique_bot(workspace_id, data, None)
        channel = Channel(workspace_id=workspace_id, platform=data.platform, audience=data.audience,
                          name=data.name.strip(), enabled=data.enabled, settings=data.settings.model_dump(),
                          public_config=_public(data), secret="", status="stopped", last_error="",
                          created_by=actor)
        channel = await self._store.create(channel)
        try:
            await self._attach(actor, workspace_id, channel.id, data)
        except Exception:
            await self._store.delete(workspace_id, channel.id)  # 渠道账号没配好就不留这个渠道
            raise
        sealed = self._secrets.seal(workspace_id, channel.id, {"secret": data.secret})
        channel = await self._store.save(workspace_id, channel.id, secret=sealed)
        await self._supervisor.reload(workspace_id, channel.id)
        return self._view(await self._found(workspace_id, channel.id), 0)

    async def update(self, actor: str, workspace_id: str, channel_id: str, data: ChannelInput) -> ChannelView:
        self._admin(actor, workspace_id)
        data = await self._checked(actor, workspace_id, data)
        await self._found(workspace_id, channel_id)
        await self._unique_bot(workspace_id, data, channel_id)
        values: dict[str, Any] = {"name": data.name.strip(), "enabled": data.enabled, "audience": data.audience,
                                  "settings": data.settings.model_dump(), "public_config": _public(data)}
        if data.secret:
            values["secret"] = self._secrets.seal(workspace_id, channel_id, {"secret": data.secret})
        # 先同步渠道账号的授权再存配置：同步失败时配置不变，页面上看到的知识库和实际能查的对得上
        await self._attach(actor, workspace_id, channel_id, data)
        await self._store.save(workspace_id, channel_id, **values)
        await self._supervisor.reload(workspace_id, channel_id)
        return await self._one(workspace_id, channel_id)

    async def set_enabled(self, actor: str, workspace_id: str, channel_id: str, enabled: bool) -> ChannelView:
        self._admin(actor, workspace_id)
        await self._found(workspace_id, channel_id)
        await self._store.save(workspace_id, channel_id, enabled=enabled)
        await self._supervisor.reload(workspace_id, channel_id)
        return await self._one(workspace_id, channel_id)

    async def delete(self, actor: str, workspace_id: str, channel_id: str) -> None:
        self._admin(actor, workspace_id)
        await self._found(workspace_id, channel_id)
        await self._detach(actor, workspace_id, channel_id)
        await self._store.delete(workspace_id, channel_id)
        await self._supervisor.reload(workspace_id, channel_id)

    async def sessions(self, actor: str, workspace_id: str, channel_id: str) -> list[SessionView]:
        self._admin(actor, workspace_id)
        channel = await self._found(workspace_id, channel_id)
        rows = await self._store.sessions(workspace_id, channel_id)
        # 渠道账号答的（和渠道同 id）不算认出的人；认出的人带上成员姓名
        people = [row.user_id for row in rows if row.user_id and row.user_id != channel.id]
        names = {user_id: getattr(info, "name", "") for user_id, info in
                 (await self._accounts.get_many(people) if people else {}).items()}
        return [_session_view(row, channel.id, names) for row in rows]

    async def strangers(self, actor: str, workspace_id: str, channel_id: str) -> list[StrangerView]:
        """认人的渠道里私聊了却没认出来的人：管理员看谁卡住了、卡在哪一步。"""
        self._admin(actor, workspace_id)
        await self._found(workspace_id, channel_id)
        return [StrangerView(external_id=row.external_id, display_name=row.display_name, reason=row.reason,
                             attempts=row.attempts, last_at=row.last_at)
                for row in await self._store.strangers(workspace_id, channel_id)]

    async def answer_modes(self, actor: str, workspace_id: str) -> list[AnswerModeView]:
        """渠道可选的回答方式：配渠道的人能用、写了查哪类资源的（内置两种查知识库）。"""
        self._admin(actor, workspace_id)
        return [AnswerModeView(id=mode.id, label=mode.label, description=mode.description,
                               resource_type=mode.channel.type, resource_label=mode.channel.label)
                for mode in await self._modes.for_channels(ModeContext(workspace_id, actor)) if mode.channel]

    async def identity_sources(self, actor: str, workspace_id: str, platform: str) -> list[IdentitySourceView]:
        """认人可选的组织来源：只列同平台的。没有能认人的扩展时为空，界面上这一项置灰。"""
        self._admin(actor, workspace_id)
        if self._identities is None:
            return []
        return [IdentitySourceView(id=source.id, name=source.name, login=bool(source.login_url))
                for source in await self._identities.list(platform)]

    async def _checked(self, actor: str, workspace_id: str, data: ChannelInput) -> ChannelInput:
        """回答方式要是配的人能用、写了查哪类资源的，只留它用的那类资料；认人要选同平台、还在的组织来源，
        群聊的资料可以不选；不认人至少选一个，不留来源。"""
        if data.audience not in (PUBLIC, MEMBER):
            raise Invalid("提问的人只能是「不认人」或「认出是谁」")
        offered = {mode.id: mode.channel for mode in await self._modes.for_channels(ModeContext(workspace_id, actor))}
        resource = offered.get(data.settings.answer_mode)
        if resource is None:
            raise Invalid("这种回答方式在渠道里用不了，请重新选")
        data = replace(data, settings=_only(data.settings, resource))
        if data.audience == PUBLIC:
            _require(data.settings, resource)
            return replace(data, settings=data.settings.model_copy(update={"identity_source": None}))
        source_id = data.settings.identity_source
        if not source_id:
            raise Invalid("请选认人用的组织来源")
        source = await self._identities.get(source_id) if self._identities is not None else None
        if source is None:
            raise Invalid("这个组织来源不存在了，请重新选")
        if source.platform != data.platform:
            raise Invalid("组织来源和机器人不是同一个平台")
        return data

    # ---- 渠道账号 ----

    async def _attach(self, actor: str, workspace_id: str, account: str, data: ChannelInput) -> None:
        """建好或更新渠道账号，让它是空间成员，在渠道用得到的几类资源上的授权正好是这次选的。"""
        label = PLATFORM_LABELS.get(data.platform, data.platform)
        await self._accounts.ensure_channel_account(account, f"{data.name.strip()}（{label}机器人）")
        await self._access.join_workspace(account, workspace_id)
        subject = f"user:{account}"
        managed = self._modes.channel_types()
        held = {grant.resource: grant.action
                for grant in await self._access.list_grants(actor, workspace_id, subject=subject)
                if grant.resource.partition(":")[0] in managed}
        resource = self._modes.channel_resource(data.settings.answer_mode) or KNOWLEDGE
        wanted = {f"{KNOWLEDGE.type}:{base}" for base in data.settings.bases} | {
            f"{resource.type}:{item}" for item in data.settings.resources}
        try:
            for resource in sorted(set(held) - wanted):
                await self._access.revoke(actor, workspace_id, subject, resource)
            for resource in sorted(wanted):
                if held.get(resource) != "use":
                    await self._access.grant(actor, workspace_id, subject, resource, "use")
        except ValueError as error:  # 权限模块的格式错误（InvalidInput）是 ValueError
            raise Invalid(str(error)) from error

    async def _detach(self, actor: str, workspace_id: str, account: str) -> None:
        try:
            await self._access.remove_member(actor, workspace_id, account)
        except NotMember:
            pass  # 已经不是成员（被人从成员页移出去了）
        await self._accounts.set_disabled(account, True)

    async def _unique_bot(self, workspace_id: str, data: ChannelInput, channel_id: str | None) -> None:
        """同一个机器人只能配一个渠道：两个渠道连同一个机器人会来回互踢。别的空间的查不到，靠运行管理按机器人加的锁兜底。"""
        for channel in await self._store.of_workspace(workspace_id):
            if (channel.id != channel_id and channel.platform == data.platform
                    and (channel.public_config or {}).get("bot_id") == data.bot_id.strip()):
                raise Invalid(f"这个机器人已经配在渠道「{channel.name}」里了")

    async def _found(self, workspace_id: str, channel_id: str) -> Channel:
        channel = await self._store.get(workspace_id, channel_id)
        if channel is None:
            raise NotFound()
        return channel

    async def _one(self, workspace_id: str, channel_id: str) -> ChannelView:
        counts = await self._store.session_counts(workspace_id)
        return self._view(await self._found(workspace_id, channel_id), counts.get(channel_id, 0))

    def _view(self, channel: Channel, sessions: int) -> ChannelView:
        secret = self._secrets.open(channel).get("secret", "")
        public = channel.public_config or {}
        return ChannelView(id=channel.id, platform=channel.platform, name=channel.name, enabled=channel.enabled,
                           bot_id=str(public.get("bot_id", "")), bot_name=str(public.get("bot_name", "")),
                           secret_mask=mask(secret), secret_set=bool(secret),
                           settings=read_settings(channel.settings).model_dump(), status=channel.status,
                           last_error=channel.last_error, status_at=channel.status_at, sessions=sessions,
                           created_at=channel.created_at, audience=channel.audience or PUBLIC)


def _only(settings: ChannelSettings, resource: ChannelResource) -> ChannelSettings:
    """只留回答方式用的那类资料：查知识库的不留别的资源，查别的不留知识库。"""
    if resource == KNOWLEDGE:
        return settings.model_copy(update={"resources": []})
    return settings.model_copy(update={"bases": []})


def _require(settings: ChannelSettings, resource: ChannelResource) -> None:
    if not (settings.bases if resource == KNOWLEDGE else settings.resources):
        raise Invalid(f"至少选一个{resource.label}：机器人只按选的{resource.label}回答")


def _public(data: ChannelInput) -> dict[str, str]:
    return {"bot_id": data.bot_id.strip(), "bot_name": data.bot_name.strip()}


def _session_view(row: SessionRow, account: str, names: dict[str, str]) -> SessionView:
    """会话键：私聊 dm:<平台用户>，群里 group:<群>:<平台用户>（见 IncomingMessage.session_key）。"""
    kind, _, rest = row.session_key.partition(":")
    chat_id, external_id = rest.rsplit(":", 1) if kind == "group" and ":" in rest else ("", rest)
    person = row.user_id if row.user_id and row.user_id != account else ""
    return SessionView(external_id=external_id, display_name=row.display_name,
                       chat_type="group" if kind == "group" else "direct", chat_id=chat_id,
                       conversation_id=row.conversation_id, title=row.title, updated_at=row.updated_at,
                       user_id=person, user_name=names.get(person, "") if person else "")
