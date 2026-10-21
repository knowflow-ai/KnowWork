"""邀请注册：只有管理员能邀请，链接一次性、会过期、可撤销，库里只存摘要。"""

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import select

from knowwork_server.identity.errors import NotAllowed
from knowwork_server.identity.models import Invitation

from .conftest import BASE_URL, PASSWORD, login

pytestmark = pytest.mark.anyio


async def _invite(client, email="carol@example.com"):
    return await client.post("/api/auth/invitations", json={"email": email})


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
    assert (await admin_client.delete(f"/api/auth/invitations/{body['id']}")).status_code == 204
    assert (await admin_client.delete(f"/api/auth/invitations/{body['id']}")).status_code == 404
    response = await admin_client.post("/api/auth/invitations/accept",
                                       json={"token": _token(body["url"]), "name": "C", "password": PASSWORD})
    assert response.status_code == 400


async def test_non_admin_cannot_revoke(identity, client, alice):
    issued = await identity.invitations.create(email="dave@example.com", invited_by="root", caller_is_admin=True)
    await login(client)
    assert (await client.delete(f"/api/auth/invitations/{issued.id}")).status_code == 403
    with pytest.raises(NotAllowed):
        await identity.invitations.create(email="erin@example.com", invited_by=alice.id, caller_is_admin=False)


async def test_accept_validates_input(client):
    unknown = await client.post("/api/auth/invitations/accept",
                                json={"token": "x" * 43, "name": "C", "password": PASSWORD})
    assert unknown.status_code == 400
    weak = await client.post("/api/auth/invitations/accept", json={"token": "x" * 43, "name": "C", "password": "123"})
    assert weak.status_code == 422


async def test_invitee_registered_elsewhere_cannot_reuse(identity):
    issued = await identity.invitations.create(email="frank@example.com", invited_by="root", caller_is_admin=True)
    await identity.users.create_user(email="frank@example.com", name="Frank", password=PASSWORD)
    from knowwork_server.identity.errors import InvalidInvitation
    with pytest.raises(InvalidInvitation):
        await identity.invitations.accept(token=issued.token, name="F", password=PASSWORD)
