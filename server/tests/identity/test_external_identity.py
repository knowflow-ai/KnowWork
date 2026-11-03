"""外部身份登录：按「身份域 + 外部 ID」认人，没绑过时按可信邮箱、邀请对上，或按登录方式的设置建号。

OIDC、OAuth2、域账号、企业微信等登录方式最后都走这一处，各方式只负责把对方的回调换成 ExternalProfile。
"""

import pytest
from sqlalchemy import select

from knowwork_server.identity.errors import SsoFailure
from knowwork_server.identity.external import ExternalProfile
from knowwork_server.identity.hooks import Placement
from knowwork_server.identity.models import ExternalIdentity, User

pytestmark = pytest.mark.anyio
REALM = "wecom:ww-test-corp"


def _profile(**overrides) -> ExternalProfile:
    values = {"realm": REALM, "subject": "zhangsan", "name": "张三"}
    return ExternalProfile(**{**values, **overrides})


async def _identities(sessionmaker) -> list[tuple[str, str, str]]:
    async with sessionmaker() as session:
        rows = (await session.execute(select(ExternalIdentity))).scalars().all()
    return [(row.realm, row.subject, row.user_id) for row in rows]


async def test_linked_identity_signs_in_the_same_user(identity, sessionmaker, alice):
    first = await identity.external.sign_in(_profile(email="alice@example.com", email_trusted=True),
                                            method="wecom", allow_signup=False)
    assert first.user_id == alice.id
    # 绑定以后对方的邮箱变了、甚至不给邮箱，仍按身份域 + 外部 ID 认出同一个人
    again = await identity.external.sign_in(_profile(email=None), method="wecom", allow_signup=False)
    assert again.user_id == alice.id
    assert await _identities(sessionmaker) == [(REALM, "zhangsan", alice.id)]


async def test_untrusted_email_never_matches_an_existing_account(identity, alice):
    # 对方给的邮箱没经过验证：不能凭它登进别人的账号
    with pytest.raises(SsoFailure):
        await identity.external.sign_in(_profile(email="alice@example.com", email_trusted=False),
                                        method="oauth2", allow_signup=False)


async def test_unknown_person_is_refused_unless_signup_is_allowed(identity):
    with pytest.raises(SsoFailure):
        await identity.external.sign_in(_profile(), method="wecom", allow_signup=False)


async def test_signup_creates_a_person_without_an_email(identity, sessionmaker):
    issued = await identity.external.sign_in(_profile(account="zhangsan"), method="wecom", allow_signup=True)
    async with sessionmaker() as session:
        user = await session.get(User, issued.user_id)
    # 企业微信多半拿不到邮箱：不编假邮箱
    assert user.email is None and user.name == "张三" and user.kind == "person"


async def test_name_falls_back_to_the_account(identity, sessionmaker):
    issued = await identity.external.sign_in(_profile(name="", account="zhangsan"), method="ldap", allow_signup=True)
    async with sessionmaker() as session:
        assert (await session.get(User, issued.user_id)).name == "zhangsan"


async def test_invitation_by_trusted_email_creates_the_user_where_invited(sessionmaker, clock, config):
    from knowwork_server.identity import build_identity

    placed: list[tuple[str, Placement | None]] = []

    async def on_created(user_id: str, placement: Placement | None) -> None:
        placed.append((user_id, placement))

    identity = build_identity(sessionmaker, config, clock=clock, on_user_created=on_created)
    inviter = await identity.users.create_user(email="boss@example.com", name="Boss", password="correct horse battery")
    await identity.invitations.create(email="new@example.com", invited_by=inviter.id,
                                      placement=Placement("ws1", "editor"), caller_is_admin=True)
    issued = await identity.external.sign_in(_profile(email="New@Example.com", email_trusted=True),
                                             method="oidc", allow_signup=False)
    assert placed == [(issued.user_id, Placement("ws1", "editor"))]


async def test_required_email_is_enforced(identity):
    # OIDC 照旧：没绑过的人必须给出已验证的邮箱
    with pytest.raises(SsoFailure):
        await identity.external.sign_in(_profile(), method="oidc", allow_signup=True, require_email=True)


async def test_disabled_user_cannot_sign_in(identity, alice):
    await identity.external.sign_in(_profile(email="alice@example.com", email_trusted=True),
                                    method="wecom", allow_signup=False)
    await identity.users.set_disabled(alice.id, True)
    with pytest.raises(SsoFailure):
        await identity.external.sign_in(_profile(), method="wecom", allow_signup=False)


async def test_channel_accounts_never_sign_in(identity, sessionmaker):
    channel = await identity.users.ensure_channel_account("bot_test", "机器人")
    async with sessionmaker() as session, session.begin():
        session.add(ExternalIdentity(realm=REALM, subject="bot", user_id=channel.id))
    with pytest.raises(SsoFailure):
        await identity.external.sign_in(_profile(subject="bot"), method="wecom", allow_signup=False)


async def test_session_records_the_method_and_login_time(identity, sessionmaker, clock):
    issued = await identity.external.sign_in(_profile(), method="wecom", allow_signup=True)
    principal = await identity.sessions.resolve(issued.token)
    assert principal is not None and principal.auth_method == "wecom"
    async with sessionmaker() as session:
        row = await session.get(ExternalIdentity, (REALM, "zhangsan"))
    assert row.last_login_at == clock.now and row.created_at == clock.now
