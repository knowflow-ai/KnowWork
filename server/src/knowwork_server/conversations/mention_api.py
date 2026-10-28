"""@ 菜单的候选：本人此刻能 @ 的知识库（带所在目录与标签）、技能，以及按名字找文件。

和发消息时的限定同一个来源：知识库走检索服务的范围收窄（授权、开放范围），技能走本会话的技能目录，
所以菜单里出现的就是这一轮真能用的。数据由 app.py 装配时注入（knowledge/mentionables.py、技能目录），
会话层不直接引用知识与技能模块。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from .api import Actor

# （工作空间，用户）→ 候选；documents 另带检索词
Listing = Callable[[str, str], Awaitable[list[dict]]]
Finding = Callable[[str, str, str], Awaitable[list[dict]]]


@dataclass(frozen=True)
class MentionSources:
    bases: Listing  # id、name、description、source、path、tags、pinnable
    skills: Listing  # name、description
    documents: Finding  # id（doc_ref）、title、base、base_name


def build_router(sources: MentionSources, current_actor: Callable[..., Awaitable[Actor]]) -> APIRouter:
    # 要排在会话路由前面挂：不然 GET …/conversations/{conversation_id} 先把 mention-options 当成会话 id
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/conversations/mention-options", tags=["conversations"])
    Who = Annotated[Actor, Depends(current_actor)]

    @router.get("")
    async def options(who: Who) -> dict:
        """@ 菜单可选的知识库、技能"""
        return {"bases": await sources.bases(who.workspace_id, who.user_id),
                "skills": await sources.skills(who.workspace_id, who.user_id)}

    @router.get("/documents")
    async def documents(who: Who, q: Annotated[str, Query(max_length=100)] = "") -> dict:
        """q 为空时列最近更新的文件（刚进「文件」组），有字时按名字找。"""
        return {"documents": await sources.documents(who.workspace_id, who.user_id, q)}

    return router
