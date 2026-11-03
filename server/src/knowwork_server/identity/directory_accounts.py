"""目录同步的账号部分：按某个来源的人员快照建号、对上已有账号、更新同步来的字段、停用离职的、恢复重新在职的。

- 认人同登录（external.py）：身份域 + 外部 ID 已绑的直接认（登录时建立、还不属于哪个来源的身份，被这个来源接管）；
  没绑的按可信邮箱对上已有账号；都没有就建（可以没有邮箱）。不可信的邮箱从不参与认人；
- 同步建的人不经「没邀请的进默认空间」的钩子：进哪个空间只看成员规则；
- 同步来的字段（姓名、可信邮箱）以目录为准；邮箱已被别的账号占用时不改；
- 离职：快照里没有、或标了不在职，就把这条身份记成不在职。任一来源不在职就停用账号（disabled_by=directory，会话作废），
  所有来源都在职、且是同步停用的才自动恢复；管理员手工停用的不动；
- 停不停由调用方的保护策略决定（allow_deactivate(这次要停几个, 这个来源现在在职几个)）：不放行时谁都不停，名单在 held 里；
- 整份在一个事务里，成批读写（一万人也是几次查询），失败整份回滚。
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .credentials import Clock, new_id, utcnow
from .errors import InvalidInput
from .models import USER_ACTIVE, USER_DISABLED, USER_PERSON, AuthSession, ExternalIdentity, User
from .users import normalize_email

BY_DIRECTORY = "directory"
BATCH = 1000

DeactivationGuard = Callable[[int, int], bool]


@dataclass(frozen=True)
class DirectoryPerson:
    subject: str                     # 来源里的稳定 ID（objectGUID、entryUUID、userid……）
    name: str = ""
    email: str | None = None
    email_trusted: bool = False      # 管理员确认邮箱由企业统一分配
    account: str | None = None       # 账号名（sAMAccountName、uid、userid）
    active: bool = True
    aliases: Mapping[str, str] = field(default_factory=dict)
    attributes: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class PeopleResult:
    users: Mapping[str, str]          # 快照里每个人（含不在职的）→ 本地账号
    created: int = 0
    linked: int = 0
    updated: int = 0
    deactivated: tuple[str, ...] = ()
    restored: int = 0
    held: tuple[str, ...] = ()        # 该停、但保护没放行的账号
    conflicts: tuple[str, ...] = ()   # 没对上、跳过的人（来源里的 ID）


def _email(person: DirectoryPerson) -> str | None:
    if not person.email_trusted or not person.email:
        return None
    try:
        return normalize_email(person.email)
    except InvalidInput:
        return None


def _chunks(values: Sequence[str]):
    for start in range(0, len(values), BATCH):
        yield values[start:start + BATCH]


class DirectoryAccounts:
    def __init__(self, sessionmaker: async_sessionmaker, clock: Clock = utcnow) -> None:
        self._sessionmaker = sessionmaker
        self._clock = clock

    async def apply(self, realm: str, source_id: str, people: Sequence[DirectoryPerson], *,
                    allow_deactivate: DeactivationGuard) -> PeopleResult:
        by_subject = {person.subject: person for person in people if person.subject}
        now = self._clock()
        async with self._sessionmaker() as session, session.begin():
            identities = await self._identities(session, realm, source_id, list(by_subject))
            users = await self._users(session, [row.user_id for row in identities.values()])
            counts = {"created": 0, "linked": 0, "updated": 0}
            conflicts: list[str] = []
            await self._match(session, realm, source_id, by_subject, identities, users, counts, conflicts, now)
            mapping = {subject: identities[subject].user_id for subject in by_subject if subject in identities}
            turned_off: list[ExternalIdentity] = []
            for subject, row in identities.items():
                if row.source_id == source_id or subject in by_subject:
                    active = subject in by_subject and by_subject[subject].active
                    if row.active and not active:
                        turned_off.append(row)
                    row.active = active
            await session.flush()
            deactivated, held, restored = await self._settle(session, source_id, users, allow_deactivate, now)
            if held:
                # 保护没放行：这次判离职的身份照旧记成在职。否则别的来源同步时会看到「有个来源不在职」，
                # 绕过这里的保护把人停掉
                stay = set(held)
                for row in turned_off:
                    if row.user_id in stay:
                        row.active = True
        return PeopleResult(users=mapping, deactivated=deactivated, held=held, restored=restored,
                            conflicts=tuple(conflicts), **counts)

    async def detach(self, source_id: str) -> int:
        """删掉一个来源时：它同步来的身份解绑（不再属于哪个来源、记成在职），账号原样保留，以后用别的方式登录
        或接了别的来源再对上。不停用任何人：删来源不等于这些人都离职了。返回解绑了几条。"""
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(update(ExternalIdentity).where(ExternalIdentity.source_id == source_id)
                                           .values(source_id=None, active=True))
        return result.rowcount or 0

    async def _identities(self, session, realm: str, source_id: str, subjects: list[str]) -> dict[str, ExternalIdentity]:
        """这个来源已有的身份，加上快照里的人在这个身份域里已有的（登录时建立的）。"""
        rows = {row.subject: row for row in (await session.scalars(
            select(ExternalIdentity).where(ExternalIdentity.realm == realm, ExternalIdentity.source_id == source_id)
            .with_for_update())).all()}
        missing = [subject for subject in subjects if subject not in rows]
        for batch in _chunks(missing):
            for row in (await session.scalars(select(ExternalIdentity).where(
                    ExternalIdentity.realm == realm, ExternalIdentity.subject.in_(batch)).with_for_update())).all():
                rows[row.subject] = row
        return rows

    async def _users(self, session, user_ids: list[str]) -> dict[str, User]:
        found: dict[str, User] = {}
        for batch in _chunks(sorted(set(user_ids))):
            for user in (await session.scalars(select(User).where(User.id.in_(batch)).with_for_update())).all():
                found[user.id] = user
        return found

    async def _match(self, session, realm, source_id, by_subject, identities, users, counts, conflicts, now) -> None:
        unmatched = [person for subject, person in by_subject.items() if subject not in identities]
        # 快照里出现的可信邮箱现在归谁：没绑的按它对人，已绑的改邮箱时不能改成别人的
        emails = sorted({email for person in by_subject.values() if (email := _email(person))})
        by_email: dict[str, User] = {}
        for batch in _chunks(emails):
            for user in (await session.scalars(select(User).where(User.email.in_(batch)).with_for_update())).all():
                by_email[user.email] = user
        taken_in_realm = {row.user_id for row in identities.values()}
        pairs: list[tuple[DirectoryPerson, User]] = []
        for person in unmatched:
            email = _email(person)
            user = by_email.get(email) if email else None
            if user is not None and (user.kind != USER_PERSON or user.id in taken_in_realm):
                conflicts.append(person.subject)  # 这个邮箱的账号在这个来源里已经对着别人，不能一号两人
                continue
            if user is None:
                user = User(id=new_id(), email=email, name=(person.name or person.account or "")[:200],
                            status=USER_ACTIVE, password_hash=None)
                session.add(user)
                if email:
                    by_email[email] = user
                counts["created"] += 1
            else:
                counts["linked"] += 1
            users[user.id] = user
            taken_in_realm.add(user.id)
            pairs.append((person, user))
        # 先落新账号、再落身份：两张表之间只有外键没有 relationship，同一次 flush 里不保证先后
        await session.flush()
        for person, user in pairs:
            row = ExternalIdentity(realm=realm, subject=person.subject, user_id=user.id, created_at=now)
            session.add(row)
            identities[person.subject] = row
        await session.flush()
        fresh = {person.subject for person in unmatched}
        for subject, person in by_subject.items():
            row = identities.get(subject)
            if row is None:
                continue
            changed = self._update(row, users.get(row.user_id), person, by_email, source_id, now)
            if changed and subject not in fresh:
                counts["updated"] += 1

    def _update(self, row: ExternalIdentity, user: User | None, person: DirectoryPerson, by_email: dict[str, User],
                source_id: str, now: datetime) -> bool:
        """同步来的字段以目录为准；只算人看得见的变化（姓名、邮箱）。"""
        row.source_id, row.synced_at = source_id, now
        row.account, row.aliases, row.attributes = person.account, dict(person.aliases), dict(person.attributes)
        if user is None:
            return False
        changed = False
        name = (person.name or person.account or "")[:200]
        if name and user.name != name:
            user.name, changed = name, True
        email = _email(person)
        if email and user.email != email and by_email.get(email) in (None, user):
            user.email, changed = email, True
            by_email[email] = user
        return changed

    async def _settle(self, session, source_id, users, allow_deactivate, now):
        """按各来源的在职情况停用或恢复账号。"""
        linked = sorted({row.user_id for row in (await session.scalars(
            select(ExternalIdentity).where(ExternalIdentity.source_id == source_id))).all()})
        everywhere: dict[str, list[bool]] = {}
        for batch in _chunks(linked):
            for row in (await session.scalars(select(ExternalIdentity).where(
                    ExternalIdentity.user_id.in_(batch), ExternalIdentity.source_id.is_not(None)))).all():
                everywhere.setdefault(row.user_id, []).append(row.active)
        missing = [user_id for user_id in linked if user_id not in users]
        users.update(await self._users(session, missing))
        leaving = sorted(user_id for user_id, states in everywhere.items()
                         if not all(states) and users.get(user_id) is not None
                         and users[user_id].status == USER_ACTIVE and users[user_id].kind == USER_PERSON)
        active_now = sum(1 for user_id in linked if users.get(user_id) is not None
                         and users[user_id].status == USER_ACTIVE)
        deactivated: tuple[str, ...] = ()
        held: tuple[str, ...] = ()
        if leaving and allow_deactivate(len(leaving), active_now):
            for user_id in leaving:
                users[user_id].status, users[user_id].disabled_by = USER_DISABLED, BY_DIRECTORY
            for batch in _chunks(leaving):
                await session.execute(update(AuthSession).where(AuthSession.user_id.in_(batch),
                                                                AuthSession.revoked_at.is_(None))
                                      .values(revoked_at=now))
            deactivated = tuple(leaving)
        elif leaving:
            held = tuple(leaving)
        restored = 0
        for user_id, states in everywhere.items():
            user = users.get(user_id)
            if user is not None and all(states) and user.status == USER_DISABLED and user.disabled_by == BY_DIRECTORY:
                user.status, user.disabled_by = USER_ACTIVE, None
                restored += 1
        return deactivated, held, restored
