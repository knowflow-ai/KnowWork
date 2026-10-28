"""邀请注册：只有这个空间的管理员能邀请，链接一次性、会过期、可撤销，库里只存摘要；
注册后进邀请所在的空间、当邀请定的角色。"""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from knowwork_server.identity.container import build_identity
from knowwork_server.identity.errors import NotAllowed
from knowwork_server.identity.hooks import Placement
from knowwork_server.identity.models import Invitation

from .conftest import BASE_URL, PASSWORD, login

pytestmark = pytest.mark.anyio


WS = "/api/workspaces/ws1"
MEMBER = Placement(workspace_id="ws1", role="editor")


async def _invite(client, email="carol@example.com", workspace=WS, **extra):
    return await client.post(f"{workspace}/invitations", json={"email": email, **extra})


def _token(url: str) -> str:
    return parse_qs(urlparse(url).query)["token"][0]


@pytest.fixture
async def admin_client(client, alice, admins):
    admins.add(alice.id)
    await login(client)
    return client


async def test_admin_invites_and_invitee_registers(admin_client, sessionmaker):
    response = await _invite(admin_client, "Carol@Example.com")
    assert response.status_code == 201
    body = response.json()
    assert body["url"].startswith(f"{BASE_URL}/invite?token=") and body["email"] == "carol@example.com"
    token = _token(body["url"])
    async with sessionmaker() as session:
        stored = (await session.execute(select(Invitation.token_hash))).scalars().all()
    assert token not in stored

    admin_client.cookies.clear()
    accepted = await admin_client.post("/api/auth/invitations/accept",
                                       json={"token": token, "name": "Carol", "password": PASSWORD})
    assert accepted.status_code == 201 and accepted.json()["email"] == "carol@example.com"
    assert (await admin_client.get("/api/auth/me")).json()["name"] == "Carol"

    reused = await admin_client.post("/api/auth/invitations/accept",
                                     json={"token": token, "name": "Mallory", "password": PASSWORD})
    assert reused.status_code == 400
    assert (await login(admin_client, "carol@example.com")).status_code == 200


async def test_non_admin_cannot_invite(client, alice):
    await login(client)
    assert (await _invite(client)).status_code == 403


async def test_admin_of_one_workspace_cannot_invite_into_another(admin_client):
    assert (await _invite(admin_client, workspace="/api/workspaces/ws2")).status_code == 403


async def test_invitation_role_is_member_or_admin(admin_client):
    assert (await _invite(admin_client, role="admin")).json()["role"] == "admin"
    assert (await _invite(admin_client, "dan@example.com")).json()["role"] == "editor", "不写角色就是成员"
    assert (await _invite(admin_client, "eve@example.com", role="owner")).status_code == 422, "所有者不能靠邀请"


async def test_invitee_lands_where_the_invitation_says(sessionmaker, config, clock):
    placed = []

    async def hook(user_id, placement):
        placed.append((user_id, placement))

    identity = build_identity(sessionmaker, config, clock=clock, on_user_created=hook)
    issued = await identity.invitations.create(email="gina@example.com", invited_by="root",
                                               placement=Placement("ws9", "admin"), caller_is_admin=True)
    accepted = await identity.invitations.accept(token=issued.token, name="G", password=PASSWORD)
    assert placed == [(accepted.user.id, Placement("ws9", "admin"))]


async def test_revoke_only_within_the_issuing_workspace(admin_client, identity):
    issued = await identity.invitations.create(email="hank@example.com", invited_by="root",
                                               placement=Placement("ws2", "editor"), caller_is_admin=True)
    assert (await admin_client.delete(f"{WS}/invitations/{issued.id}")).status_code == 404, "别的空间发的撤不了"


async def test_unauthenticated_cannot_invite(client):
    assert (await _invite(client)).status_code == 401


async def test_invite_existing_or_bad_email(admin_client):
    assert (await _invite(admin_client, "alice@example.com")).status_code == 409
    assert (await _invite(admin_client, "not-an-email")).status_code == 422


async def test_expired_invitation_is_rejected(admin_client, clock):
    token = _token((await _invite(admin_client)).json()["url"])
    clock.advance(days=3, seconds=1)
    response = await admin_client.post("/api/auth/invitations/accept",
                                       json={"token": token, "name": "Carol", "password": PASSWORD})
    assert response.status_code == 400 and response.json()["detail"] == "邀请无效或已过期"


async def test_revoked_invitation_is_rejected(admin_client):
    body = (await _invite(admin_client)).json()
    assert (await admin_client.delete(f"{WS}/invitations/{body['id']}")).status_code == 204
    assert (await admin_client.delete(f"{WS}/invitations/{body['id']}")).status_code == 404
    response = await admin_client.post("/api/auth/invitations/accept",
                                       json={"token": _token(body["url"]), "name": "C", "password": PASSWORD})
    assert response.status_code == 400


async def test_non_admin_cannot_revoke(identity, client, alice):
    issued = await identity.invitations.create(email="dave@example.com", invited_by="root", placement=MEMBER, caller_is_admin=True)
    await login(client)
    assert (await client.delete(f"{WS}/invitations/{issued.id}")).status_code == 403
    with pytest.raises(NotAllowed):
        await identity.invitations.create(email="erin@example.com", invited_by=alice.id, placement=MEMBER, caller_is_admin=False)


async def test_accept_validates_input(client):
    unknown = await client.post("/api/auth/invitations/accept",
                                json={"token": "x" * 43, "name": "C", "password": PASSWORD})
    assert unknown.status_code == 400
    weak = await client.post("/api/auth/invitations/accept", json={"token": "x" * 43, "name": "C", "password": "123"})
    assert weak.status_code == 422


async def test_invitee_registered_elsewhere_cannot_reuse(identity):
    issued = await identity.invitations.create(email="frank@example.com", invited_by="root", placement=MEMBER, caller_is_admin=True)
    await identity.users.create_user(email="frank@example.com", name="Frank", password=PASSWORD)
    from knowwork_server.identity.errors import InvalidInvitation
    with pytest.raises(InvalidInvitation):
        await identity.invitations.accept(token=issued.token, name="F", password=PASSWORD)
