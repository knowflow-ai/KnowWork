"""渠道：企业微信、飞书、钉钉机器人与网页分享。现在有企业微信、飞书、钉钉，都用渠道账号回答（不认人）。

主线整合：
    channels = build_channels(master_key, scoped, global_sessionmaker, engine, access, service,
                              accounts=identity.users)
    启动：await channels.supervisor.start()；停机：await channels.supervisor.stop()
    app.include_router(build_router(channels.admin, scoped_principal(identity, WORKSPACE_SCOPE)))
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from .admin import Access, Accounts, ChannelAdmin
from .api import build_router
from .dingtalk import DingtalkConnection
from .feishu import FeishuConnection
from .models import Channel
from .pictures import Catalogs, InProcessFetch, Pictures, Sign
from .service import ChannelService, Conversations
from .store import ChannelStore
from .supervisor import ChannelSecrets, ChannelSupervisor
from .wecom import WecomConnection

__all__ = ["Channels", "build_channels", "build_router"]


@dataclass(frozen=True)
class Channels:
    admin: ChannelAdmin
    service: ChannelService
    supervisor: ChannelSupervisor
    picture_fetch: InProcessFetch

    def bind_app(self, app: Any) -> None:
        """公开接口的 app 建好后接上：回答里的图在进程内调它的取图接口取回。"""
        self.picture_fetch.bind(app)


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
                   factories: dict | None = None) -> Channels:
    """sessionmaker 要按空间声明（make_workspace_sessionmaker）；engine 给运行管理拿按机器人的 advisory 锁，
    测试里可以不给。factories 不给时用真实的平台连接（测试里换成假的）。catalogs（引用目录）与 sign（站内链接签名）
    给了才发回答里的图。"""
    store = ChannelStore(sessionmaker, global_sessionmaker)
    secrets = ChannelSecrets(master_key)
    fetch = InProcessFetch()
    pictures = Pictures(catalogs, sign, fetch) if catalogs is not None and sign is not None else None
    service = ChannelService(store, conversations, pictures=pictures,
                             member=lambda user_id, workspace_id: access.check(user_id, workspace_id,
                                                                               "workspace", "use"))
    supervisor = ChannelSupervisor(store, secrets, engine, service.handle, factories or PLATFORMS)
    admin = ChannelAdmin(store, secrets, supervisor, access, accounts)
    return Channels(admin=admin, service=service, supervisor=supervisor, picture_fetch=fetch)
