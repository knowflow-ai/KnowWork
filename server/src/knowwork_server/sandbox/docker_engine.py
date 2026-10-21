"""Docker Engine 的薄封装：官方 Python SDK（docker-py）的同步调用放进线程，错误统一转成 SandboxError。

对应 WeKnora 的 docker_engine.go + docker_rpc_timeout.go：
- 客户端第一次用到时才建（daemon 没起时不让启动失败，也不在构建时协商 API 版本）；
- 短调用（inspect、create、list、exec create/inspect）受 http_timeout 约束：docker-py 的 timeout 是每次读写的
  套接字超时；exec 的输出流在接管套接字后关掉超时，否则一条半分钟不出声的命令会被当成连接断开。
  镜像拉取有单独的总预算；
- exec 输出流放在专用线程池里读（数量有上限），不占默认线程池，免得长命令把其他 to_thread 调用饿死；
- 状态码与 Docker 原文只进日志，异常 message 只写面向用户的说明。
"""

import asyncio
import contextlib
import logging
import socket as pysocket
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

import docker
import requests
from docker.errors import APIError, DockerException, NotFound
from docker.utils.socket import frames_iter

from .contract import SandboxError
from .docker_host import ClientFactory

log = logging.getLogger(__name__)

UNAVAILABLE = "沙箱服务暂时不可用，请稍后再试"
FAILED = "沙箱操作失败"
IMAGE_UNAVAILABLE = "沙箱镜像不可用"
T = TypeVar("T")
Sink = Callable[[int, bytes], None]


class DockerNotFound(SandboxError):
    pass


class DockerConflict(SandboxError):
    pass


def translate(action: str, error: BaseException) -> SandboxError:
    """按 docker-py 的异常类型分类（比看文案可靠），与 WeKnora 的 dockerErrorKind 同一思路。"""
    if isinstance(error, SandboxError):
        return error
    if isinstance(error, NotFound):
        log.debug("docker %s: not found", action)
        return DockerNotFound(FAILED)
    if isinstance(error, APIError):
        status = error.status_code
        log.warning("docker %s 返回 %s", action, status)
        if status == 409:
            return DockerConflict(FAILED)
        return SandboxError(UNAVAILABLE if error.is_server_error() else FAILED)
    log.warning("docker %s 连接失败：%s", action, type(error).__name__)
    return SandboxError(UNAVAILABLE)


DOCKER_ERRORS = (DockerException, requests.RequestException, OSError)


def _no_timeout(sock: Any) -> None:
    """与 docker-py 的 APIClient._disable_socket_timeout 一样：包装层与底层套接字都关掉超时。"""
    for candidate in (sock, getattr(sock, "_sock", None)):
        if candidate is not None and hasattr(candidate, "settimeout"):
            candidate.settimeout(None)


class ExecStream:
    """一条 exec 输出连接。close() 可以从别的线程调用：只 shutdown 底层套接字把读线程唤醒，
    真正关闭文件描述符由读线程自己做——别的线程关掉 fd 后它可能被复用，读线程就会去 poll 一个不相干的连接。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sock: Any = None
        self.closed = False

    def attach(self, sock: Any) -> bool:
        with self._lock:
            if self.closed:
                _shutdown(sock)
                _close(sock)
                return False
            self._sock = sock
            return True

    def close(self) -> None:
        with self._lock:
            self.closed = True
            if self._sock is not None:
                _shutdown(self._sock)

    def release(self) -> None:
        """读线程结束时调用：关掉连接。"""
        with self._lock:
            self.closed = True
            sock, self._sock = self._sock, None
        if sock is not None:
            _shutdown(sock)
            _close(sock)


def _shutdown(sock: Any) -> None:
    raw = getattr(sock, "_sock", sock)
    with contextlib.suppress(OSError):
        raw.shutdown(pysocket.SHUT_RDWR)


def _close(sock: Any) -> None:
    with contextlib.suppress(OSError):
        sock.close()
    raw = getattr(sock, "_sock", None)
    if raw is not None:
        with contextlib.suppress(OSError):
            raw.close()


class DockerEngine:
    def __init__(self, factory: ClientFactory, *, pull_timeout_seconds: float = 600.0,
                 max_streams: int = 32) -> None:
        self._factory = factory
        self._client: docker.DockerClient | None = None
        self._lock = threading.Lock()
        self._pull_timeout = pull_timeout_seconds
        self._streams = ThreadPoolExecutor(max_workers=max_streams, thread_name_prefix="kw-sandbox-exec")

    def _docker(self) -> docker.DockerClient:
        with self._lock:
            if self._client is None:
                self._client = self._factory()
            return self._client

    async def _call(self, action: str, fn: Callable[[docker.DockerClient], T]) -> T:
        def run() -> T:
            return fn(self._docker())
        try:
            return await asyncio.to_thread(run)
        except DOCKER_ERRORS as error:
            raise translate(action, error) from error

    async def aclose(self) -> None:
        self._streams.shutdown(wait=False, cancel_futures=True)
        client, self._client = self._client, None
        if client is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(client.close)

    # -- 容器 ---------------------------------------------------------------

    async def inspect_container(self, ref: str) -> dict[str, Any] | None:
        try:
            return await self._call("inspect", lambda c: c.api.inspect_container(ref))
        except DockerNotFound:
            return None

    async def list_containers(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        filters = {"label": [f"{key}={value}" for key, value in labels.items()]}
        return await self._call("list", lambda c: c.api.containers(all=True, filters=filters))

    async def create_container(self, name: str, options: dict[str, Any]) -> str:
        return await self._call("create", lambda c: str(c.containers.create(name=name, **options).id))

    async def start_container(self, container_id: str) -> None:
        await self._call("start", lambda c: c.api.start(container_id))

    async def unpause_container(self, container_id: str) -> None:
        await self._call("unpause", lambda c: c.api.unpause(container_id))

    async def remove_container(self, container_id: str) -> None:
        """强制删除，连匿名卷一起删（WeKnora 的 Delete 同此）；本来就不存在不算错。"""
        try:
            await self._call("remove", lambda c: c.api.remove_container(container_id, force=True, v=True))
        except DockerNotFound:
            return

    async def put_archive(self, container_id: str, path: str, tar_bytes: bytes) -> None:
        ok = await self._call("put_archive", lambda c: c.api.put_archive(container_id, path, tar_bytes))
        if ok is False:
            raise SandboxError(FAILED)

    async def stat_path(self, container_id: str, path: str) -> dict[str, Any] | None:
        """读一个文件的元数据（WeKnora 用 ContainerStatPath 读活动标记的 mtime）。不存在时为 None。"""
        def stat(client: docker.DockerClient) -> dict[str, Any]:
            stream, info = client.api.get_archive(container_id, path)
            for _chunk in stream:  # 标记文件是空的，读完即可释放连接
                pass
            return dict(info or {})
        try:
            return await self._call("stat", stat)
        except DockerNotFound:
            return None

    # -- 镜像 ---------------------------------------------------------------

    async def ensure_image(self, image: str) -> None:
        """本机没有就拉。拉取按自己的总预算，不受单次调用超时约束（冷拉镜像要几分钟）。"""
        try:
            await self._call("image_inspect", lambda c: c.images.get(image))
            return
        except DockerNotFound:
            pass
        try:
            await asyncio.wait_for(self._call("pull", lambda c: c.images.pull(image)), self._pull_timeout)
        except TimeoutError as error:
            log.warning("拉取沙箱镜像超时")
            raise SandboxError(IMAGE_UNAVAILABLE) from error
        except SandboxError as error:
            raise SandboxError(IMAGE_UNAVAILABLE) from error

    # -- exec ---------------------------------------------------------------

    async def exec_create(self, container_id: str, cmd: list[str], *, user: str, workdir: str) -> str:
        def create(client: docker.DockerClient) -> str:
            created = client.api.exec_create(container_id, cmd, stdout=True, stderr=True, stdin=False, tty=False,
                                             user=user, workdir=workdir)
            return str(created["Id"])
        return await self._call("exec_create", create)

    async def exec_run(self, exec_id: str, stream: ExecStream, sink: Sink) -> None:
        """启动 exec 并把输出按流（1 stdout / 2 stderr）交给 sink，直到进程结束或 stream 被关掉。"""
        def pump() -> None:
            sock = self._docker().api.exec_start(exec_id, socket=True)
            _no_timeout(sock)
            if not stream.attach(sock):
                return
            try:
                for kind, payload in frames_iter(sock, tty=False):
                    sink(kind, payload)
            except OSError:
                if not stream.closed:
                    raise
            finally:
                stream.release()
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._streams, pump)
        except DOCKER_ERRORS as error:
            raise translate("exec_start", error) from error
        except RuntimeError as error:  # 线程池已关闭（服务正在退出）
            raise SandboxError(UNAVAILABLE) from error

    async def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        return await self._call("exec_inspect", lambda c: c.api.exec_inspect(exec_id))


__all__ = ["DockerConflict", "DockerEngine", "DockerNotFound", "ExecStream", "translate"]
