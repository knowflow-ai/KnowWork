"""扩展登记的 @ 类型（conversations/mention_kinds.py）：登记时校验、按人列出各组、某一组出错不连累别的。
@ 菜单带上对话时按这段对话的范围收窄（专家对话只列专家能查的库、会的技能、能用的连接器，文件也只找这些库里的；扩展的那组
由扩展自己收窄），范围取不到报 503。发消息时的核对与回调在 test_profile_api.py 里经接口测。"""

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.conversations.mention_api import MentionSources, build_router
from knowwork_server.conversations.mention_kinds import MentionKind, MentionKinds, MentionOption
from knowwork_server.conversations.scopes import ConversationScope, ConversationScopes

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


async def test_menu_narrows_to_the_scope_of_the_conversation():
    async def bases(workspace_id, user_id):
        return [{"id": "builtin.kb1", "name": "财务制度"}, {"id": "builtin.kb2", "name": "员工手册"},
                {"id": "other.kb9", "name": "别的来源"}]

    async def skills(workspace_id, user_id):
        return [{"name": "经营周报"}, {"name": "写诗"}]

    async def services(workspace_id, user_id):
        return [{"name": "mail"}, {"name": "github"}]

    async def documents(workspace_id, user_id, q):
        return [{"id": "d1", "base": "builtin.kb2"}, {"id": "d2", "base": "other.kb9"}]

    async def sources(workspace_id, user_id):
        return [MentionOption("p1", "销售库"), MentionOption("p2", "财务库")]

    async def scoped_sources(workspace_id, user_id, conversation_id):
        return [MentionOption("p1", "销售库")]

    async def scope(workspace_id, conversation_id):
        if conversation_id == "c_broken":
            raise RuntimeError("库连不上")
        return ConversationScope(label="经营分析", knowledge_bases=("builtin.*",), skills=("经营周报",),
                                 mcp_servers=("mail",)) if conversation_id == "c_expert" else None

    kinds = MentionKinds()
    kinds.add(MentionKind("data_source", "数据源", "问数只查这个数据源", sources, scoped=scoped_sources))
    scopes = ConversationScopes()
    scopes.add(scope)
    app = FastAPI()
    app.include_router(build_router(MentionSources(bases=bases, skills=skills, services=services, documents=documents,
                                                   extensions=kinds.listing, scopes=scopes), token_actor("d" * 48)))
    base = "/api/workspaces/default/conversations/mention-options"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t",
                                 headers={"authorization": f"Bearer {'d' * 48}"}) as http:
        narrowed = (await http.get(base, params={"conversation_id": "c_expert"})).json()
        plain = (await http.get(base, params={"conversation_id": "c_plain"})).json()
        everything = (await http.get(base)).json()
        docs = (await http.get(f"{base}/documents", params={"conversation_id": "c_expert"})).json()
        broken = await http.get(base, params={"conversation_id": "c_broken"})
    assert [b["id"] for b in narrowed["bases"]] == ["builtin.kb1", "builtin.kb2"], "<知识源>.* 是这个来源下的全部"
    assert ([s["name"] for s in narrowed["skills"]], [s["name"] for s in narrowed["services"]]) == (["经营周报"], ["mail"])
    assert [i["id"] for i in narrowed["extensions"][0]["items"]] == ["p1"], "扩展的那组由扩展自己按范围收窄"
    assert plain == everything and len(everything["bases"]) == 3, "没有范围的对话和不带对话一样"
    assert [d["id"] for d in docs["documents"]] == ["d1"], "文件也只找范围里的库"
    assert broken.status_code == 503

