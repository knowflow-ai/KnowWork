"""原生应用登录（OAuth 2.0 授权码 + PKCE，RFC 6749 / 7636 / 8252 / 7009）：桌面端经系统浏览器登录拿令牌。

开源只提供授权服务；客户端由扩展登记，没登记的一律拒绝。"""

import secrets
from urllib.parse import parse_qs, urlsplit

import pytest
from authlib.oauth2.rfc7636 import create_s256_code_challenge
from sqlalchemy import update

from knowwork_server.identity.models import USER_DISABLED, User

from .conftest import login

pytestmark = pytest.mark.anyio

CLIENT = "kw-desktop"
REDIRECT = "http://127.0.0.1:53111/callback"


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    return verifier, create_s256_code_challenge(verifier)


def authorize_params(challenge: str, **overrides) -> dict:
    params = {"response_type": "code", "client_id": CLIENT, "redirect_uri": REDIRECT, "code_challenge": challenge,
              "code_challenge_method": "S256", "state": "st-1", "device_name": "Alice 的 MacBook"}
    params.update(overrides)
    return params


async def approve(client, challenge: str, **overrides) -> str:
    response = await client.post("/api/auth/oauth/authorize", json={**authorize_params(challenge, **overrides),
                                                                      "approve": True})
    assert response.status_code == 200, response.text
    target = urlsplit(response.json()["redirectTo"])
    query = parse_qs(target.query)
    assert f"{target.scheme}://{target.netloc}{target.path}" == overrides.get("redirect_uri", REDIRECT)
    assert query["state"] == ["st-1"]
    return query["code"][0]


async def exchange(client, code: str, verifier: str, **overrides):
    form = {"grant_type": "authorization_code", "client_id": CLIENT, "code": code, "redirect_uri": REDIRECT,
            "code_verifier": verifier}
    form.update(overrides)
    return await client.post("/api/auth/oauth/token", data=form)


async def refresh(client, refresh_token: str):
    return await client.post("/api/auth/oauth/token", data={"grant_type": "refresh_token", "client_id": CLIENT,
                                                            "refresh_token": refresh_token})


async def tokens_for(client, alice) -> dict:
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge)
    client.cookies.clear()
    response = await exchange(client, code, verifier)
    assert response.status_code == 200, response.text
    return response.json()


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def test_unregistered_client_is_rejected_without_redirect(client, alice):
    await login(client)
    _, challenge = pkce()
    response = await client.get("/api/auth/oauth/authorize", params=authorize_params(challenge))
    assert response.status_code == 200
    assert response.json()["error"]["code"] == "invalid_client"
    assert "redirectTo" not in response.json()["error"]


@pytest.mark.parametrize("redirect_uri", [
    "http://localhost:53111/callback",  # RFC 8252 8.3：不用 localhost，只认回环 IP 字面量
    "https://127.0.0.1:53111/callback",
    "http://127.0.0.1:53111/other",
    "http://10.0.0.8:53111/callback",
    "http://127.0.0.1:53111/callback?x=1",
    "http://user@127.0.0.1:53111/callback",
])
async def test_only_registered_loopback_redirects_are_accepted(client, desktop, alice, redirect_uri):
    await login(client)
    _, challenge = pkce()
    response = await client.get("/api/auth/oauth/authorize", params=authorize_params(challenge,
                                                                                    redirect_uri=redirect_uri))
    assert response.json()["error"]["code"] == "invalid_request"
    assert "redirectTo" not in response.json()["error"]


async def test_any_loopback_port_and_ipv6_are_accepted(client, desktop, alice):
    await login(client)
    _, challenge = pkce()
    for redirect_uri in ("http://127.0.0.1:61000/callback", "http://[::1]:5000/callback"):
        response = await client.get("/api/auth/oauth/authorize", params=authorize_params(challenge,
                                                                                        redirect_uri=redirect_uri))
        assert response.status_code == 200, response.text
        assert response.json()["client"] == {"id": CLIENT, "name": "KnowWork 桌面端"}
        assert response.json()["deviceName"] == "Alice 的 MacBook"


async def test_plain_pkce_is_refused_back_to_the_client(client, desktop, alice):
    await login(client)
    response = await client.get("/api/auth/oauth/authorize",
                                params=authorize_params("x" * 43, code_challenge_method="plain"))
    failure = response.json()["error"]
    assert failure["code"] == "invalid_request"
    query = parse_qs(urlsplit(failure["redirectTo"]).query)
    assert query["error"] == ["invalid_request"] and query["state"] == ["st-1"]


async def test_approving_requires_a_signed_in_browser(client, desktop, alice):
    _, challenge = pkce()
    response = await client.post("/api/auth/oauth/authorize", json={**authorize_params(challenge), "approve": True})
    assert response.status_code == 401


async def test_a_desktop_token_cannot_approve_another_login(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    _, challenge = pkce()
    response = await client.post("/api/auth/oauth/authorize", json={**authorize_params(challenge), "approve": True},
                                 headers=bearer(tokens["access_token"]))
    assert response.status_code == 403


async def test_approving_an_invalid_request_is_refused(client, desktop, alice):
    await login(client)
    response = await client.post("/api/auth/oauth/authorize",
                                 json={**authorize_params("x" * 43, code_challenge_method="plain"), "approve": True})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_request"


async def test_denying_sends_access_denied_back(client, desktop, alice):
    await login(client)
    _, challenge = pkce()
    response = await client.post("/api/auth/oauth/authorize", json={**authorize_params(challenge), "approve": False})
    query = parse_qs(urlsplit(response.json()["redirectTo"]).query)
    assert query["error"] == ["access_denied"] and query["state"] == ["st-1"] and "code" not in query


async def test_full_flow_gives_a_token_that_acts_as_the_user(client, desktop, alice):
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge)
    client.cookies.clear()
    response = await exchange(client, code, verifier)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["token_type"] == "Bearer" and body["expires_in"] == 3600
    me = await client.get("/api/auth/me", headers=bearer(body["access_token"]))
    assert me.status_code == 200 and me.json()["id"] == alice.id


async def test_access_token_is_not_limited_by_scopes(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    response = await client.post("/api/probe/write", headers=bearer(tokens["access_token"]))
    assert response.json() == {"userId": alice.id, "method": "oauth"}


async def test_wrong_verifier_is_rejected(client, desktop, alice):
    await login(client)
    _, challenge = pkce()
    code = await approve(client, challenge)
    response = await exchange(client, code, secrets.token_urlsafe(48))
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_redirect_uri_must_match_the_one_approved(client, desktop, alice):
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge)
    response = await exchange(client, code, verifier, redirect_uri="http://127.0.0.1:53112/callback")
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_code_expires_quickly(client, desktop, alice, clock):
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge)
    clock.advance(minutes=2)
    response = await exchange(client, code, verifier)
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_reusing_a_code_revokes_what_it_issued(client, desktop, alice):
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge)
    client.cookies.clear()
    first = (await exchange(client, code, verifier)).json()
    again = await exchange(client, code, verifier)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    me = await client.get("/api/auth/me", headers=bearer(first["access_token"]))
    assert me.status_code == 401


async def test_refresh_rotates_tokens(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    response = await refresh(client, tokens["refresh_token"])
    assert response.status_code == 200
    renewed = response.json()
    assert renewed["refresh_token"] != tokens["refresh_token"]
    assert (await client.get("/api/auth/me", headers=bearer(renewed["access_token"]))).status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401


async def test_replaying_an_old_refresh_token_revokes_the_login(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    renewed = (await refresh(client, tokens["refresh_token"])).json()
    replay = await refresh(client, tokens["refresh_token"])
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert (await client.get("/api/auth/me", headers=bearer(renewed["access_token"]))).status_code == 401
    assert (await refresh(client, renewed["refresh_token"])).status_code == 400


async def test_access_token_expires_but_refresh_still_works(client, desktop, alice, clock):
    tokens = await tokens_for(client, alice)
    clock.advance(hours=1, seconds=1)
    assert (await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401
    assert (await refresh(client, tokens["refresh_token"])).status_code == 200


async def test_refresh_token_expires_after_long_idle(client, desktop, alice, clock):
    tokens = await tokens_for(client, alice)
    clock.advance(days=31)
    assert (await refresh(client, tokens["refresh_token"])).status_code == 400


async def test_revocation_ends_the_login(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    response = await client.post("/api/auth/oauth/revoke", data={"token": tokens["refresh_token"],
                                                                "client_id": CLIENT})
    assert response.status_code == 200
    assert (await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401
    assert (await refresh(client, tokens["refresh_token"])).status_code == 400


async def test_revoking_unknown_tokens_still_answers_ok(client, desktop):
    response = await client.post("/api/auth/oauth/revoke", data={"token": "kwrt_nope", "client_id": CLIENT})
    assert response.status_code == 200


async def test_disabled_user_loses_access_at_once(client, desktop, alice, sessionmaker):
    tokens = await tokens_for(client, alice)
    async with sessionmaker() as session, session.begin():
        await session.execute(update(User).where(User.id == alice.id).values(status=USER_DISABLED))
    assert (await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401
    assert (await refresh(client, tokens["refresh_token"])).status_code == 400


async def test_token_endpoint_rejects_unknown_clients_and_grant_types(client, desktop, alice):
    response = await client.post("/api/auth/oauth/token", data={"grant_type": "refresh_token",
                                                                "client_id": "someone", "refresh_token": "x"})
    assert response.status_code == 400 and response.json()["error"] == "invalid_client"
    response = await client.post("/api/auth/oauth/token", data={"grant_type": "password", "client_id": CLIENT})
    assert response.status_code == 400 and response.json()["error"] == "unsupported_grant_type"


async def test_signed_in_devices_can_be_listed_and_signed_out(client, desktop, alice):
    tokens = await tokens_for(client, alice)
    await login(client)
    devices = (await client.get("/api/auth/devices")).json()
    assert [(d["clientName"], d["deviceName"]) for d in devices] == [("KnowWork 桌面端", "Alice 的 MacBook")]
    assert (await client.delete(f"/api/auth/devices/{devices[0]['id']}")).status_code == 204
    assert (await client.get("/api/auth/devices")).json() == []
    client.cookies.clear()
    assert (await client.get("/api/auth/me", headers=bearer(tokens["access_token"]))).status_code == 401


async def test_cannot_sign_out_someone_elses_device(client, desktop, alice, identity):
    await tokens_for(client, alice)
    await identity.users.create_user(email="bob@example.com", name="Bob", password="correct horse battery")
    await login(client)
    device = (await client.get("/api/auth/devices")).json()[0]
    client.cookies.clear()
    await login(client, email="bob@example.com")
    assert (await client.delete(f"/api/auth/devices/{device['id']}")).status_code == 404


DEVICE = "dev_" + "a1" * 16


async def test_the_device_id_given_at_sign_in_stays_with_the_login(client, desktop, alice, identity):
    """应用登录时可以报一个自己生成、长期不变的设备号：同一台设备重新登录后还是它，扩展据此认「是不是那台设备」。"""
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge, device_id=DEVICE)
    client.cookies.clear()
    tokens = (await exchange(client, code, verifier)).json()
    principal = await identity.oauth.resolve(tokens["access_token"])
    assert await identity.oauth.device_id(principal.grant_id) == DEVICE
    renewed = (await refresh(client, tokens["refresh_token"])).json()
    again = await identity.oauth.resolve(renewed["access_token"])
    assert await identity.oauth.device_id(again.grant_id) == DEVICE, "刷新令牌不换设备"
    await login(client)
    listed = (await client.get("/api/auth/devices")).json()
    assert [d["deviceId"] for d in listed] == [DEVICE]


async def test_sign_in_without_a_device_id_has_none(client, desktop, alice, identity):
    tokens = await tokens_for(client, alice)
    principal = await identity.oauth.resolve(tokens["access_token"])
    assert await identity.oauth.device_id(principal.grant_id) is None
    assert await identity.oauth.device_id("0" * 32) is None


@pytest.mark.parametrize("bad", ["short", "有中文的设备号aaaaaaaaaaaa", "x" * 65, "a b c d e f g h i j k"])
async def test_malformed_device_ids_are_refused(client, desktop, alice, bad):
    await login(client)
    _, challenge = pkce()
    response = await client.get("/api/auth/oauth/authorize", params=authorize_params(challenge, device_id=bad))
    assert response.json()["error"]["code"] == "invalid_request"


async def test_a_revoked_login_has_no_device(client, desktop, alice, identity):
    await login(client)
    verifier, challenge = pkce()
    code = await approve(client, challenge, device_id=DEVICE)
    tokens = (await exchange(client, code, verifier)).json()
    principal = await identity.oauth.resolve(tokens["access_token"])
    await client.delete(f"/api/auth/devices/{principal.grant_id}")
    assert await identity.oauth.device_id(principal.grant_id) is None
