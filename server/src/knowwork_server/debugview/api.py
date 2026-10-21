"""运行调试视图的接口：只有会话本人能看（同引用出处、产物下载，不是本人或不存在一律 404）。

- GET /api/conversations/{id}/debug/turns                           每一轮的汇总
- GET /api/conversations/{id}/debug/turns/{turn}                    一轮的步骤时间线、子 Agent、技能与审批
- GET /api/conversations/{id}/debug/turns/{turn}/steps/{step}/input 某一步发给模型的输入（按日志还原）
- GET /api/conversations/{id}/debug/turns/{turn}/export             这一轮的原始日志（JSON 下载）
"""

import re
from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from ..conversations.api import Actor, ActorDependency
from ..sessionfiles.lookup import ConversationLookup, owned_conversation
from .service import DebugService

_SNAKE = re.compile(r"_([a-z])")


def camel(value: Any) -> Any:
    """dataclass 转出的字典键改成前端约定的驼峰式（同会话、引用接口）。"""
    if isinstance(value, dict):
        return {_SNAKE.sub(lambda m: m.group(1).upper(), str(k)): camel(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [camel(item) for item in value]
    return value


def build_router(debug: DebugService, conversations: ConversationLookup,
                 current_actor: ActorDependency) -> APIRouter:
    router = APIRouter(prefix="/api/conversations/{conversation_id}/debug")

    @router.get("/turns")
    async def turns(conversation_id: str, who: Actor = Depends(current_actor)):  # noqa: B008
        listing = await debug.turns(await owned_conversation(conversations, conversation_id, who))
        return camel(asdict(listing))

    @router.get("/turns/{turn}")
    async def turn(conversation_id: str, turn: int, who: Actor = Depends(current_actor)):  # noqa: B008
        found = await debug.turn(await owned_conversation(conversations, conversation_id, who), turn)
        if found is None:
            raise HTTPException(status_code=404, detail="这一轮的记录不存在")
        return camel(asdict(found))

    @router.get("/turns/{turn}/steps/{step}/input")
    async def step_input(conversation_id: str, turn: int, step: int,
                         who: Actor = Depends(current_actor)):  # noqa: B008
        found = await debug.step_input(await owned_conversation(conversations, conversation_id, who), turn, step)
        if found is None:
            raise HTTPException(status_code=404, detail="这一步的记录不存在")
        return camel(asdict(found))

    @router.get("/turns/{turn}/export")
    async def export(conversation_id: str, turn: int, who: Actor = Depends(current_actor)):  # noqa: B008
        found = await debug.export(await owned_conversation(conversations, conversation_id, who), turn)
        if found is None:
            raise HTTPException(status_code=404, detail="这一轮的记录不存在")
        name = f"{conversation_id}-turn-{turn}.json"
        return JSONResponse({"conversationId": conversation_id, "turn": turn, "events": found},
                            headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return router
