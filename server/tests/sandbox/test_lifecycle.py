"""容器生命周期：开关、姿态参数、按名字与 label 找回、停了拉起、配置变更重建、不收养、销毁、新建钩子。"""

import asyncio

import pytest

from knowwork_server.sandbox import SandboxSettings, build_sandbox, sandbox_eraser
from knowwork_server.sandbox.config import ContainerPosture
from knowwork_server.sandbox.containers import LABEL_IDLE_TTL, LABEL_POSTURE, SandboxKey
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.scripts import ACTIVITY_MARKER

from .fake_docker import FakeDocker, api_error
from .helpers import SID, WS, settings

pytestmark = pytest.mark.anyio
KEY = SandboxKey.of(WS, SID)


async def _touch(module):
    return await module.sandbox.exec(WS, SID, ["true"], timeout_seconds=5, max_output_bytes=100)


def _digest(**overrides) -> str:
    return ContainerPosture.of(settings(**overrides)).digest


def test_docker_backend_is_off_by_default():
    assert SandboxSettings().docker_enabled is False
    assert build_sandbox(SandboxSettings(image="kw-sandbox:1")) is None


async def test_first_use_creates_container_with_the_sandbox_posture(make):
    fake, module = make()
    await _touch(module)
    container = fake.containers[KEY.name]
    options = container["Options"]
    assert options["entrypoint"][:2] == ["/bin/sh", "-c"] and "exec sleep infinity" in options["entrypoint"][2]
    assert ACTIVITY_MARKER in options["entrypoint"][2] and options["command"] == []
    assert options["user"] == "0" and options["working_dir"] == "/workspace" and options["init"] is True
    assert options["cap_drop"] == ["ALL"]
    assert options["cap_add"] == ["CHOWN", "DAC_OVERRIDE", "FOWNER", "FSETID", "SETGID", "SETUID", "KILL"]
    assert options["security_opt"] == ["no-new-privileges"]
    assert options["mem_limit"] == options["memswap_limit"] == 2 * 1024**3
    assert options["nano_cpus"] == 2_000_000_000 and options["pids_limit"] == 512
    assert options["network_mode"] == "bridge" and "runtime" not in options
    assert "volumes" not in options and "mounts" not in options  # 工作区不挂卷
    labels = container["Labels"]
    assert labels["io.knowwork.workspace"] == WS and labels["io.knowwork.session"] == SID
    assert labels["io.knowwork.component"] == "sandbox" and labels[LABEL_IDLE_TTL] == "1800"
    assert container["Status"] == "running"
    await _touch(module)
    assert fake.calls.count("create") == 1


async def test_limits_network_and_runtime_are_configurable(make):
    fake, module = make(memory_bytes=512 * 1024**2, cpus=0.5, pids_limit=64, allow_internet=False, runtime="runsc",
                        idle_ttl_seconds=600)
    await _touch(module)
    container = fake.containers[KEY.name]
    options = container["Options"]
    assert (options["mem_limit"], options["memswap_limit"], options["nano_cpus"], options["pids_limit"]) == (
        512 * 1024**2, 512 * 1024**2, 500_000_000, 64)
    assert options["network_mode"] == "none" and options["runtime"] == "runsc"
    assert container["Labels"][LABEL_IDLE_TTL] == "600"


async def test_client_is_built_lazily_once(make):
    fake, module = make()
    assert fake.built == 0
    await _touch(module)
    await _touch(module)
    assert fake.built == 1


@pytest.mark.parametrize("status,call", [("exited", "start"), ("created", "start"), ("paused", "unpause"),
                                         ("running", None)])
async def test_existing_container_is_recovered_and_resumed(make, status, call):
    fake, module = make()
    existing = fake.add_container(KEY.name, {**KEY.labels(), LABEL_POSTURE: _digest()}, status=status)
    await _touch(module)
    assert existing["Status"] == "running" and "create" not in fake.calls
    if call:
        assert call in fake.calls


@pytest.mark.parametrize("labels_extra,status", [({LABEL_POSTURE: "stale"}, "running"), ({}, "dead")])
async def test_stale_or_dead_container_is_replaced_and_hook_runs(make, labels_extra, status):
    fake, module = make()
    old = fake.add_container(KEY.name, {**KEY.labels(), LABEL_POSTURE: _digest(), **labels_extra}, status=status)
    created = []

    async def hook(workspace_id, session_id):
        created.append((workspace_id, session_id))

    module.on_created(hook)
    await _touch(module)
    assert fake.containers[KEY.name]["Id"] != old["Id"]
    assert created == [(WS, SID)]
    await _touch(module)
    assert created == [(WS, SID)]  # 只在新建时调用


async def test_created_hook_failure_does_not_fail_the_operation(make):
    _fake, module = make()

    async def broken(workspace_id, session_id):
        raise RuntimeError("restage failed")

    module.on_created(broken)
    assert (await _touch(module)).exit_code == 0


async def test_created_hook_can_use_the_sandbox_without_deadlock(make):
    fake, module = make()

    async def restage(workspace_id, session_id):
        await module.sandbox.put_input(workspace_id, session_id, "a.txt", b"x")

    module.on_created(restage)
    await asyncio.wait_for(_touch(module), 5)
    assert len(fake.archives) == 1


@pytest.mark.parametrize("labels", [{}, {"io.knowwork.component": "sandbox", "io.knowwork.workspace": "other",
                                         "io.knowwork.session": SID}])
async def test_foreign_container_with_our_name_is_not_adopted(make, labels):
    fake, module = make()
    fake.add_container(KEY.name, labels)
    with pytest.raises(SandboxError, match="冲突"):
        await _touch(module)


async def test_missing_image_is_pulled_once(make):
    fake, module = make(FakeDocker(images=set()))
    await _touch(module)
    await _touch(module)
    assert fake.pulls == ["kw-sandbox:1"]


async def test_pull_failure_reports_image_unavailable(make):
    fake, module = make(FakeDocker(images=set()))
    fake.fail["pull"] = api_error(500)
    with pytest.raises(SandboxError, match="镜像不可用"):
        await _touch(module)


async def test_pull_timeout_reports_image_unavailable(make, monkeypatch):
    _fake, module = make(FakeDocker(images=set()), pull_timeout_seconds=0.05)
    original = type(module.engine)._call

    async def slow(self, action, fn):
        if action == "pull":
            await asyncio.sleep(1)
        return await original(self, action, fn)

    monkeypatch.setattr(type(module.engine), "_call", slow)
    with pytest.raises(SandboxError, match="镜像不可用"):
        await _touch(module)


async def test_failed_start_removes_the_half_built_container(make):
    fake, module = make()
    fake.fail["start"] = api_error(500)
    with pytest.raises(SandboxError):
        await _touch(module)
    assert fake.containers == {}


async def test_container_that_exits_right_away_is_removed(make, monkeypatch):
    fake, module = make(FakeDocker(start_status="exited"))
    with pytest.raises(SandboxError, match="启动失败"):
        await _touch(module)
    assert fake.containers == {}


async def test_container_that_never_becomes_ready_times_out(make, monkeypatch):
    monkeypatch.setattr("knowwork_server.sandbox.containers.START_READY_SECONDS", 0.1)
    _fake, module = make(FakeDocker(start_status="restarting"))
    with pytest.raises(SandboxError, match="启动失败"):
        await _touch(module)


async def test_create_conflict_is_reported_without_details(make):
    fake, module = make()
    fake.fail["create"] = api_error(409)
    with pytest.raises(SandboxError, match="准备中") as caught:
        await _touch(module)
    assert KEY.name not in str(caught.value)


@pytest.mark.parametrize("workspace_id,session_id", [("", SID), (WS, "../x"), (WS, "a" * 200), (WS, "a b")])
async def test_invalid_ids_are_rejected(make, workspace_id, session_id):
    _fake, module = make()
    with pytest.raises(SandboxError, match="标识"):
        await module.sandbox.exec(workspace_id, session_id, ["true"], timeout_seconds=1, max_output_bytes=10)


async def test_destroy_removes_every_owned_container_and_is_idempotent(make):
    fake, module = make()
    await _touch(module)
    fake.add_container("kw-sbx-leftover", KEY.labels(), status="exited")
    fake.add_container("unrelated", {"owner": "other"})

    class Conversation:
        id = SID
        workspace_id = WS

    await sandbox_eraser(module.sandbox)(Conversation())
    assert set(fake.containers) == {"unrelated"}
    await module.eraser()(Conversation())


async def test_destroy_failure_propagates(make):
    fake, module = make()
    fake.fail_all = 500
    with pytest.raises(SandboxError, match="暂时不可用"):
        await module.sandbox.destroy(WS, SID)


async def test_start_triggers_one_background_sweep_and_close_is_clean(make):
    fake, module = make()
    module.start()
    await asyncio.sleep(0.05)
    assert "list" in fake.calls
    await module.aclose()
    assert fake.closed
