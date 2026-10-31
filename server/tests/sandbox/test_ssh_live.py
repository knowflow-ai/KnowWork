"""真实 Docker 上的流式执行管道：真实的 ssh 客户端经 SshPipes 连进按生产姿态建的会话容器（打开流式执行：多加
SYS_CHROOT、启动时生成主机密钥、断网）里的 sshd -i，公钥登录后跑命令。

默认跳过；KNOWWORK_DOCKER_TESTS=1、本机有带 sshd 的沙箱镜像（KNOWWORK_SANDBOX_SSH_TEST_IMAGE，默认
knowwork-sandbox:ssh-world）与 ssh、ssh-keygen、nc 时运行。跑完删掉建过的容器。
"""

import asyncio
import os
import shutil
import tempfile
import uuid
from pathlib import Path

import docker
import pytest

from knowwork_server.sandbox import SandboxSettings, build_sandbox
from knowwork_server.sandbox.containers import SandboxKey

pytestmark = pytest.mark.anyio
IMAGE = os.environ.get("KNOWWORK_SANDBOX_SSH_TEST_IMAGE", "knowwork-sandbox:ssh-world")


def _ready() -> bool:
    if any(shutil.which(tool) is None for tool in ("ssh", "ssh-keygen", "nc")):
        return False
    try:
        client = docker.DockerClient.from_env(timeout=3)
        try:
            return bool(client.ping()) and bool(client.images.list(name=IMAGE))
        finally:
            client.close()
    except (docker.errors.DockerException, OSError):
        return False


if os.environ.get("KNOWWORK_DOCKER_TESTS") != "1" or not _ready():
    pytest.skip("未开启真实 Docker 测试、Docker 不可用，或缺带 sshd 的沙箱镜像与 ssh / nc", allow_module_level=True)


class StreamPeer:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader, self._writer = reader, writer

    async def receive(self) -> bytes | None:
        data = await self._reader.read(65536)
        return data or None

    async def send(self, data: bytes) -> None:
        try:
            self._writer.write(data)
            await self._writer.drain()
        except (OSError, RuntimeError) as error:
            raise ConnectionError("ssh 客户端已断开") from error


async def _ssh(work: Path, socket_path: Path, command: str) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        "ssh", "-F", "/dev/null", "-o", f"ProxyCommand=nc -U {socket_path}", "-o", f"UserKnownHostsFile={work / 'kh'}",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "StrictHostKeyChecking=yes", "-o", "BatchMode=yes",
        "-o", "IdentitiesOnly=yes", "-i", str(work / "key"), "root@kw-test", command,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await asyncio.wait_for(proc.communicate(), 60)
    return proc.returncode or 0, out.decode(), err.decode()


async def test_real_ssh_client_logs_in_through_the_pipe_and_runs_in_the_session_container():
    module = build_sandbox(SandboxSettings(docker_enabled=True, image=IMAGE, ssh_enabled=True, allow_internet=False,
                                           memory_bytes=512 * 1024 * 1024, exec_max_timeout_seconds=60))
    assert module is not None and module.ssh is not None
    ws, sid = "ws-test", f"s-{uuid.uuid4().hex[:12]}"
    work = Path(tempfile.mkdtemp(dir="/tmp", prefix="kw-ssh-live-"))  # Unix 套接字路径有长度上限，不用 pytest 的长路径
    try:
        identity = await module.ssh.identity(ws, sid)
        assert identity.user == "root" and identity.host_key.startswith("ssh-ed25519 ")
        await asyncio.to_thread(lambda: os.system(f"ssh-keygen -q -t ed25519 -N '' -f {work / 'key'} >/dev/null"))
        public_key = " ".join((work / "key.pub").read_text().split()[:2])
        (work / "kh").write_text(f"kw-test {identity.host_key}\n")

        async def serve(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                await module.ssh.relay(ws, sid, public_key, StreamPeer(reader, writer))
            finally:
                writer.close()

        server = await asyncio.start_unix_server(serve, path=str(work / "s"))
        try:
            code, out, err = await _ssh(work, work / "s", "id -u; pwd; tail -n +3 /proc/net/dev | cut -d: -f1 | tr -d ' '")
            assert code == 0, err
            uid, cwd, *devices = out.split()
            assert uid == "0" and cwd == "/root"
            assert not any(device.startswith("eth") for device in devices), "容器应当断网"
            # 第二次连接换一把新钥匙也能登：每个执行环境的公钥在连接前现装
            os.remove(work / "key")
            os.remove(work / "key.pub")
            await asyncio.to_thread(lambda: os.system(f"ssh-keygen -q -t ed25519 -N '' -f {work / 'key'} >/dev/null"))
            public_key = " ".join((work / "key.pub").read_text().split()[:2])
            code, out, err = await _ssh(work, work / "s", "echo again")
            assert (code, out.strip()) == (0, "again"), err
        finally:
            server.close()
            await server.wait_closed()
        for _ in range(50):
            if not module.containers.is_busy(SandboxKey.of(ws, sid)):
                break
            await asyncio.sleep(0.1)
        assert not module.containers.is_busy(SandboxKey.of(ws, sid)), "ssh 断开后管道收尾、释放容器"
    finally:
        await module.sandbox.destroy(ws, sid)
        await module.aclose()
        shutil.rmtree(work, ignore_errors=True)
