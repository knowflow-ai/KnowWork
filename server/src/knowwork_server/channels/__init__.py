"""渠道：企业微信、飞书、钉钉机器人与网页嵌入（嵌入代码、分享链接，channels/web.py）。

主线整合：
    channels = build_channels(master_key, scoped, global_sessionmaker, engine, access, service,
                              accounts=identity.users)
    启动：await channels.supervisor.start()；停机：await channels.supervisor.stop()
    app.include_router(build_router(channels.admin, scoped_principal(identity, WORKSPACE_SCOPE)))
"""

from dataclasses import dataclass
from typing import Any

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..conversations.answer_modes import AnswerModes
from ..identity.im_identities import ImIdentities
from .admin import Access, Accounts, ChannelAdmin
from .api import build_router
from .dingtalk import DingtalkConnection
from .feishu import FeishuConnection
from .limits import Limiter, build_limiter
from .models import Channel
from .people import People
from .pictures import Catalogs, InProcessFetch, Pictures, Sign
from .service import ChannelService, Conversations
from .store import ChannelStore
from .supervisor import ChannelSecrets, ChannelSupervisor
from .targets import ChannelTargets
from .web import WebChannels
from .web_api import build_web_router
from .web_identity import EmbedPeople
from .web_tokens import EmbedTokens
from .wecom import WecomConnection

__all__ = ["Channels", "build_channels", "build_router", "build_web_router"]


@dataclass(frozen=True)
class Channels:
    admin: ChannelAdmin
    service: ChannelService
    supervisor: ChannelSupervisor
    picture_fetch: InProcessFetch
    # 网页渠道（channels/web.py）：公开接口、会话令牌、会话归属与限次、对话范围
    web: WebChannels
    redis: Redis | None = None  # 限次用的 Redis 客户端（配了 KNOWWORK_REDIS_URL 才有），停机时关掉

    def bind_app(self, app: Any) -> None:
        """公开接口的 app 建好后接上：回答里的图在进程内调它的取图接口取回。"""
        self.picture_fetch.bind(app)

    async def aclose(self) -> None:
        if self.redis is not None:
            await self.redis.aclose()


def _wecom(channel: Channel, secrets: dict[str, str], on_message, on_status) -> WecomConnection:
    public = channel.public_config or {}
    return WecomConnection(channel_id=channel.id, workspace_id=channel.workspace_id,
                           bot_id=str(public.get("bot_id", "")), secret=secrets.get("secret", ""),
                           bot_name=str(public.get("bot_name", "")), on_message=on_message, on_status=on_status)


def _feishu(channel: Channel, secrets: dict[str, str], on_message, on_status) -> FeishuConnection:
    return FeishuConnection(channel_id=channel.id, workspace_id=channel.workspace_id,
                            app_id=str((channel.public_config or {}).get("bot_id", "")),
                            secret=secrets.get("secret", ""), on_message=on_message, on_status=on_status)


def _dingtalk(channel: Channel, secrets: dict[str, str], on_message, on_status) -> DingtalkConnection:
    return DingtalkConnection(channel_id=channel.id, workspace_id=channel.workspace_id,
                              client_id=str((channel.public_config or {}).get("bot_id", "")),
                              secret=secrets.get("secret", ""), on_message=on_message, on_status=on_status)


PLATFORMS = {"wecom": _wecom, "feishu": _feishu, "dingtalk": _dingtalk}


def build_channels(master_key: str, sessionmaker: async_sessionmaker, global_sessionmaker: async_sessionmaker,
                   engine: AsyncEngine | None, access: Access, conversations: Conversations, *,
                   accounts: Accounts, catalogs: Catalogs | None = None, sign: Sign | None = None,
                   factories: dict | None = None, identities: ImIdentities | None = None,
                   public_base_url: str = "", answer_modes: AnswerModes | None = None,
                   redis_url: str = "", limiter: Limiter | None = None,
                   targets: ChannelTargets | None = None) -> Channels:
    """sessionmaker 要按空间声明（make_workspace_sessionmaker）；engine 给运行管理拿按机器人的 advisory 锁，
    测试里可以不给。factories 不给时用真实的平台连接（测试里换成假的）。catalogs（引用目录）与 sign（站内链接签名）
    给了才发回答里的图。identities 是扩展登记的身份来源，给了才能认人；public_base_url 拼免登入口与网页会话链接。
    answer_modes 是回答方式登记表（同输入框的那一份），扩展登记的回答方式写了查哪类资源的，渠道里也能选。
    redis_url 给网页嵌入的限次用（多个业务服务进程共用计数）；limiter 直接给时用它（测试）。targets 是扩展登记的
    网页渠道可挂对象（例如专家，targets.py），同一个实例交给扩展。"""
    targets = targets or ChannelTargets()
    store = ChannelStore(sessionmaker, global_sessionmaker)
    secrets = ChannelSecrets(master_key)
    redis = None
    if limiter is None:
        limiter, redis = build_limiter(redis_url)
    # 网页嵌入：认证访问要解开认证密钥、认人（对照表在全局的身份表里）、核对账号还能不能用
    people = EmbedPeople(global_sessionmaker, accounts, access, identities)
    web = WebChannels(store, EmbedTokens.from_master_key(master_key), limiter, answer_modes, secrets=secrets,
                      people=people, users=accounts, targets=targets)
    fetch = InProcessFetch()
    pictures = Pictures(catalogs, sign, fetch) if catalogs is not None and sign is not None else None
    people = People(identities, global_sessionmaker, public_base_url) if identities is not None else None
    service = ChannelService(store, conversations, pictures=pictures, people=people, modes=answer_modes,
                             member=lambda user_id, workspace_id: access.check(user_id, workspace_id,
                                                                               "workspace", "use"))
    supervisor = ChannelSupervisor(store, secrets, engine, service.handle, factories or PLATFORMS)
    admin = ChannelAdmin(store, secrets, supervisor, access, accounts, identities, answer_modes, targets)
    return Channels(admin=admin, service=service, supervisor=supervisor, picture_fetch=fetch, web=web, redis=redis)
