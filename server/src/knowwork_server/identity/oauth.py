"""原生应用登录的授权服务：OAuth 2.0 授权码 + PKCE（RFC 6749、7636、8252），刷新令牌轮换，吊销（RFC 7009）。

桌面端这类公开客户端没有密钥，靠 PKCE 证明换令牌的就是发起授权的那个应用：只认 S256，不认 plain。
流程：应用打开系统浏览器 → 用户在我们的页面上确认（要已登录）→ 带一次性授权码回到应用的本机回环地址 →
应用用码和校验串换访问令牌与刷新令牌。令牌是随机串，库里只存摘要（同 API Key、浏览器会话）。

只做协议规定的这几步；PKCE 的计算用 Authlib 的实现。Authlib 的授权服务框架只有同步的 Flask/Django 接法，
和异步数据库对不上，流程这里按 RFC 写，出错码照 RFC 6749 第 5.2 节。
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from urllib.parse import urlencode

from authlib.oauth2.rfc7636 import create_s256_code_challenge
from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..auth.principal import OAUTH, Principal
from .credentials import Clock, digest, new_id, new_token, same, utcnow
from .models import USER_ACTIVE, OAuthCode, OAuthGrant, User
from .oauth_clients import OAuthClient, OAuthClients

ACCESS_PREFIX = "kwat_"
REFRESH_PREFIX = "kwrt_"
CODE_PREFIX = "kwac_"
MAX_TOKEN_LENGTH = 128
MAX_STATE_LENGTH = 500
MAX_DEVICE_NAME = 100
# RFC 7636 4.1：校验串 43～128 个非保留字符；S256 的挑战是 32 字节的 base64url（不带填充），正好 43 个字符
_VERIFIER = re.compile(r"^[A-Za-z0-9._~-]{43,128}$")
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
# 设备号：应用自己生成、存在设备上、长期不变（重新登录也不变）。只用来区分同一个人的几台设备，不是凭据
_DEVICE_ID = re.compile(r"^[A-Za-z0-9_-]{16,64}$")


class OAuthError(Exception):
    """协议出错：error 是 RFC 里的错误码。redirect_to 有值时把错误带回应用（回调地址已确认可信）；
    客户端或回调地址本身不对时没有值，只在页面上报错，不跳转（RFC 6749 4.1.2.1）。"""

    def __init__(self, error: str, description: str, *, redirect_to: str | None = None) -> None:
        super().__init__(description)
        self.error = error
        self.description = description
        self.redirect_to = redirect_to


@dataclass(frozen=True)
class AuthorizationRequest:
    client: OAuthClient
    redirect_uri: str
    code_challenge: str
    state: str
    device_name: str
    device_id: str = ""


@dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_in: int


@dataclass(frozen=True)
class DeviceLogin:
    id: str
    client_id: str
    client_name: str
    device_name: str
    created_at: datetime
    last_used_at: datetime
    device_id: str = ""


def _with_query(uri: str, **params: str) -> str:
    """回调地址已确认不带查询串，直接接上。"""
    return f"{uri}?{urlencode({k: v for k, v in params.items() if v})}"


class OAuthService:
    def __init__(self, sessionmaker: async_sessionmaker, clients: OAuthClients, *, code_ttl: timedelta,
                 access_ttl: timedelta, refresh_ttl: timedelta, touch_interval: timedelta,
                 clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._clients = clients
        self._code_ttl = code_ttl
        self._access_ttl = access_ttl
        self._refresh_ttl = refresh_ttl
        self._touch_interval = touch_interval
        self._clock = clock

    def check(self, params: dict[str, str]) -> AuthorizationRequest:
        """核对授权请求。先核客户端与回调地址（不对就不跳回），其余的错带回应用。"""
        client = self._clients.get(params.get("client_id", ""))
        if client is None:
            raise OAuthError("invalid_client", "未登记的客户端")
        redirect_uri = params.get("redirect_uri", "")
        if not client.accepts_redirect(redirect_uri):
            raise OAuthError("invalid_request", "回调地址不在允许范围内")
        state = params.get("state", "")[:MAX_STATE_LENGTH]

        def back(error: str, description: str) -> OAuthError:
            return OAuthError(error, description, redirect_to=_with_query(redirect_uri, error=error, state=state))

        if params.get("response_type") != "code":
            raise back("unsupported_response_type", "只支持授权码方式")
        if params.get("code_challenge_method") != "S256":
            raise back("invalid_request", "PKCE 只支持 S256")
        challenge = params.get("code_challenge", "")
        if not _CHALLENGE.match(challenge):
            raise back("invalid_request", "PKCE 挑战格式不对")
        device_name = " ".join(params.get("device_name", "").split())[:MAX_DEVICE_NAME]
        device_id = params.get("device_id", "")
        if device_id and not _DEVICE_ID.match(device_id):
            raise back("invalid_request", "设备号格式不对")
        return AuthorizationRequest(client=client, redirect_uri=redirect_uri, code_challenge=challenge,
                                    state=state, device_name=device_name, device_id=device_id)

    @staticmethod
    def deny(request: AuthorizationRequest) -> str:
        return _with_query(request.redirect_uri, error="access_denied", state=request.state)

    async def approve(self, user_id: str, request: AuthorizationRequest) -> str:
        """用户点了允许：签发一次性授权码，返回带着码回到应用的地址。"""
        code = CODE_PREFIX + new_token()
        async with self._sessionmaker() as session, session.begin():
            session.add(OAuthCode(code_hash=digest(code), client_id=request.client.client_id, user_id=user_id,
                                  redirect_uri=request.redirect_uri, code_challenge=request.code_challenge,
                                  device_name=request.device_name, device_id=request.device_id,
                                  expires_at=self._clock() + self._code_ttl))
        return _with_query(request.redirect_uri, code=code, state=request.state)

    def _client(self, client_id: str) -> OAuthClient:
        client = self._clients.get(client_id)
        if client is None:
            raise OAuthError("invalid_client", "未登记的客户端")
        return client

    async def exchange_code(self, client_id: str, code: str, redirect_uri: str, verifier: str) -> TokenSet:
        self._client(client_id)
        if not _VERIFIER.match(verifier):
            raise OAuthError("invalid_request", "PKCE 校验串格式不对")
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = (await session.execute(select(OAuthCode).where(OAuthCode.code_hash == digest(code[:MAX_TOKEN_LENGTH]))
                                         .with_for_update())).scalar_one_or_none()
            if row is None or row.client_id != client_id:
                raise OAuthError("invalid_grant", "授权码无效")
            if row.grant_id is not None:
                # 码被用过第二次：作废它换出的那次登录，提交后再报错
                await self._revoke_grants(session, OAuthGrant.id == row.grant_id)
                reused = True
            else:
                reused = False
                valid = (row.expires_at > now and row.redirect_uri == redirect_uri
                         and same(create_s256_code_challenge(verifier), row.code_challenge)
                         and await self._user_active(session, row.user_id))
                if not valid:
                    raise OAuthError("invalid_grant", "授权码无效")
                tokens, grant = self._new_grant(row, now)
                session.add(grant)
                await session.flush()
                row.grant_id = grant.id
        if reused:
            raise OAuthError("invalid_grant", "授权码无效")
        return tokens

    async def refresh(self, client_id: str, refresh_token: str) -> TokenSet:
        self._client(client_id)
        hashed = digest(refresh_token[:MAX_TOKEN_LENGTH])
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            grant = (await session.execute(select(OAuthGrant).where(OAuthGrant.refresh_hash == hashed)
                                           .with_for_update())).scalar_one_or_none()
            if grant is None:
                # 换过的旧刷新令牌又来了：令牌被人拿去用过，整次登录作废
                replayed = await self._revoke_grants(session, OAuthGrant.previous_refresh_hash == hashed,
                                                     OAuthGrant.client_id == client_id)
            else:
                replayed = False
                valid = (grant.client_id == client_id and grant.revoked_at is None
                         and grant.refresh_expires_at > now and await self._user_active(session, grant.user_id))
                if not valid:
                    raise OAuthError("invalid_grant", "刷新令牌无效")
                tokens = self._rotate(grant, now)
        if grant is None or replayed:
            raise OAuthError("invalid_grant", "刷新令牌无效")
        return tokens

    async def revoke_token(self, client_id: str, token: str) -> None:
        """RFC 7009：不管令牌认不认得都当成功，不给人试探。访问令牌、刷新令牌都可以，作废的是整次登录。"""
        hashed = digest(token[:MAX_TOKEN_LENGTH])
        async with self._sessionmaker() as session, session.begin():
            await self._revoke_grants(session, OAuthGrant.client_id == client_id,
                                      or_(OAuthGrant.access_hash == hashed, OAuthGrant.refresh_hash == hashed))

    async def resolve(self, access_token: str) -> Principal | None:
        if not access_token.startswith(ACCESS_PREFIX) or len(access_token) > MAX_TOKEN_LENGTH:
            return None
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            row = (await session.execute(
                select(OAuthGrant, User.status).join(User, User.id == OAuthGrant.user_id)
                .where(OAuthGrant.access_hash == digest(access_token))
            )).one_or_none()
            if row is None:
                return None
            grant, status = row
            if grant.revoked_at is not None or grant.access_expires_at <= now or status != USER_ACTIVE:
                return None
            if now - grant.last_used_at >= self._touch_interval:
                grant.last_used_at = now
            return Principal(user_id=grant.user_id, auth_method=OAUTH, grant_id=grant.id)

    async def subject(self, client_id: str, access_token: str) -> Principal:
        """令牌交换里拿来换的访问令牌：必须是这个客户端的、还有效、人还在；不然 invalid_grant。"""
        principal = await self.resolve(access_token)
        if principal is None or principal.grant_id is None:
            raise OAuthError("invalid_grant", "访问令牌无效")
        async with self._sessionmaker() as session:
            owner = (await session.execute(select(OAuthGrant.client_id)
                                           .where(OAuthGrant.id == principal.grant_id))).scalar_one_or_none()
        if owner != client_id:
            raise OAuthError("invalid_grant", "访问令牌无效")
        return principal

    async def list_devices(self, user_id: str) -> list[DeviceLogin]:
        now = self._clock()
        async with self._sessionmaker() as session:
            rows = (await session.execute(
                select(OAuthGrant).where(OAuthGrant.user_id == user_id, OAuthGrant.revoked_at.is_(None),
                                         OAuthGrant.refresh_expires_at > now)
                .order_by(OAuthGrant.last_used_at.desc())
            )).scalars().all()
        return [DeviceLogin(id=row.id, client_id=row.client_id, client_name=self._client_name(row.client_id),
                            device_name=row.device_name, created_at=row.created_at, last_used_at=row.last_used_at,
                            device_id=row.device_id)
                for row in rows]

    async def device_id(self, grant_id: str) -> str | None:
        """这次登录报的设备号；没报、登录已退出或过期、不存在都是 None。扩展据此认「是不是那台设备」。"""
        async with self._sessionmaker() as session:
            found = (await session.execute(select(OAuthGrant.device_id).where(
                OAuthGrant.id == grant_id, OAuthGrant.revoked_at.is_(None),
                OAuthGrant.refresh_expires_at > self._clock()))).scalar_one_or_none()
        return found or None

    async def revoke_device(self, user_id: str, grant_id: str) -> bool:
        async with self._sessionmaker() as session, session.begin():
            return await self._revoke_grants(session, OAuthGrant.id == grant_id, OAuthGrant.user_id == user_id)

    async def revoke_all(self, user_id: str) -> None:
        async with self._sessionmaker() as session, session.begin():
            await self._revoke_grants(session, OAuthGrant.user_id == user_id)

    def _client_name(self, client_id: str) -> str:
        client = self._clients.get(client_id)
        return client.name if client is not None else client_id

    async def _revoke_grants(self, session: AsyncSession, *conditions) -> bool:
        result = await session.execute(update(OAuthGrant).where(*conditions, OAuthGrant.revoked_at.is_(None))
                                       .values(revoked_at=self._clock()))
        return result.rowcount > 0

    @staticmethod
    async def _user_active(session: AsyncSession, user_id: str) -> bool:
        status = (await session.execute(select(User.status).where(User.id == user_id))).scalar_one_or_none()
        return status == USER_ACTIVE

    def _issue(self) -> tuple[str, str]:
        return ACCESS_PREFIX + new_token(), REFRESH_PREFIX + new_token()

    def _new_grant(self, code: OAuthCode, now: datetime) -> tuple[TokenSet, OAuthGrant]:
        access, refresh = self._issue()
        grant = OAuthGrant(id=new_id(), user_id=code.user_id, client_id=code.client_id, device_name=code.device_name,
                           device_id=code.device_id, access_hash=digest(access), access_expires_at=now + self._access_ttl,
                           refresh_hash=digest(refresh), refresh_expires_at=now + self._refresh_ttl,
                           created_at=now, last_used_at=now)
        return self._tokens(access, refresh), grant

    def _rotate(self, grant: OAuthGrant, now: datetime) -> TokenSet:
        access, refresh = self._issue()
        grant.previous_refresh_hash = grant.refresh_hash
        grant.access_hash = digest(access)
        grant.access_expires_at = now + self._access_ttl
        grant.refresh_hash = digest(refresh)
        grant.refresh_expires_at = now + self._refresh_ttl
        grant.last_used_at = now
        return self._tokens(access, refresh)

    def _tokens(self, access: str, refresh: str) -> TokenSet:
        return TokenSet(access_token=access, refresh_token=refresh,
                        expires_in=int(self._access_ttl.total_seconds()))
