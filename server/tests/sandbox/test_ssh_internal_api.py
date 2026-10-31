"""流式执行的内部接口：WebSocket 握手先过令牌与成员检查、没打开时明确拒绝、通了以后原样转发；身份接口。"""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from knowwork_server.sandbox.internal_api import build_internal_router
from knowwork_server.sandbox.ssh_pipe import InvalidKey, SshIdentity

from .test_internal_api import FakeSandbox, FakeTokens

PREFIX = "/internal/extensions/sandbox"
KEY = "ssh-ed25519 " + "A" * 68
GOOD = {"authorization": "Bearer good", "x-knowwork-ssh-key": KEY}


class FakeSsh:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    async def identity(self, ws, sid):
        self.calls.append(("identity", ws, sid))
        return SshIdentity(user="root", host_key=KEY)

    async def relay(self, ws, sid, public_key, peer, still_allowed=None):
        self.calls.append(("relay", ws, sid, public_key, still_allowed()))
        while (data := await peer.receive()) is not None:
            await peer.send(b"echo:" + data)


def _client(ssh, *, member: bool = True) -> TestClient:
    app = FastAPI()
    app.include_router(build_internal_router(FakeSandbox(), FakeTokens(), lambda _u, _w: member,
                                             max_timeout_seconds=600, ssh=ssh))
    return TestClient(app)


def _close_code(client: TestClient, headers: dict[str, str]) -> int:
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect(f"{PREFIX}/ssh", headers=headers) as ws:
        ws.receive_bytes()
    return closed.value.code


def test_handshake_is_refused_without_a_valid_token_or_membership():
    assert _close_code(_client(FakeSsh()), {"x-knowwork-ssh-key": KEY}) == 4401
    assert _close_code(_client(FakeSsh()), {**GOOD, "authorization": "Bearer bad"}) == 4401
    assert _close_code(_client(FakeSsh(), member=False), GOOD) == 4403


def test_handshake_says_unavailable_when_streaming_is_off():
    assert _close_code(_client(None), GOOD) == 4409


def test_pipe_forwards_bytes_for_the_token_session():
    ssh = FakeSsh()
    with _client(ssh).websocket_connect(f"{PREFIX}/ssh", headers=GOOD) as ws:
        ws.send_bytes(b"SSH-2.0-client\r\n")
        assert ws.receive_bytes() == b"echo:SSH-2.0-client\r\n"
    assert ssh.calls == [("relay", "ws1", "s1", KEY, True)], "管道开着期间按当前成员身份复核"


def test_identity_endpoint():
    ssh = FakeSsh()
    client = _client(ssh)
    response = client.post(f"{PREFIX}/ssh-identity", json={}, headers={"authorization": "Bearer good"})
    assert response.status_code == 200 and response.json() == {"user": "root", "hostKey": KEY}
    off = _client(None).post(f"{PREFIX}/ssh-identity", json={}, headers={"authorization": "Bearer good"})
    assert off.status_code == 409 and off.json()["error"]["code"] == "ssh_unavailable"


def test_a_malformed_key_closes_with_its_own_code():
    class RejectingSsh(FakeSsh):
        async def relay(self, ws, sid, public_key, peer, still_allowed=None):
            raise InvalidKey("公钥格式不对")

    with pytest.raises(WebSocketDisconnect) as closed, \
            _client(RejectingSsh()).websocket_connect(f"{PREFIX}/ssh", headers=GOOD) as ws:
        ws.receive_bytes()
    assert closed.value.code == 4400
