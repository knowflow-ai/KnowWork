"""流式执行：容器姿态只在打开时变、SSH 身份、字节管道两头的转发与关闭、连接数上限。真实 sshd 另由宿主的端到端测试覆盖。"""

import asyncio
import base64
import hashlib
import json
from dataclasses import asdict

import pytest

from knowwork_server.sandbox.config import POSTURE_VERSION, ContainerPosture
from knowwork_server.sandbox.containers import CAPABILITIES, SandboxKey, container_options
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.scripts import ENTRYPOINT, ENTRYPOINT_SSH, SSH_AUTHORIZE, SSH_IDENTITY, SSHD_ARGV
from knowwork_server.sandbox.ssh_pipe import SshUnavailable

from .fake_docker import Behavior, FakeDocker
from .helpers import SID, WS, make

pytestmark = pytest.mark.anyio


def _key(seed: int) -> str:
    blob = b"\0\0\0\x0bssh-ed25519\0\0\0\x20" + bytes((seed + i) % 256 for i in range(32))
    return "ssh-ed25519 " + base64.b64encode(blob).decode()


CLIENT_KEY, HOST_KEY = _key(1), _key(2)


def responder(user_name: str = "root", *, identity_exit: int = 0):
    def respond(argv: list[str], _user: str) -> Behavior:
        if argv[2:3] == [SSH_IDENTITY]:
            return Behavior(stdout=f"{user_name}\n{HOST_KEY}\n".encode(), exit_code=identity_exit)
        return Behavior()
    return respond


class Peer:
    """宿主那一头：inbound 里放要发给 sshd 的字节，None 表示宿主断开；收到的放 received。"""

    def __init__(self, on_receive=None) -> None:
        self.inbound: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.received = bytearray()
        self.got = asyncio.Event()
        self._on_receive = on_receive

    async def receive(self) -> bytes | None:
        data = await self.inbound.get()
        if self._on_receive is not None:
            await self._on_receive()
        return data

    async def send(self, data: bytes) -> None:
        self.received.extend(data)
        self.got.set()


def _posture(**changes) -> ContainerPosture:
    base = {"image": "kw-sandbox:1", "memory_bytes": 1 << 31, "nano_cpus": 2_000_000_000, "pids_limit": 512,
            "network_mode": "none", "runtime": ""}
    return ContainerPosture(**{**base, **changes})


def test_ssh_off_keeps_the_posture_digest_so_existing_containers_are_not_recreated():
    posture = _posture()
    legacy = {key: value for key, value in asdict(posture).items() if key != "ssh"}
    expected = hashlib.sha256(json.dumps({"v": POSTURE_VERSION, **legacy}, sort_keys=True).encode()).hexdigest()[:32]
    assert posture.digest == expected
    assert _posture(ssh=True).digest != expected


def test_ssh_on_adds_sys_chroot_and_host_key_generation_only_then():
    key = SandboxKey.of(WS, SID)
    off = container_options(key, _posture(), idle_ttl_seconds=60)
    assert off["cap_add"] == list(CAPABILITIES) and off["entrypoint"] == list(ENTRYPOINT)
    on = container_options(key, _posture(ssh=True), idle_ttl_seconds=60)
    assert on["cap_add"] == [*CAPABILITIES, "SYS_CHROOT"] and on["entrypoint"] == list(ENTRYPOINT_SSH)
    assert "ssh-keygen" in on["entrypoint"][2]


async def test_identity_uses_the_exec_account_and_reads_the_container_host_key():
    fake, module = make(FakeDocker(responder=responder("user")), ssh_enabled=True, exec_user="1000:1000")
    identity = await module.ssh.identity(WS, SID)
    assert (identity.user, identity.host_key) == ("user", HOST_KEY)
    record = next(r for r in fake.execs.values() if r.argv[2:3] == [SSH_IDENTITY])
    assert record.argv[4:] == ["1000"] and record.user == "root"


async def test_identity_reports_unavailable_when_the_image_has_no_sshd():
    _, module = make(FakeDocker(responder=responder(identity_exit=3)), ssh_enabled=True)
    with pytest.raises(SshUnavailable):
        await module.ssh.identity(WS, SID)


async def test_ssh_off_builds_no_pipes():
    _, module = make()
    assert module.ssh is None


async def test_relay_installs_the_key_then_forwards_bytes_both_ways_while_holding_the_container():
    fake, module = make(FakeDocker(responder=responder()), ssh_enabled=True)
    busy: list[bool] = []

    async def check_busy() -> None:
        busy.append(module.containers.is_busy(SandboxKey.of(WS, SID)))

    peer = Peer(on_receive=check_busy)
    relay = asyncio.create_task(module.ssh.relay(WS, SID, CLIENT_KEY, peer))
    await peer.inbound.put(b"SSH-2.0-client\r\n")
    await asyncio.wait_for(peer.got.wait(), 5)
    assert bytes(peer.received) == b"echo:SSH-2.0-client\r\n"
    await peer.inbound.put(None)  # 宿主断开：关 sshd 的 stdin，它退出，管道收尾
    await asyncio.wait_for(relay, 10)

    assert busy[0] is True, "管道开着期间持有容器，空闲清扫不会收走"
    assert not module.containers.is_busy(SandboxKey.of(WS, SID))
    authorize = next(r for r in fake.execs.values() if r.argv[2:3] == [SSH_AUTHORIZE])
    assert authorize.argv[4:] == ["root", CLIENT_KEY] and authorize.user == "root"
    sshd = next(r for r in fake.execs.values() if r.cmd == list(SSHD_ARGV))
    assert sshd.user == "root" and sshd.stdin is True and sshd.workdir == "/"


async def test_relay_rejects_a_malformed_key_before_touching_the_container():
    fake, module = make(FakeDocker(responder=responder()), ssh_enabled=True)
    with pytest.raises(SandboxError, match="公钥"):
        await module.ssh.relay(WS, SID, "ssh-rsa AAAA", Peer())
    assert fake.execs == {}


async def test_relay_refuses_beyond_the_pipe_limit():
    _, module = make(FakeDocker(responder=responder()), ssh_enabled=True, ssh_max_pipes=1)
    first = Peer()
    relay = asyncio.create_task(module.ssh.relay(WS, SID, CLIENT_KEY, first))
    await first.inbound.put(b"x")
    await asyncio.wait_for(first.got.wait(), 5)
    with pytest.raises(SandboxError, match="已满"):
        await module.ssh.relay(WS, "conv-2", CLIENT_KEY, Peer())
    await first.inbound.put(None)
    await asyncio.wait_for(relay, 10)


async def test_relay_closes_when_the_caller_loses_access():
    fake = FakeDocker(responder=responder())
    _, module = make(fake, ssh_enabled=True)
    module.ssh._recheck = 0.05
    peer = Peer()
    checks: list[bool] = []

    def still_allowed() -> bool:
        checks.append(True)
        return len(checks) < 2  # 第二次复核时已被移出空间

    relay = asyncio.create_task(module.ssh.relay(WS, SID, CLIENT_KEY, peer, still_allowed=still_allowed))
    await peer.inbound.put(b"x")
    await asyncio.wait_for(peer.got.wait(), 5)
    await asyncio.wait_for(relay, 10)  # 宿主没断开，管道也因权限收回而收尾
    assert len(checks) >= 2
    assert not module.containers.is_busy(SandboxKey.of(WS, SID))


async def test_a_stuck_send_does_not_leak_the_reader_thread(monkeypatch):
    """宿主那头的发送一直不返回（对端卡死）：收尾时要把这次发送取消掉，读线程才会退出、不占着线程池。
    max_pipes=1 时线程池只有两个线程，泄漏一个，下一条管道就起不来。"""
    from knowwork_server.sandbox import ssh_pipe

    monkeypatch.setattr(ssh_pipe, "CLOSE_GRACE_SECONDS", 0.2)
    _, module = make(FakeDocker(responder=responder()), ssh_enabled=True, ssh_max_pipes=1)

    class StuckPeer(Peer):
        async def send(self, data: bytes) -> None:
            self.got.set()
            await asyncio.Event().wait()  # 永远不返回

    stuck = StuckPeer()
    relay = asyncio.create_task(module.ssh.relay(WS, SID, CLIENT_KEY, stuck))
    await stuck.inbound.put(b"x")
    await asyncio.wait_for(stuck.got.wait(), 5)
    await stuck.inbound.put(None)
    await asyncio.wait_for(relay, 5)

    second = Peer()
    again = asyncio.create_task(module.ssh.relay(WS, "conv-2", CLIENT_KEY, second))
    await second.inbound.put(b"y")
    await asyncio.wait_for(second.got.wait(), 5)
    assert bytes(second.received) == b"echo:y"
    await second.inbound.put(None)
    await asyncio.wait_for(again, 5)
