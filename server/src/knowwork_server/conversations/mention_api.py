"""@ 菜单的候选：本人此刻能 @ 的知识库（带所在目录与标签）、MCP 服务、技能、扩展登记的类型（mention_kinds.py），
以及按名字找文件。

和发消息时的限定同一个来源：知识库走检索服务的范围收窄（授权、开放范围），技能走本会话的技能目录，
MCP 服务走「设置 → 连接」同一份判断（本人能用、不用连接或已经连上），所以菜单里出现的就是这一轮真能用的。数据由 app.py 装配时注入（knowledge/mentionables.py、技能目录），
会话层不直接引用知识与技能模块。

带上 conversation_id 时按这段对话的范围收窄（scopes.py，例如专家对话只列专家能查的知识库、会的技能、能用的连接器，文件也
只找这些库里的）；扩展的那几组由扩展自己按范围收窄（MentionKind.scoped）。范围只会让候选变少，别人的对话 id 也只是收窄
自己的候选，不用核对对话归谁。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from .api import Actor
from .scopes import ConversationScope, ConversationScopes, ScopeUnavailable

# （工作空间，用户）→ 候选；documents 另带检索词；extensions 另带对话（按它的范围收窄，没有是 None）
Listing = Callable[[str, str], Awaitable[list[dict]]]
Finding = Callable[[str, str, str], Awaitable[list[dict]]]
ExtensionListing = Callable[[str, str, str | None], Awaitable[list[dict]]]
ConversationId = Annotated[str | None, Query(pattern=r"^[A-Za-z0-9_\-]{1,64}$")]


@dataclass(frozen=True)
class MentionSources:
    bases: Listing  # id、name、description、source、path、tags、pinnable
    skills: Listing  # name、description
    services: Listing  # MCP 服务：name（标识）、title、tools（工具数）、status（ready、connected、invalid）
    documents: Finding  # id（doc_ref）、title、base、base_name
    extensions: ExtensionListing | None = None  # 扩展登记的类型各一组：kind、label、effect、answer_mode、items（id、name、description）
    scopes: ConversationScopes | None = None  # 带了对话时按它的范围收窄


def build_router(sources: MentionSources, current_actor: Callable[..., Awaitable[Actor]]) -> APIRouter:
    # 要排在会话路由前面挂：不然 GET …/conversations/{conversation_id} 先把 mention-options 当成会话 id
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations/mention-options", tags=["conversations"])
    Who = Annotated[Actor, Depends(current_actor)]

    async def scope_of(who: Actor, conversation_id: str | None) -> ConversationScope | None:
        if conversation_id is None or not sources.scopes:
            return None
        try:
            return await sources.scopes.of(who.workspace_id, conversation_id)
        except ScopeUnavailable as error:
            raise HTTPException(status_code=503, detail="这段对话的设置暂时取不到，请稍后再试") from error

    @router.get("")
    async def options(who: Who, conversation_id: ConversationId = None) -> dict:
        """@ 菜单可选的知识库、MCP 服务、技能，以及扩展登记的类型；带上对话时按它的范围收窄"""
        scope = await scope_of(who, conversation_id)
        extensions = (await sources.extensions(who.workspace_id, who.user_id, conversation_id if scope else None)
                      if sources.extensions else [])
        bases = await sources.bases(who.workspace_id, who.user_id)
        services = await sources.services(who.workspace_id, who.user_id)
        skills = await sources.skills(who.workspace_id, who.user_id)
        if scope is not None:
            bases = [b for b in bases if scope.allows_base(str(b.get("id", "")))]
            services = [s for s in services if scope.allows_server(str(s.get("name", "")))]
            skills = [s for s in skills if scope.allows_skill(str(s.get("name", "")))]
        return {"bases": bases, "services": services, "skills": skills,
                **({"extensions": extensions} if extensions else {})}

    @router.get("/documents")
    async def documents(who: Who, q: Annotated[str, Query(max_length=100)] = "",
                        conversation_id: ConversationId = None) -> dict:
        """q 为空时列最近更新的文件（刚进「文件」组），有字时按名字找；带上对话时只找它范围里的库"""
        scope = await scope_of(who, conversation_id)
        found = await sources.documents(who.workspace_id, who.user_id, q)
        if scope is not None:
            found = [d for d in found if scope.allows_base(str(d.get("base", "")))]
        return {"documents": found}

    return router
