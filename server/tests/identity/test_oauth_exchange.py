"""令牌交换（RFC 8693）：原生应用用自己的访问令牌，按受众换一个范围更小的短期令牌（例如给本机宿主调模型用）。

开源只做交换的流程；每个受众怎么签由登记的交换者决定，没登记的受众一律拒绝。"""

import pytest

from knowwork_server.identity.oauth import OAuthError
from knowwork_server.identity.oauth_exchange import ACCESS_TOKEN_TYPE, TOKEN_EXCHANGE, Exchanged

from .test_oauth import CLIENT, tokens_for

pytestmark = pytest.mark.anyio


@pytest.fixture
def model_audience(identity):
    seen = []

    async def exchange(principal, params):
        seen.append((principal.user_id, principal.grant_id is not None, params.get("workspace_id")))
        if params.get("workspace_id") == "ws-other":
            raise OAuthError("invalid_target", "不是这个空间的成员")
        return Exchanged(token="signed-model-token", expires_in=300)

    identity.oauth_exchangers.register("model", exchange)
    return seen


async def exchange(client, subject: str, **overrides):
    form = {"grant_type": TOKEN_EXCHANGE, "client_id": CLIENT, "subject_token": subject,
            "subject_token_type": ACCESS_TOKEN_TYPE, "audience": "model", "workspace_id": "ws1"}
    form.update(overrides)
    return await client.post("/api/auth/oauth/token", data=form)


async def test_exchanges_an_access_token_for_an_audience_token(client, desktop, alice, model_audience):
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"])
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"access_token": "signed-model-token", "issued_token_type": ACCESS_TOKEN_TYPE,
                               "token_type": "Bearer", "expires_in": 300}
    assert model_audience == [(alice.id, True, "ws1")]


async def test_unknown_audience_is_refused(client, desktop, alice, model_audience):
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"], audience="mail")
    assert response.status_code == 400 and response.json()["error"] == "invalid_target"


async def test_the_audience_can_refuse(client, desktop, alice, model_audience):
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"], workspace_id="ws-other")
    assert response.status_code == 400 and response.json()["error"] == "invalid_target"


async def test_only_live_access_tokens_can_be_exchanged(client, desktop, alice, model_audience, clock):
    tokens = await tokens_for(client, alice)
    assert (await exchange(client, tokens["refresh_token"])).json()["error"] == "invalid_grant"
    assert (await exchange(client, "kwat_nope")).json()["error"] == "invalid_grant"
    clock.advance(hours=1, seconds=1)
    assert (await exchange(client, tokens["access_token"])).json()["error"] == "invalid_grant"
    assert model_audience == []


async def test_subject_token_type_must_be_an_access_token(client, desktop, alice, model_audience):
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"], subject_token_type="urn:ietf:params:oauth:token-type:jwt")
    assert response.status_code == 400 and response.json()["error"] == "invalid_request"


async def test_another_clients_token_cannot_be_exchanged(client, desktop, alice, model_audience, identity):
    from knowwork_server.identity.oauth_clients import OAuthClient
    identity.oauth_clients.register(OAuthClient(client_id="kw-cli", name="命令行", redirect_path="/cb"))
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"], client_id="kw-cli")
    assert response.status_code == 400 and response.json()["error"] == "invalid_grant"


async def test_unregistered_client_is_refused(client, desktop, alice, model_audience):
    tokens = await tokens_for(client, alice)
    response = await exchange(client, tokens["access_token"], client_id="nobody")
    assert response.status_code == 400 and response.json()["error"] == "invalid_client"
