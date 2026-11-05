"""扩展登记的 @ 类型（conversations/mention_kinds.py）：登记时校验、按人列出各组、某一组出错不连累别的。
发消息时的核对与回调在 test_profile_api.py 里经接口测。"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.mention_api import MentionSources, build_router
from knowwork_server.conversations.mention_kinds import MentionKind, MentionKinds, MentionOption

from .fakes import token_actor

pytestmark = pytest.mark.anyio


async def nothing(workspace_id, user_id):
    return []


def test_registration_is_validated():
    kinds = MentionKinds()
    for bad in (MentionKind("Data", "数据源", "", nothing), MentionKind("skill", "技能", "", nothing),
                MentionKind("data_source", "", "", nothing), MentionKind("data_source", "数据源" * 5, "", nothing),
                MentionKind("data_source", "数据源", "", nothing, answer_mode="Bad Mode")):
        with pytest.raises(ValueError):
            kinds.add(bad)
    assert not kinds, "没登记时和没有这个功能一样"
    kinds.add(MentionKind("data_source", "数据源", "问数只查这个数据源", nothing))
    with pytest.raises(ValueError):
        kinds.add(MentionKind("data_source", "数据源", "重复登记", nothing))
    assert kinds


async def test_listing_per_user_skips_a_failing_group():
    async def sources(workspace_id, user_id):
        return [MentionOption("p1", "销售库", "第 3 版"), *([MentionOption("p2", "财务库")] if user_id == "boss" else [])]

    async def broken(workspace_id, user_id):
        raise RuntimeError("lookup failed")

    async def slow(workspace_id, user_id):
        await asyncio.sleep(1)
        return []

    kinds = MentionKinds(timeout=0.05)
    kinds.add(MentionKind("data_source", "数据源", "问数只查这个数据源", sources, answer_mode="analytics"))
    kinds.add(MentionKind("broken", "坏的", "", broken))
    kinds.add(MentionKind("slow", "慢的", "", slow))
    assert await kinds.listing("ws", "u1") == [{
        "kind": "data_source", "label": "数据源", "effect": "问数只查这个数据源", "answer_mode": "analytics",
        "items": [{"id": "p1", "name": "销售库", "description": "第 3 版"}]}]
    assert [i["id"] for i in (await kinds.listing("ws", "boss"))[0]["items"]] == ["p1", "p2"]


async def test_menu_lists_extension_groups_only_when_there_are_some():
    async def empty(*args):
        return []

    kinds = MentionKinds()
    menus = []
    for extensions in (kinds.listing, None):
        app = FastAPI()
        app.include_router(build_router(MentionSources(bases=empty, skills=empty, services=empty, documents=empty,
                                                       extensions=extensions), token_actor("d" * 48)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            menus.append((await http.get("/api/workspaces/default/conversations/mention-options",
                                         headers={"authorization": f"Bearer {'d' * 48}"})).json())
    assert menus == [{"bases": [], "services": [], "skills": []}] * 2, "没登记时和原来一样"
