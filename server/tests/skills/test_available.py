"""我能用的技能：本会话清单加上宿主自带的；同名时会话里的技能盖过自带的；宿主连不上时不列自带的。"""

from dataclasses import dataclass

import pytest

from knowwork_server.skills.available import SkillAvailability
from knowwork_server.skills.bundled import BundledSkills

from .conftest import actor, skill_zip

pytestmark = pytest.mark.anyio


@dataclass(frozen=True)
class Bundled:
    name: str
    description: str
    user_invocable: bool = True


class Host:
    def __init__(self, *skills: Bundled) -> None:
        self.skills = skills
        self.calls = 0
        self.down = False

    async def __call__(self):
        self.calls += 1
        if self.down:
            raise ConnectionError("host down")
        return self.skills


async def test_lists_session_skills_then_bundled_without_name_clashes(env):
    host = Host(Bundled("office-docx", "Word 文档"), Bundled("summary", "自带的总结"))
    available = SkillAvailability(env.skills.delivery.catalog, BundledSkills(host))
    svc = env.skills.service
    shared = await svc.install(actor("bob"), "workspace", skill_zip("summary", version="2.0.0"))
    await svc.publish(actor("bob"), shared.id)
    await env.access.grant("alice", "ws1", "user:dave", f"skill:{shared.id}", "use")
    await svc.install(actor("dave"), "personal", skill_zip("my-notes"))
    found = await available.for_user("ws1", "dave")
    assert [(s.name, s.source, s.version) for s in found] == [
        ("office-docx", "bundled", ""), ("summary", "workspace", "2.0.0"), ("my-notes", "personal", found[2].version)]


async def test_bundled_list_is_cached_and_survives_host_outage():
    now = [0.0]
    host = Host(Bundled("office-xlsx", "Excel", user_invocable=False))
    bundled = BundledSkills(host, ttl_seconds=60, clock=lambda: now[0])
    assert [(s.name, s.user_invocable) for s in await bundled.list()] == [("office-xlsx", False)]
    await bundled.list()
    assert host.calls == 1
    now[0] = 61
    host.down = True
    assert [s.name for s in await bundled.list()] == ["office-xlsx"], "连不上时用上次的"
    assert host.calls == 2
    await bundled.list()
    assert host.calls == 2, "连不上之后隔一会儿再问，不是每次都等超时"
    now[0] = 61 + 31
    host.down = False
    await bundled.list()
    assert host.calls == 3
    assert await BundledSkills(None).list() == ()
