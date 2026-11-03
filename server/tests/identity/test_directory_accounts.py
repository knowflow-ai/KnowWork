"""目录同步的账号部分：按目录的人员快照建号、对上已有账号、更新同步来的字段、停用离职的、恢复重新在职的。

认人同登录（external.py）：身份域 + 外部 ID 已绑的直接认；没绑的按可信邮箱对上已有账号；都没有就建（可以没有邮箱）。
同步建的人不经「没邀请的进默认空间」：进哪个空间只看成员规则。
"""

import pytest
from sqlalchemy import select

from knowwork_server.identity.directory_accounts import DirectoryPerson as Person
from knowwork_server.identity.models import ExternalIdentity, User

pytestmark = pytest.mark.anyio
REALM, SOURCE = "ldap:src_ad", "src_ad"
ALL = lambda deactivations, active: True
NONE = lambda deactivations, active: False


def _people(*extra: Person) -> tuple[Person, ...]:
    return (Person("u-zhang", "张三", email="zhangsan@corp.cn", email_trusted=True, account="zhangsan"),
            Person("u-li", "李四", account="lisi"), *extra)


async def _user(sessionmaker, user_id: str) -> User:
    async with sessionmaker() as session:
        return await session.get(User, user_id)


async def test_new_people_are_created_without_placement(sessionmaker, config, clock):
    from knowwork_server.identity import build_identity

    placed: list[str] = []

    async def on_created(user_id, placement):
        placed.append(user_id)

    identity = build_identity(sessionmaker, config, clock=clock, on_user_created=on_created)
    result = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    assert result.created == 2 and set(result.users) == {"u-zhang", "u-li"}
    zhang = await _user(sessionmaker, result.users["u-zhang"])
    li = await _user(sessionmaker, result.users["u-li"])
    assert zhang.email == "zhangsan@corp.cn" and zhang.name == "张三"
    assert li.email is None and li.name == "李四"
    assert placed == []  # 同步来的人进哪个空间只看成员规则


async def test_a_trusted_email_links_the_existing_account(identity, sessionmaker, alice):
    person = Person("u-alice", "Alice（AD）", email="alice@example.com", email_trusted=True)
    result = await identity.accounts.apply(REALM, SOURCE, (person,), allow_deactivate=ALL)
    assert result.users["u-alice"] == alice.id and result.linked == 1 and result.created == 0
    # 同步来的字段以目录为准
    assert (await _user(sessionmaker, alice.id)).name == "Alice（AD）"


async def test_an_untrusted_email_never_links(identity, alice):
    person = Person("u-x", "冒名", email="alice@example.com", email_trusted=False)
    result = await identity.accounts.apply(REALM, SOURCE, (person,), allow_deactivate=ALL)
    assert result.users["u-x"] != alice.id and result.created == 1


async def test_a_login_identity_is_adopted_by_the_sync(identity, sessionmaker):
    """先用企业微信扫码登录过（建了身份、不属于哪个来源），后接通讯录同步：同一个人，不再建一个。"""
    from knowwork_server.identity.external import ExternalProfile

    issued = await identity.external.sign_in(ExternalProfile(realm=REALM, subject="u-li", name="李四"),
                                             method="ldap", allow_signup=True)
    result = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    assert result.users["u-li"] == issued.user_id
    async with sessionmaker() as session:
        row = await session.get(ExternalIdentity, (REALM, "u-li"))
    assert row.source_id == SOURCE and row.account == "lisi"


async def test_updates_follow_the_directory(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    renamed = (Person("u-zhang", "张三丰", email="zs@corp.cn", email_trusted=True), Person("u-li", "李四"))
    again = await identity.accounts.apply(REALM, SOURCE, renamed, allow_deactivate=ALL)
    assert again.updated == 1 and again.created == 0
    zhang = await _user(sessionmaker, first.users["u-zhang"])
    assert zhang.name == "张三丰" and zhang.email == "zs@corp.cn"


async def test_leavers_are_disabled_and_come_back_when_active_again(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    li = first.users["u-li"]
    session = await identity.sessions.create(li)
    # 李四从目录里消失：停用、会话作废
    gone = await identity.accounts.apply(REALM, SOURCE, _people()[:1], allow_deactivate=ALL)
    assert gone.deactivated == (li,)
    user = await _user(sessionmaker, li)
    assert user.status == "disabled" and user.disabled_by == "directory"
    assert await identity.sessions.resolve(session.token) is None
    # 回来了：自动恢复
    back = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    assert back.restored == 1 and (await _user(sessionmaker, li)).status == "active"


async def test_disabled_in_the_directory_counts_as_left(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    people = (_people()[0], Person("u-li", "李四", active=False))
    result = await identity.accounts.apply(REALM, SOURCE, people, allow_deactivate=ALL)
    assert result.deactivated == (first.users["u-li"],)
    assert result.users["u-li"] == first.users["u-li"]  # 停用的人也给出对应，调用方据此不放进部门


async def test_a_held_deactivation_changes_nobody(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    asked: list[tuple[int, int]] = []

    def guard(deactivations: int, active: int) -> bool:
        asked.append((deactivations, active))
        return False

    held = await identity.accounts.apply(REALM, SOURCE, (), allow_deactivate=guard)
    assert asked == [(2, 2)] and held.deactivated == () and set(held.held) == set(first.users.values())
    for user_id in first.users.values():
        assert (await _user(sessionmaker, user_id)).status == "active"
    # 判离职的身份照旧记成在职：别的来源同步时不会借它绕过这里的保护
    async with sessionmaker() as session:
        assert all(row.active for row in (await session.scalars(select(ExternalIdentity))).all())


async def test_manual_disable_is_not_undone(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    await identity.users.set_disabled(first.users["u-li"], True)  # 管理员手工停的
    result = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    assert result.restored == 0 and (await _user(sessionmaker, first.users["u-li"])).status == "disabled"


async def test_another_source_still_marking_someone_left_keeps_them_disabled(identity, sessionmaker):
    """任一来源判离职就停用；所有来源都在职才恢复。"""
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    zhang = first.users["u-zhang"]
    wecom = Person("zhangsan", "张三", email="zhangsan@corp.cn", email_trusted=True)
    assert (await identity.accounts.apply("wecom:corp", "src_wx", (wecom,), allow_deactivate=ALL)).users["zhangsan"] \
        == zhang
    await identity.accounts.apply("wecom:corp", "src_wx", (), allow_deactivate=ALL)  # 企业微信里离职了
    assert (await _user(sessionmaker, zhang)).status == "disabled"
    await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)  # AD 里还在
    assert (await _user(sessionmaker, zhang)).status == "disabled"
    await identity.accounts.apply("wecom:corp", "src_wx", (wecom,), allow_deactivate=ALL)
    assert (await _user(sessionmaker, zhang)).status == "active"


async def test_an_email_already_taken_is_not_copied(identity, sessionmaker, alice):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    clash = (Person("u-zhang", "张三", email="alice@example.com", email_trusted=True), _people()[1])
    await identity.accounts.apply(REALM, SOURCE, clash, allow_deactivate=ALL)
    assert (await _user(sessionmaker, first.users["u-zhang"])).email == "zhangsan@corp.cn"


async def test_identities_record_the_source_and_sync_time(identity, sessionmaker, clock):
    await identity.accounts.apply(REALM, SOURCE, _people(Person("u-wang", "王五", aliases={"open_id": "ou_1"})),
                                  allow_deactivate=ALL)
    async with sessionmaker() as session:
        rows = {row.subject: row for row in (await session.scalars(select(ExternalIdentity))).all()}
    assert rows["u-wang"].aliases == {"open_id": "ou_1"} and rows["u-wang"].synced_at == clock.now
    assert {row.source_id for row in rows.values()} == {SOURCE}


async def test_no_deactivation_guard_means_nothing_is_disabled(identity):
    await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    result = await identity.accounts.apply(REALM, SOURCE, (), allow_deactivate=NONE)
    assert result.deactivated == () and len(result.held) == 2


async def test_deleting_a_source_detaches_its_identities_and_disables_nobody(identity, sessionmaker):
    first = await identity.accounts.apply(REALM, SOURCE, _people(), allow_deactivate=ALL)
    assert await identity.accounts.detach(SOURCE) == 2
    async with sessionmaker() as session:
        rows = (await session.scalars(select(ExternalIdentity))).all()
    assert {(row.source_id, row.active) for row in rows} == {(None, True)}
    for user_id in first.users.values():
        assert (await _user(sessionmaker, user_id)).status == "active"
