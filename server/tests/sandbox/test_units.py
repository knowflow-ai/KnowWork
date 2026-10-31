"""配置与 daemon 地址校验、工具层、路径、输出收集、docker-py 错误分类与封装层的小单元。"""

import re
import threading

import pytest
import requests
from docker.errors import APIError, NotFound

from knowwork_server.sandbox import SandboxSettings, build_sandbox, scripts
from knowwork_server.sandbox.config import ContainerPosture
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.docker_engine import DockerConflict, DockerEngine, DockerNotFound, ExecStream, translate
from knowwork_server.sandbox.docker_host import client_factory, tls_config, validate_docker_host
from knowwork_server.sandbox.docker_sandbox import owner_of
from knowwork_server.sandbox.output import CappedOutput
from knowwork_server.sandbox.paths import check_name, resolve, resolve_writable
from knowwork_server.tools.registry import ToolContext

from .fake_docker import FakeApi, FakeDocker, FakeResponse, api_error
from .helpers import SID, WS, settings

pytestmark = pytest.mark.anyio
CTX = ToolContext(workspace_id=WS, session_id=SID, user_id="u1")
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
DSH_KEYWORDS = {"type", "properties", "required", "additionalProperties", "items", "enum", "const", "description"}


def _keywords(schema):
    found = set(schema)
    for value in schema.get("properties", {}).values():
        found |= _keywords(value)
    return found


# -- 配置 --------------------------------------------------------------------


def test_defaults():
    s = SandboxSettings()
    assert (s.docker_enabled, s.cpus, s.memory_bytes, s.pids_limit) == (False, 2.0, 2 * 1024**3, 512)
    assert (s.idle_ttl_seconds, s.network_mode, s.exec_user, s.runtime) == (1800, "bridge", "root", "")


def test_enabled_requires_image_and_timeouts_are_consistent():
    with pytest.raises(ValueError):
        SandboxSettings(docker_enabled=True, image=" ")
    with pytest.raises(ValueError):
        SandboxSettings(exec_default_timeout_seconds=100, exec_max_timeout_seconds=10)


@pytest.mark.parametrize("mode", ["host", "container:abc", "knowwork_backend", ""])
def test_network_mode_only_allows_bridge_or_none(mode):
    with pytest.raises(ValueError):
        SandboxSettings(network_mode=mode)


def test_no_internet_forces_none_and_changes_posture():
    assert SandboxSettings(network_mode="BRIDGE").effective_network_mode == "bridge"
    assert SandboxSettings(allow_internet=False).effective_network_mode == "none"
    assert ContainerPosture.of(settings()).digest != ContainerPosture.of(settings(allow_internet=False)).digest


@pytest.mark.parametrize("user", ["nobody", "1000:", "-1", "root:root"])
def test_exec_user_must_be_root_or_numeric(user):
    with pytest.raises(ValueError):
        SandboxSettings(exec_user=user)


@pytest.mark.parametrize("host", ["", "unix:///var/run/docker.sock", "tcp://10.0.0.5:2376", "https://d:2376"])
def test_docker_host_accepts_local_and_remote(host):
    assert validate_docker_host(host) == host


@pytest.mark.parametrize("host", ["docker.sock", "unix://relative.sock", "ssh://u@h", "tcp://", "tcp://h:1/x"])
def test_docker_host_rejects_bad_values(host):
    with pytest.raises(ValueError):
        validate_docker_host(host)


def test_remote_daemon_requires_tls_directory(tmp_path):
    with pytest.raises(ValueError, match="TLS"):
        SandboxSettings(docker_host="tcp://10.0.0.5:2376")
    with pytest.raises(ValueError, match="cert.pem"):
        tls_config(str(tmp_path))
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (tmp_path / name).write_text("x")
    config = tls_config(str(tmp_path))
    assert config.verify is True and config.cert == (str(tmp_path / "cert.pem"), str(tmp_path / "key.pem"))
    assert SandboxSettings(docker_host="tcp://10.0.0.5:2376", docker_tls_cert_path=str(tmp_path))


def test_client_factory_uses_env_detection_or_explicit_host(monkeypatch, tmp_path):
    seen = {}

    class Recorder:
        def __init__(self, **kwargs):
            seen["explicit"] = kwargs

        @classmethod
        def from_env(cls, **kwargs):
            seen["env"] = kwargs
            return "from-env"

    monkeypatch.setattr("knowwork_server.sandbox.docker_host.docker.DockerClient", Recorder)
    assert client_factory("", "", timeout_seconds=30)() == "from-env" and seen["env"]["timeout"] == 30
    client_factory("unix:///var/run/docker.sock", "", timeout_seconds=12.4)()
    assert seen["explicit"]["base_url"] == "unix:///var/run/docker.sock" and seen["explicit"]["tls"] is False
    for name in ("ca.pem", "cert.pem", "key.pem"):
        (tmp_path / name).write_text("x")
    client_factory("tcp://10.0.0.5:2376", str(tmp_path), timeout_seconds=5)()
    assert seen["explicit"]["tls"].verify is True and seen["explicit"]["version"] == "auto"


def test_build_sandbox_respects_switch():
    assert build_sandbox(SandboxSettings(docker_enabled=False)) is None
    assert build_sandbox(settings(), factory=FakeDocker().factory) is not None


# -- 工具 --------------------------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("a.txt", "/workspace/a.txt"), ("/workspace", "/workspace"),
                                          ("/workspace/x/../y", "/workspace/y"), ("./out/", "/workspace/out"),
                                          ("//workspace//a", "/workspace/a")])
def test_resolve_accepts_workspace_paths(raw, expected):
    assert resolve(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "/etc/passwd", "../etc", "/workspace/../etc", "/workspacex",
                                 "a\x00b", "x" * 5000, scripts.ACTIVITY_MARKER])
def test_resolve_rejects_escapes(raw):
    with pytest.raises(SandboxError):
        resolve(raw)


@pytest.mark.parametrize("raw", ["/workspace/input", "input/a", "/workspace"])
def test_resolve_writable_rejects_attachments_and_dirs(raw):
    with pytest.raises(SandboxError):
        resolve_writable(raw)


def test_check_name_and_owner():
    assert check_name("报告 v2.docx") == "报告 v2.docx"
    for bad in ("", "..", "a/b", "a\x7f", 3):
        with pytest.raises(SandboxError):
            check_name(bad)  # type: ignore[arg-type]
    assert owner_of("root") == (0, 0) and owner_of("1000") == (1000, 1000) and owner_of("1:2") == (1, 2)


def test_capped_output_marks_truncation():
    output = CappedOutput(limit=4)
    for stream, payload in [(1, b"hello"), (2, b"oops"), (1, b"!")]:
        output(stream, payload)
    assert (bytes(output.stdout), bytes(output.stderr), output.truncated) == (b"hell", b"oops", True)


# -- docker-py 封装 -------------------------------------------------------------


@pytest.mark.parametrize("error,kind,message", [
    (NotFound("x"), DockerNotFound, "操作失败"),
    (api_error(409), DockerConflict, "操作失败"),
    (api_error(500), SandboxError, "暂时不可用"),
    (api_error(400), SandboxError, "操作失败"),
    (requests.ConnectionError("refused"), SandboxError, "暂时不可用"),
    (OSError("broken pipe"), SandboxError, "暂时不可用"),
])
def test_translate_classifies_docker_errors(error, kind, message):
    translated = translate("op", error)
    assert type(translated) is kind and message in str(translated)
    assert "x" not in str(translated) and "refused" not in str(translated)


def test_translate_keeps_sandbox_errors_and_api_error_without_response():
    original = SandboxError("已有说明")
    assert translate("op", original) is original
    assert "操作失败" in str(translate("op", APIError("odd", response=FakeResponse(418))))


async def test_client_construction_failure_is_unavailable_and_retried():
    attempts = []

    def factory():
        attempts.append(1)
        if len(attempts) == 1:
            raise requests.ConnectionError("daemon down")
        return FakeDocker().factory()

    engine = DockerEngine(factory)
    with pytest.raises(SandboxError, match="暂时不可用"):
        await engine.inspect_container("x")
    assert await engine.inspect_container("x") is None
    await engine.aclose()


async def test_exec_after_close_is_unavailable():
    engine = DockerEngine(FakeDocker().factory)
    await engine.aclose()
    with pytest.raises(SandboxError, match="暂时不可用"):
        await engine.exec_run("e", ExecStream(), lambda _s, _p: None)


async def test_put_archive_reporting_false_is_a_failure(monkeypatch):
    fake = FakeDocker()
    container = fake.add_container("c", {})
    engine = DockerEngine(fake.factory)
    monkeypatch.setattr(FakeApi, "put_archive", lambda self, *args: False)
    with pytest.raises(SandboxError):
        await engine.put_archive(container["Id"], "/", b"")
    await engine.aclose()


def test_exec_stream_closed_before_attach_releases_socket():
    import socket

    reader, writer = socket.socketpair()
    stream = ExecStream()
    stream.close()
    assert stream.attach(reader) is False
    assert reader.fileno() == -1
    writer.close()
    other = ExecStream()
    a, b = socket.socketpair()
    assert other.attach(a)
    done = threading.Event()

    def read():
        a.recv(1)
        done.set()

    thread = threading.Thread(target=read)
    thread.start()
    other.close()  # 只 shutdown，读线程被唤醒
    thread.join(2)
    assert done.is_set() and a.fileno() != -1
    other.release()
    assert a.fileno() == -1
    b.close()