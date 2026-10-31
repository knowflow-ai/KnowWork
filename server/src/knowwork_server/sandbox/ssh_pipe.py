"""流式执行的字节管道：宿主的 ssh 客户端经这里连进会话容器里的 `sshd -i`。

宿主用 DSH 官方的 SSH 执行环境（dsh-ssh 与配套的子进程提供方）跑执行中要来回通话的能力：PTC 的 fd 7 控制管道、
终端、stdin/stdout 管道。这里只做最底下那一段传输：
- 只转发字节，不分帧、不看内容。SSH 自己做多路复用与流控，程序的每条流在 SSH 里各占一条通道，官方辅助程序
  再用 TLS-PSK 认证两端；业务服务看不到也改不了里面的内容（技能环境变量因此只在收集模式的 exec 里注入）；
- 连之前把这次的临时公钥装进容器：宿主为每个执行环境现生成一对，公钥随握手带来。登录账号与 exec 的执行账号
  一致（root 或 uid 1000），两条路径建的文件属主才不打架；
- 管道开着期间持有容器的 using()：空闲清扫跳过正在用的容器；
- 握手时判过一次权限，开着期间每 30 秒再判一次（still_allowed），被移出空间就断开，同 HTTP 接口「权限收回立即生效」；
- 一头断了另一头跟着断：宿主断开就关 sshd 的 stdin，sshd 退出后官方辅助程序（它的子进程）按租约清掉它管的进程。
"""

import asyncio
import contextlib
import logging
import re
import socket as pysocket
from collections.abc import Callable
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Protocol

from docker.utils.socket import STDERR, STDOUT, frames_iter

from .containers import ContainerManager, SandboxKey
from .contract import SandboxError
from .docker_engine import DockerEngine
from .execution import ExecRequest, RawExec, run
from .scripts import SSH_AUTHORIZE, SSH_IDENTITY, SSHD_ARGV

log = logging.getLogger(__name__)

# ed25519 公钥：类型名加 51 字节密钥块的 base64（68 个字符，不带补位）
PUBLIC_KEY = re.compile(r"^ssh-ed25519 [A-Za-z0-9+/]{68}$")
ACCOUNT = re.compile(r"^[a-z_][a-z0-9_-]{0,31}$")
SCRIPT_TIMEOUT_SECONDS = 15.0
CLOSE_GRACE_SECONDS = 5.0
STDERR_TAIL_BYTES = 4096
RECHECK_SECONDS = 30.0


class SshUnavailable(SandboxError):
    """镜像不带 sshd、主机密钥或执行账号；宿主据此认定这个部署不能流式执行。"""


class InvalidKey(SandboxError):
    """宿主带来的公钥格式不对。"""


UNAVAILABLE = "这个部署的沙箱镜像不支持流式执行"
BUSY = "流式执行的连接数已满，请稍后再试"


@dataclass(frozen=True)
class SshIdentity:
    user: str  # 登录账号名
    host_key: str  # 本容器的主机公钥（类型 + base64）


class Peer(Protocol):
    """管道的宿主那一头（内部接口里是 WebSocket）。"""

    async def receive(self) -> bytes | None:
        """下一段字节；对端关了返回 None。"""
        ...

    async def send(self, data: bytes) -> None:
        """对端已断时抛 ConnectionError。"""
        ...


def _raw(sock: Any) -> Any:
    return getattr(sock, "_sock", sock)


def _shutdown(sock: Any, how: int) -> None:
    with contextlib.suppress(OSError):
        _raw(sock).shutdown(how)


class SshPipes:
    def __init__(self, engine: DockerEngine, containers: ContainerManager, *, exec_user: str, max_pipes: int,
                 recheck_seconds: float = RECHECK_SECONDS) -> None:
        self._engine = engine
        self._recheck = recheck_seconds
        self._containers = containers
        self._account = exec_user.partition(":")[0]  # root，或 uid（getent 按 uid 也查得到名字）
        self._max = max_pipes
        self._open = 0
        # 每条管道两个线程（读 sshd 的输出、写它的输入），与普通 exec 的线程池分开，长连接不饿死短命令
        self._pool = ThreadPoolExecutor(max_workers=2 * max_pipes, thread_name_prefix="kw-sandbox-ssh")

    async def aclose(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    async def identity(self, workspace_id: str, session_id: str) -> SshIdentity:
        """登录账号与主机公钥（宿主写进 known_hosts）；容器没有就建。"""
        async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
            return await self._identity(str(container_id))

    async def relay(self, workspace_id: str, session_id: str, public_key: str, peer: Peer,
                    still_allowed: Callable[[], bool] | None = None) -> None:
        """装好公钥、起 `sshd -i`，在 peer 与 sshd 之间转发字节，直到任一头关掉，或 still_allowed 不再成立。"""
        if not PUBLIC_KEY.match(public_key):
            raise InvalidKey("公钥格式不对")
        if self._open >= self._max:
            raise SandboxError(BUSY)
        self._open += 1
        try:
            async with self._containers.using(SandboxKey.of(workspace_id, session_id)) as container_id:
                cid = str(container_id)
                identity = await self._identity(cid)
                if (await self._as_root(cid, SSH_AUTHORIZE, [identity.user, public_key])).exit_code != 0:
                    raise SandboxError("流式执行准备失败")
                exec_id = await self._engine.exec_create(cid, list(SSHD_ARGV), user="root", workdir="/", stdin=True)
                sock = await self._engine.exec_attach(exec_id)
                await self._pump(sock, peer, still_allowed)
        finally:
            self._open -= 1

    async def _identity(self, container_id: str) -> SshIdentity:
        result = await self._as_root(container_id, SSH_IDENTITY, [self._account])
        if result.exit_code in (3, 4):
            raise SshUnavailable(UNAVAILABLE)
        lines = result.stdout.decode("utf-8", errors="replace").splitlines()
        if result.exit_code != 0 or len(lines) < 2 or not ACCOUNT.match(lines[0]) or not PUBLIC_KEY.match(lines[1]):
            log.warning("沙箱容器里的 SSH 身份读不出来（退出码 %s）", result.exit_code)
            raise SshUnavailable(UNAVAILABLE)
        return SshIdentity(user=lines[0], host_key=lines[1])

    async def _as_root(self, container_id: str, script: str, args: list[str]) -> RawExec:
        # 装公钥要写 /etc/ssh，sshd -i 要 root 才能做权限分离，所以这两步固定用 root（不受 exec_user 影响）
        request = ExecRequest(argv=("sh", "-c", script, "kw", *args), user="root", workdir="/",
                              timeout_seconds=SCRIPT_TIMEOUT_SECONDS, max_output_bytes=8192)
        return await run(self._engine, container_id, request)

    async def _revoked(self, still_allowed: Callable[[], bool] | None) -> None:
        """权限还在就一直等；收回了就返回（管道随之断开）。"""
        if still_allowed is None:
            await asyncio.Event().wait()
        while True:
            await asyncio.sleep(self._recheck)
            if not still_allowed():
                log.info("流式执行管道的调用方已没有权限，断开")
                return

    async def _pump(self, sock: Any, peer: Peer, still_allowed: Callable[[], bool] | None = None) -> None:
        loop = asyncio.get_running_loop()
        finished = asyncio.Event()
        stderr = bytearray()
        sending: list[Future[None]] = []  # 读线程正在等的那次发送：收尾时取消，否则对端卡死时读线程永远等下去

        def downstream() -> None:
            """容器 → 宿主：sshd 的 stdout 原样交给 peer；stderr 是 sshd 自己的日志，只留尾巴备查。"""
            try:
                for kind, payload in frames_iter(sock, tty=False):
                    if kind == STDOUT:
                        future = asyncio.run_coroutine_threadsafe(peer.send(payload), loop)
                        sending[:] = [future]
                        future.result()
                    elif kind == STDERR:
                        stderr.extend(payload)
                        del stderr[:-STDERR_TAIL_BYTES]
            except (OSError, RuntimeError, CancelledError) as error:  # 套接字被关、peer 已断：都按管道结束处理
                log.debug("流式执行管道结束：%r", error)
            finally:
                loop.call_soon_threadsafe(finished.set)

        async def upstream() -> None:
            """宿主 → 容器：写进 sshd 的 stdin；宿主断开时只关写端，sshd 读到 EOF 自己退出。"""
            raw = _raw(sock)
            with contextlib.suppress(OSError):
                while (data := await peer.receive()) is not None:
                    await loop.run_in_executor(self._pool, raw.sendall, data)
            _shutdown(sock, pysocket.SHUT_WR)

        reading = loop.run_in_executor(self._pool, downstream)
        writing = asyncio.ensure_future(upstream())
        ended = asyncio.ensure_future(finished.wait())
        revoked = asyncio.ensure_future(self._revoked(still_allowed))
        try:
            await asyncio.wait({ended, writing, revoked}, return_when=asyncio.FIRST_COMPLETED)
            if not finished.is_set() and not revoked.done():
                # 宿主先断：给 sshd 一点时间收尾（它退出时辅助程序开始清理），超时就硬关
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(asyncio.shield(ended), CLOSE_GRACE_SECONDS)
        finally:
            writing.cancel()
            ended.cancel()
            revoked.cancel()
            for future in sending:
                future.cancel()
            _shutdown(sock, pysocket.SHUT_RDWR)  # 唤醒读线程
            with contextlib.suppress(Exception):
                await asyncio.wait_for(reading, CLOSE_GRACE_SECONDS)
            with contextlib.suppress(Exception):
                _raw(sock).close()
            if stderr:
                log.debug("sshd 输出：%s", stderr.decode("utf-8", errors="replace"))


__all__ = ["PUBLIC_KEY", "InvalidKey", "Peer", "SshIdentity", "SshPipes", "SshUnavailable"]
