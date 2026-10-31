"""空闲清扫与孤儿回收：按活动标记判空闲、删除不暂停、TTL 取容器 label、伪造的未来时间不算、
在用的不删、后台触发且限频；孤儿按会话是否还在判定，刚建的不动、拿不准的不删。"""

import asyncio
from datetime import UTC, datetime

import pytest

from knowwork_server.sandbox.containers import LABEL_IDLE_TTL, LABEL_POSTURE, SandboxKey
from knowwork_server.sandbox.sweeper import _timestamp

from .fake_docker import api_error
from .helpers import SID, WS

pytestmark = pytest.mark.anyio
NOW = 1_800_000_000.0
KEY = SandboxKey.of(WS, SID)


def iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%S.%f") + "123Z"


def owned(fake, key=KEY, *, ttl="1800", created=NOW - 10_000, status="running", marker=None):
    container = fake.add_container(key.name, {**key.labels(), LABEL_IDLE_TTL: ttl}, status=status, created=created)
    if marker is not None:
        fake.markers[container["Id"]] = iso(marker)
    return container


def clocked(module, now=NOW):
    module.sweeper._clock = lambda: now
    return module.sweeper


def test_timestamps_parse_docker_nanoseconds_and_zero_value():
    assert _timestamp("2026-10-19T09:46:36.22294901Z") == pytest.approx(1792403196.222949)
    assert _timestamp("0001-01-01T00:00:00Z") is None
    assert _timestamp("garbage") is None and _timestamp(None) is None


async def test_idle_containers_are_deleted_not_paused(make):
    fake, module = make()
    idle = owned(fake, marker=NOW - 2000)
    fresh = owned(fake, SandboxKey.of(WS, "conv-2"), marker=NOW - 100)
    assert await clocked(module).sweep() == 1
    assert idle["Name"] not in fake.containers and fresh["Name"] in fake.containers
    assert "stop" not in fake.calls and "pause" not in fake.calls


async def test_missing_marker_falls_back_to_creation_time(make):
    fake, module = make()
    old = owned(fake, created=NOW - 5000)
    new = owned(fake, SandboxKey.of(WS, "conv-2"), created=NOW - 10)
    assert await clocked(module).sweep() == 1
    assert old["Name"] not in fake.containers and new["Name"] in fake.containers


async def test_marker_in_the_future_is_not_trusted(make):
    fake, module = make()
    owned(fake, marker=NOW + 86_400, created=NOW - 5000)  # 脚本 touch -d 到未来
    assert await clocked(module).sweep() == 1


async def test_ttl_comes_from_the_container_label(make):
    fake, module = make(idle_ttl_seconds=100_000)
    owned(fake, ttl="60", marker=NOW - 120)
    owned(fake, SandboxKey.of(WS, "conv-2"), ttl="bad", marker=NOW - 120)  # 读不出就用当前配置
    owned(fake, SandboxKey.of(WS, "conv-3"), ttl="0", marker=NOW - 10**6)  # 0 表示不清扫
    assert await clocked(module).sweep() == 1


async def test_busy_sandbox_is_not_deleted(make):
    fake, module = make()
    owned(fake, marker=NOW - 5000)
    async with module.containers.using(KEY):
        assert await clocked(module).sweep() == 0
    assert KEY.name in fake.containers


async def test_unparseable_managed_container_is_swept_and_unmanaged_is_ignored(make):
    fake, module = make()
    fake.add_container("kw-bad", {"io.knowwork.component": "sandbox", "io.knowwork.workspace": "bad id"},
                       created=NOW - 5000)
    fake.add_container("someone-else", {"owner": "x"}, created=NOW - 5000)
    assert await clocked(module).sweep() == 1
    assert set(fake.containers) == {"someone-else"}


async def test_one_failed_delete_does_not_stop_the_sweep(make):
    fake, module = make()
    owned(fake, marker=NOW - 5000)
    owned(fake, SandboxKey.of(WS, "conv-2"), marker=NOW - 5000)
    fake.fail["remove"] = api_error(500)
    assert await clocked(module).sweep() == 1


async def test_stat_failure_counts_as_not_idle(make):
    fake, module = make()
    owned(fake, marker=NOW - 5000)
    fake.fail["get_archive"] = api_error(500)
    assert await clocked(module).sweep() == 0


async def test_use_triggers_background_sweep_at_most_once_per_interval(make):
    fake, module = make()
    owned(fake, SandboxKey.of(WS, "idle"), marker=NOW - 5000)
    clocked(module)
    digest = module.containers._posture.digest
    fake.add_container(KEY.name, {**KEY.labels(), LABEL_POSTURE: digest})
    await module.sandbox.exec(WS, SID, ["true"], timeout_seconds=1, max_output_bytes=10)
    await module.sandbox.exec(WS, SID, ["true"], timeout_seconds=1, max_output_bytes=10)
    for _ in range(50):
        await asyncio.sleep(0.01)
        if SandboxKey.of(WS, "idle").name not in fake.containers:
            break
    assert SandboxKey.of(WS, "idle").name not in fake.containers
    assert fake.calls.count("list") == 1


async def test_background_sweep_failure_is_swallowed(make):
    fake, module = make()
    fake.fail["list"] = api_error(500)
    module.start()
    await asyncio.sleep(0.05)
    assert not module.sweeper._tasks


async def test_reap_orphans_deletes_only_sessions_that_are_gone(make):
    fake, module = make(orphan_grace_seconds=600)
    gone = owned(fake, SandboxKey.of(WS, "gone"), created=0)
    live = owned(fake, SandboxKey.of(WS, "live"), created=0)
    young = owned(fake, SandboxKey.of(WS, "young"), created=10**12)
    broken = owned(fake, SandboxKey.of(WS, "broken"), created=0)
    bad = fake.add_container("kw-bad", {"io.knowwork.component": "sandbox"}, created=0)

    async def is_live(workspace_id, session_id):
        if session_id == "broken":
            raise RuntimeError("db down")
        return session_id == "live"

    assert await module.reap_orphans(is_live) == 2
    remaining = set(fake.containers)
    assert gone["Name"] not in remaining and bad["Name"] not in remaining
    assert {live["Name"], young["Name"], broken["Name"]} <= remaining


async def test_reap_orphans_skips_busy_and_survives_delete_errors(make):
    fake, module = make(orphan_grace_seconds=0)
    owned(fake, created=0)
    other = owned(fake, SandboxKey.of(WS, "other"), created=0)

    async def gone(workspace_id, session_id):
        return False

    fake.add_container("x", {}, created=0)
    module.containers._busy[KEY] = 1
    fake.fail["remove"] = api_error(500)
    assert await module.reap_orphans(gone) == 0
    assert other["Name"] in fake.containers and KEY.name in fake.containers

