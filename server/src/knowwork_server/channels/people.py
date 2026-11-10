"""认人的渠道里，私聊按提问人本人回答（docs 里的 IM 认人方案）。

渠道选一个组织来源（扩展登记的身份来源，identity/im_identities.py），用它的身份域把消息里平台给的用户 ID
对到本地用户；对不上（没同步到、也没用这个来源登录过）时给免登入口，让他在 IM 里点开登录一次就对上了。
群聊不走这里：回答全群看得见，仍用渠道账号。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..identity.im_identities import INACTIVE, UNKNOWN, ImIdentities, ImIdentitySource, Resolved, resolve
from .types import IncomingMessage

MEMBER = "member"  # channel.audience：认人
PUBLIC = "public"  # 不认人，用渠道账号
NOT_MEMBER = "not_member"  # 认出来了，但不是渠道所在空间的成员
PLATFORM_LABELS = {"wecom": "企业微信", "feishu": "飞书", "dingtalk": "钉钉"}


@dataclass(frozen=True)
class People:
    identities: ImIdentities
    sessionmaker: async_sessionmaker  # 不声明工作空间的那种：身份表是全局的
    public_base_url: str = ""

    async def source(self, source_id: str | None) -> ImIdentitySource | None:
        return await self.identities.get(source_id) if source_id else None

    async def resolve(self, source: ImIdentitySource, message: IncomingMessage) -> Resolved:
        ids = dict(message.ids) or {"id": message.external_id}
        return await resolve(self.sessionmaker, source.realm, ids)

    def link(self, path: str) -> str:
        return f"{self.public_base_url.rstrip('/')}{path}"

    def stranger_reply(self, reason: str, source: ImIdentitySource) -> str:
        """对不上时回给他的话：说清为什么、下一步怎么办。"""
        platform = PLATFORM_LABELS.get(source.platform, source.platform)
        if reason == UNKNOWN and source.login_url:
            return (f"还没认出你是谁。点这里用{platform}登录一次，之后就按你自己的权限回答：\n"
                    f"{self.link(source.login_url)}")
        if reason == UNKNOWN:
            return f"还没认出你是谁：你的{platform}账号还没同步进来，请联系管理员。"
        if reason == INACTIVE:
            return "你的账号已停用，请联系管理员。"
        return "你还不是这个空间的成员，请联系管理员把你加进来。"
