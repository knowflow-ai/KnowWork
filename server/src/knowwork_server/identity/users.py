"""用户：建号、按密码认证、禁用；渠道账号（IM 机器人回答时用的身份）也在这里建。"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .credentials import Clock, new_id, utcnow
from .errors import EmailTaken, InvalidInput
from .models import USER_ACTIVE, USER_CHANNEL, USER_DISABLED, USER_PERSON, AuthSession, User
from .passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH, hash_password, needs_rehash, verify_password

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
MAX_LOOKUP_IDS = 500
MAX_SEARCH_LIMIT = 50
MAX_QUERY_LENGTH = 100


@dataclass(frozen=True)
class UserInfo:
    id: str
    email: str
    name: str
    status: str
    kind: str = USER_PERSON

    @property
    def active(self) -> bool:
        return self.status == USER_ACTIVE


def to_info(user: User) -> UserInfo:
    return UserInfo(id=user.id, email=user.email, name=user.name, status=user.status, kind=user.kind)


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if len(normalized) > 320 or not _EMAIL.match(normalized):
        raise InvalidInput("邮箱格式不正确")
    return normalized


def check_password_policy(password: str) -> None:
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise InvalidInput(f"密码长度需在 {MIN_PASSWORD_LENGTH} 到 {MAX_PASSWORD_LENGTH} 之间")


async def insert_user(session: AsyncSession, *, email: str, name: str, password: str | None) -> User:
    """在调用方的事务里建号（邀请注册、OIDC 首次登录共用）。"""
    if password is not None:
        check_password_policy(password)
    user = User(id=new_id(), email=normalize_email(email), name=name.strip()[:200], status=USER_ACTIVE,
                password_hash=hash_password(password) if password is not None else None)
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as error:
        raise EmailTaken("邮箱已被使用") from error
    return user


class UserService:
    def __init__(self, sessionmaker: async_sessionmaker, clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def create_user(self, *, email: str, name: str, password: str | None = None) -> UserInfo:
        async with self._sessionmaker() as session, session.begin():
            return to_info(await insert_user(session, email=email, name=name, password=password))

    async def get(self, user_id: str) -> UserInfo | None:
        async with self._sessionmaker() as session:
            user = await session.get(User, user_id)
            return to_info(user) if user else None

    async def get_many(self, user_ids: Sequence[str]) -> dict[str, UserInfo]:
        """按 id 批量取资料，查不到的 id 不出现在结果里。按 MAX_LOOKUP_IDS 分批查询，避免超长 IN 列表。"""
        wanted = tuple(dict.fromkeys(user_ids))
        found: dict[str, UserInfo] = {}
        async with self._sessionmaker() as session:
            for start in range(0, len(wanted), MAX_LOOKUP_IDS):
                chunk = wanted[start:start + MAX_LOOKUP_IDS]
                rows = (await session.execute(select(User).where(User.id.in_(chunk)))).scalars().all()
                found.update({row.id: to_info(row) for row in rows})
        return found

    async def search(self, query: str, *, limit: int = 20) -> tuple[UserInfo, ...]:
        """按邮箱或姓名子串搜索启用中的账号（不区分大小写），按邮箱排序。通配符按字面匹配。"""
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise ValueError(f"limit 须在 1 到 {MAX_SEARCH_LIMIT} 之间")
        term = query.strip()[:MAX_QUERY_LENGTH]
        if not term:
            return ()
        pattern = "%" + _escape_like(term) + "%"
        statement = (select(User)
                     .where(User.status == USER_ACTIVE, User.kind == USER_PERSON,
                            or_(User.email.ilike(pattern, escape="\\"), User.name.ilike(pattern, escape="\\")))
                     .order_by(User.email).limit(limit))
        async with self._sessionmaker() as session:
            rows = (await session.execute(statement)).scalars().all()
        return tuple(to_info(row) for row in rows)

    async def find_by_email(self, email: str) -> UserInfo | None:
        async with self._sessionmaker() as session:
            user = (await session.execute(select(User).where(User.email == normalize_email(email)))).scalar_one_or_none()
            return to_info(user) if user else None

    async def authenticate(self, email: str, password: str) -> UserInfo | None:
        """邮箱或密码错、账号被禁用一律返回 None；调用方不得区分原因。"""
        try:
            normalized = normalize_email(email)
        except InvalidInput:
            normalized = ""
        async with self._sessionmaker() as session, session.begin():
            user = (await session.execute(select(User).where(User.email == normalized))).scalar_one_or_none()
            stored = user.password_hash if user else None
            if not verify_password(stored, password[:MAX_PASSWORD_LENGTH]) or user is None:
                return None
            if user.status != USER_ACTIVE or user.kind != USER_PERSON:
                return None
            if needs_rehash(stored):
                user.password_hash = hash_password(password)
            return to_info(user)

    async def set_disabled(self, user_id: str, disabled: bool) -> bool:
        """禁用时吊销该用户全部浏览器会话；API Key 在解析时按用户状态拒绝，重新启用后恢复可用。"""
        now: datetime = self._clock()
        async with self._sessionmaker() as session, session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                return False
            user.status = USER_DISABLED if disabled else USER_ACTIVE
            if disabled:
                await session.execute(update(AuthSession)
                                      .where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
                                      .values(revoked_at=now))
            return True

    async def ensure_channel_account(self, user_id: str, name: str) -> UserInfo:
        """建或更新一个渠道账号：没有密码，邮箱用保留域名 .invalid（不会撞上真人，单点登录也不会给出这种邮箱）。
        渠道删掉后账号只禁用不删，运行记录、用量里还能看到是哪个机器人答的；同一个渠道重建时重新启用。"""
        async with self._sessionmaker() as session, session.begin():
            user = await session.get(User, user_id, with_for_update=True)
            if user is None:
                user = User(id=user_id, email=f"{user_id.lower()}@channel.invalid", kind=USER_CHANNEL,
                            password_hash=None)
                session.add(user)
            elif user.kind != USER_CHANNEL:
                raise InvalidInput("这个 id 已经是一个人的账号")
            user.name, user.status = name.strip()[:200], USER_ACTIVE
            await session.flush()
            return to_info(user)
