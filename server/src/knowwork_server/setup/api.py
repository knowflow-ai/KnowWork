"""初始化向导接口：未初始化时可调用一次，完成后永久返回 409。

- GET  /api/setup  → {"initialized": bool}（前端据此决定是否进入向导）
- POST /api/setup  → 建首个系统管理员与默认工作空间
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..identity.errors import EmailTaken, InvalidInput
from ..identity.passwords import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH
from .service import AlreadyInitialized, SetupInconsistent, SetupInProgress, SetupService

log = logging.getLogger(__name__)

ALREADY_INITIALIZED = "系统已完成初始化"
IN_PROGRESS = "初始化正在进行，请稍后刷新"
INCONSISTENT = "初始化状态异常，请联系运维处理"


class SetupBody(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)


def build_router(service: SetupService, *, prefix: str = "/api/setup") -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["setup"])

    @router.get("")
    async def status() -> dict:
        return {"initialized": (await service.status()).initialized}

    @router.post("", status_code=201)
    async def initialize(body: SetupBody) -> dict:
        try:
            result = await service.initialize(email=body.email, name=body.name, password=body.password)
        except AlreadyInitialized as error:
            raise HTTPException(status_code=409, detail=ALREADY_INITIALIZED) from error
        except SetupInProgress as error:
            raise HTTPException(status_code=409, detail=IN_PROGRESS) from error
        except (InvalidInput, EmailTaken) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except SetupInconsistent as error:
            log.error("setup found a default workspace without a completion record")
            raise HTTPException(status_code=503, detail=INCONSISTENT) from error
        return {"admin_user_id": result.admin_user_id, "workspace_id": result.workspace_id}

    return router
