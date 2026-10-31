"""扮 docker-py 客户端的测试桩：实现沙箱用到的 DockerClient.containers/images 与低层 api 方法，状态全在内存里。

异常用 docker-py 真实的异常类（NotFound、ImageNotFound、APIError），exec 输出走真实的 socketpair，
由 docker-py 自己的 frames_iter 解析，所以封装层的流读取、关闭与错误分类都被实际跑到。
exec 的行为由 responder 决定：拿到「包装脚本之后的那段 argv」与执行账号，返回输出、退出码，或挂起（模拟超时）。
"""

import io
import socket
import tarfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import requests
from docker.errors import APIError, ImageNotFound, NotFound

from knowwork_server.sandbox.output import STDERR, STDOUT, frame
from knowwork_server.sandbox.scripts import EXEC_WRAPPER, KILL_GROUP, SSHD_ARGV


@dataclass
class Behavior:
    stdout: bytes = b""
    stderr: bytes = b""
    exit_code: int = 0
    hang: bool = False
    ignore_kill: bool = False  # 被杀后连接仍不断开（考验关闭连接的兜底）


Responder = Callable[[list[str], str], Behavior]


class FakeResponse:
    def __init__(self, status: int) -> None:
        self.status_code = status
        self.reason = "fake"
        self.url = "http://docker/fake"


def api_error(status: int) -> APIError:
    return APIError("boom", response=FakeResponse(status))


@dataclass
class ExecRecord:
    container: str
    cmd: list[str]
    user: str
    workdir: str
    running: bool = False
    exit_code: int | None = None
    released: threading.Event = field(default_factory=threading.Event)
    stdin: bool = False

    @property
    def token(self) -> str | None:
        return self.cmd[4] if len(self.cmd) > 5 and self.cmd[2] == EXEC_WRAPPER else None

    @property
    def argv(self) -> list[str]:
        return self.cmd[6:] if self.cmd[2:3] == [EXEC_WRAPPER] else self.cmd


@dataclass
class FakeDocker:
    responder: Responder = field(default=lambda argv, user: Behavior())
    containers: dict[str, dict[str, Any]] = field(default_factory=dict)  # name -> 容器
    execs: dict[str, ExecRecord] = field(default_factory=dict)
    archives: list[tuple[str, str, bytes]] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    kills: list[str] = field(default_factory=list)
    images: set[str] = field(default_factory=lambda: {"kw-sandbox:1"})
    pulls: list[str] = field(default_factory=list)
    markers: dict[str, str] = field(default_factory=dict)  # 容器 Id -> 活动标记 mtime
    fail_all: int | None = None  # 设了就让所有调用返回这个状态码
    connect_error: bool = False  # 设了就让所有调用连不上
    fail: dict[str, Exception] = field(default_factory=dict)  # 方法名 -> 这次要抛的异常
    start_status: str = "running"  # start 之后容器进入的状态
    built: int = 0
    closed: bool = False
    _seq: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # -- docker-py 外形 -------------------------------------------------------

    def factory(self) -> "FakeClient":
        self.built += 1
        return FakeClient(self)

    def _next(self, prefix: str) -> str:
        with self._lock:
            self._seq += 1
            return f"{prefix}{self._seq:04d}"

    def guard(self, method: str) -> None:
        self.calls.append(method)
        if self.connect_error:
            raise requests.ConnectionError("refused")
        if self.fail_all is not None:
            raise api_error(self.fail_all)
        if method in self.fail:
            raise self.fail.pop(method)

    def by_ref(self, ref: str) -> dict[str, Any]:
        found = self.containers.get(ref) or next((c for c in self.containers.values() if c["Id"] == ref), None)
        if found is None:
            raise NotFound("No such container")
        return found

    def add_container(self, name: str, labels: dict[str, str], *, status: str = "running",
                      created: float | None = None) -> dict[str, Any]:
        container = {"Id": self._next("c"), "Name": name, "Labels": labels, "Status": status,
                     "Created": int(created if created is not None else time.time()), "Options": {}}
        self.containers[name] = container
        return container

    def exec_by_argv(self, argv: list[str]) -> ExecRecord:
        return next(record for record in self.execs.values() if record.argv == argv)


class FakeClient:
    def __init__(self, fake: FakeDocker) -> None:
        self.api = FakeApi(fake)
        self.containers = FakeContainers(fake)
        self.images = FakeImages(fake)
        self._fake = fake

    def close(self) -> None:
        self._fake.closed = True


class FakeContainers:
    def __init__(self, fake: FakeDocker) -> None:
        self._fake = fake

    def create(self, name: str, **options: Any) -> Any:
        self._fake.guard("create")
        if name in self._fake.containers:
            raise api_error(409)
        if options["image"] not in self._fake.images:
            raise ImageNotFound("No such image")
        container = self._fake.add_container(name, dict(options["labels"]), status="created")
        container["Options"] = options
        return type("Container", (), {"id": container["Id"]})()


class FakeImages:
    def __init__(self, fake: FakeDocker) -> None:
        self._fake = fake

    def get(self, image: str) -> Any:
        self._fake.guard("image_get")
        if image not in self._fake.images:
            raise ImageNotFound("No such image")
        return object()

    def pull(self, image: str) -> Any:
        self._fake.guard("pull")
        self._fake.pulls.append(image)
        self._fake.images.add(image)
        return object()


class FakeApi:
    def __init__(self, fake: FakeDocker) -> None:
        self._fake = fake

    def inspect_container(self, ref: str) -> dict[str, Any]:
        self._fake.guard("inspect")
        c = self._fake.by_ref(ref)
        return {"Id": c["Id"], "Config": {"Labels": c["Labels"]},
                "State": {"Status": c["Status"], "Running": c["Status"] == "running"}}

    def containers(self, all: bool, filters: dict[str, list[str]]) -> list[dict[str, Any]]:
        self._fake.guard("list")
        pairs = [item.split("=", 1) for item in filters.get("label", [])]
        return [{"Id": c["Id"], "Labels": c["Labels"], "State": c["Status"], "Created": c["Created"]}
                for c in self._fake.containers.values() if all and all_match(c["Labels"], pairs)]

    def start(self, container_id: str) -> None:
        self._fake.guard("start")
        self._fake.by_ref(container_id)["Status"] = self._fake.start_status

    def unpause(self, container_id: str) -> None:
        self._fake.guard("unpause")
        self._fake.by_ref(container_id)["Status"] = "running"

    def remove_container(self, container_id: str, force: bool, v: bool) -> None:
        self._fake.guard("remove")
        container = self._fake.by_ref(container_id)
        assert force and v
        del self._fake.containers[container["Name"]]

    def put_archive(self, container_id: str, path: str, data: bytes) -> bool:
        self._fake.guard("put_archive")
        self._fake.by_ref(container_id)
        self._fake.archives.append((container_id, path, data))
        return True

    def get_archive(self, container_id: str, path: str) -> tuple[Any, dict[str, Any]]:
        self._fake.guard("get_archive")
        container = self._fake.by_ref(container_id)
        if container["Id"] not in self._fake.markers:
            raise NotFound("no such file")
        return iter([b""]), {"name": path.rsplit("/", 1)[-1], "mtime": self._fake.markers[container["Id"]]}

    def exec_create(self, container_id: str, cmd: list[str], **options: Any) -> dict[str, str]:
        self._fake.guard("exec_create")
        self._fake.by_ref(container_id)
        exec_id = self._fake._next("e")
        self._fake.execs[exec_id] = ExecRecord(container_id, list(cmd), options["user"], options["workdir"],
                                               stdin=bool(options.get("stdin")))
        return {"Id": exec_id}

    def exec_start(self, exec_id: str, socket: bool) -> Any:
        self._fake.guard("exec_start")
        assert socket
        record = self._fake.execs[exec_id]
        if record.cmd[2:3] == [KILL_GROUP]:
            return self._kill(record)
        if record.cmd == list(SSHD_ARGV):
            return _serve_sshd(record)
        behavior = self._fake.responder(record.argv, record.user)
        return _serve(record, behavior)

    def _kill(self, record: ExecRecord) -> Any:
        token = record.cmd[4]
        self._fake.kills.append(token)
        for target in self._fake.execs.values():
            if target.token == token:
                target.exit_code = 137
                target.released.set()
        record.exit_code = 0
        return _serve(record, Behavior())

    def exec_inspect(self, exec_id: str) -> dict[str, Any]:
        self._fake.guard("exec_inspect")
        record = self._fake.execs[exec_id]
        return {"Running": record.running, "ExitCode": record.exit_code}


def all_match(labels: dict[str, str], pairs: list[list[str]]) -> bool:
    return all(labels.get(key) == value for key, value in pairs)


def _serve(record: ExecRecord, behavior: Behavior) -> socket.socket:
    """返回读端；另一个线程按 Docker 的帧格式写输出（故意切碎，覆盖帧跨块）。"""
    reader, writer = socket.socketpair()
    record.running = True

    def write() -> None:
        try:
            for start in range(0, len(behavior.stdout), 7):
                writer.sendall(frame(STDOUT, behavior.stdout[start:start + 7]))
            if behavior.stderr:
                writer.sendall(frame(STDERR, behavior.stderr))
            if behavior.hang:
                record.released.wait(30)
                if behavior.ignore_kill:
                    time.sleep(30)
        except OSError:
            pass
        finally:
            if record.exit_code is None:
                record.exit_code = behavior.exit_code
            record.running = False
            writer.close()

    threading.Thread(target=write, daemon=True).start()
    return reader


def _serve_sshd(record: ExecRecord) -> socket.socket:
    """假的 `sshd -i`：先写一行日志到 stderr，之后把 stdin 收到的每段加上 echo: 写回 stdout，读到 EOF 就退出。"""
    near, far = socket.socketpair()
    record.running = True

    def serve() -> None:
        try:
            far.sendall(frame(STDERR, b"sshd: ready\n"))
            while chunk := far.recv(65536):
                far.sendall(frame(STDOUT, b"echo:" + chunk))
        except OSError:
            pass
        finally:
            record.exit_code = 0
            record.running = False
            far.close()

    threading.Thread(target=serve, daemon=True).start()
    return near


def tar_members(data: bytes) -> list[tarfile.TarInfo]:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        return archive.getmembers()


def tar_payload(data: bytes, name: str) -> bytes:
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        extracted = archive.extractfile(name)
        assert extracted is not None
        return extracted.read()
