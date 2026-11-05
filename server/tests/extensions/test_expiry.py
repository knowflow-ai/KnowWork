"""运行中授权到期：到期那一刻重读授权，续上了接着跑，续不上就让进程退出（重启后按过期处理，扩展不再启用）。"""

import asyncio
import dataclasses
import time
from datetime import datetime, timedelta

import pytest

from knowwork_server.extensions import LicenseState, discover
from knowwork_server.extensions.expiry import stop_when_license_ends

from .test_loader import DEMO, found, verifier

pytestmark = pytest.mark.anyio


def licensed(valid_until: datetime | None):
    """启用着 demo 扩展、授权到 valid_until 的扩展集合。"""
    extensions = discover(found(DEMO, verifier("granting_demo")))
    assert [e.name for e in extensions.active] == ["demo"]
    return dataclasses.replace(extensions, license=dataclasses.replace(extensions.license, valid_until=valid_until))


def soon(seconds: float = 0.2) -> datetime:
    return datetime.now() + timedelta(seconds=seconds)


def states(*items: LicenseState):
    """依次返回给定的授权状态，记下被重读了几次。"""
    calls: list[int] = []

    def reverify() -> LicenseState:
        calls.append(1)
        return items[min(len(calls), len(items)) - 1]

    return reverify, calls


EXPIRED = LicenseState(status="expired", message="授权已过期")


async def test_expiry_stops_the_process_when_not_renewed():
    stop = asyncio.Event()
    reverify, calls = states(EXPIRED)
    started = time.monotonic()
    assert await asyncio.wait_for(stop_when_license_ends(licensed(soon()), stop, reverify=reverify), 5) is True
    assert stop.is_set(), "到期续不上要置 stop，让进程退出"
    assert len(calls) == 1 and time.monotonic() - started >= 0.15, "到了到期那一刻才重读授权"


async def test_renewed_license_keeps_running_until_the_new_expiry():
    stop = asyncio.Event()
    renewed = LicenseState(status="valid", message="续期", modules=frozenset({"demo"}), valid_until=soon(0.4))
    reverify, calls = states(renewed, EXPIRED)
    assert await asyncio.wait_for(stop_when_license_ends(licensed(soon()), stop, reverify=reverify), 5) is True
    assert len(calls) == 2, "第一次到期时已续上，到新的到期日才停"


async def test_renewal_that_drops_an_enabled_module_still_stops():
    stop = asyncio.Event()
    narrower = LicenseState(status="valid", message="换了授权", modules=frozenset({"other"}), valid_until=None)
    reverify, _ = states(narrower)
    assert await asyncio.wait_for(stop_when_license_ends(licensed(soon()), stop, reverify=reverify), 5) is True


async def test_renewal_to_a_permanent_license_stops_watching_without_exiting():
    stop = asyncio.Event()
    permanent = LicenseState(status="valid", message="永久", modules=frozenset({"demo"}), valid_until=None)
    reverify, calls = states(permanent)
    assert await asyncio.wait_for(stop_when_license_ends(licensed(soon()), stop, reverify=reverify), 5) is False
    assert not stop.is_set() and len(calls) == 1


@pytest.mark.parametrize("extensions", [
    pytest.param(lambda: licensed(None), id="permanent-license"),
    pytest.param(lambda: discover(found(DEMO)), id="no-extension-enabled"),
])
async def test_nothing_to_watch_returns_at_once(extensions):
    stop = asyncio.Event()
    reverify, calls = states(EXPIRED)
    assert await asyncio.wait_for(stop_when_license_ends(extensions(), stop, reverify=reverify), 1) is False
    assert not stop.is_set() and calls == []


async def test_shutting_down_before_expiry_does_not_reread_the_license():
    stop = asyncio.Event()
    reverify, calls = states(EXPIRED)
    watcher = asyncio.create_task(stop_when_license_ends(licensed(soon(3600)), stop, reverify=reverify))
    await asyncio.sleep(0.05)
    stop.set()
    assert await asyncio.wait_for(watcher, 1) is False
    assert calls == []


async def test_clock_still_short_of_expiry_after_waking_waits_the_rest():
    """醒来时一对时间还没到（中途改过系统时间之类），按剩下的接着等，不提前停。"""
    stop = asyncio.Event()
    expiry = datetime(2026, 12, 18)
    readings = iter([expiry - timedelta(seconds=0.1), expiry - timedelta(seconds=0.1), expiry])
    reverify, calls = states(EXPIRED)
    ended = await asyncio.wait_for(stop_when_license_ends(licensed(expiry), stop, reverify=reverify,
                                                          clock=lambda: next(readings)), 5)
    assert ended is True and len(calls) == 1
