"""内容安全的接口。

管理员（空间所有者、管理员）：
- GET /api/workspaces/{ws}/content-safety   这个空间的策略
- PUT /api/workspaces/{ws}/content-safety   保存（带打开时的版本，别人先改过回 409 和最新的一份）
- GET /api/workspaces/{ws}/content-safety/sources/{id}/words   下载扩展登记的某份词表（txt），管理员看里面有什么

宿主回调（只挂在内部端口，令牌用这段会话的工具令牌，另按当前授权再判一次是不是空间成员）。模型网关也来这里查：它拿个人
专属宿主的模型令牌（带归属，见 kernel/tokens.py）来，按归属的空间与人查，不带会话：
- GET  /internal/extensions/content-safety/policy   开没开、哪几个方向、流式输出要留几个字、第几版，插件按会话缓存几秒
- POST /internal/extensions/content-safety/check    送一段或几段文字来查；命中回阻断提示，不回命中的词（词表不出业务服务）
登记了原生应用（桌面端）时，同一套接口在公开端口的 `/api/extensions/content-safety` 下再挂一份，给用户电脑上的本机宿主回调
（同工具桥：令牌是本机会话经令牌交换拿到的同一种工具令牌）。检查接口不记审计（每几十个字查一次，记了会刷满审计表；
命中另记 content_safety.blocked），见 audit/actions.py 的 UNAUDITED_ROUTES。不进接口文档（宿主回调用的，同工具桥）。
错误统一为 `{"error": {"code", "message"}}`。
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Path
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from ..auth.principal import Principal
from ..tools.service import ToolAccessError
from ..tools.tokens import ToolTokenClaims
from .policy import MAX_MESSAGE_CHARS, Policy, PolicyInvalid, draft_of
from .service import Caller, Conflict, ContentSafetyService, Forbidden
from .sources import CUSTOM_SOURCE, WordSources

IdPath = Annotated[str, Path(pattern=r"^[A-Za-z0-9_.\-]{1,64}$")]
PREFIX = "/internal/extensions/content-safety"
PUBLIC_PREFIX = "/api/extensions/content-safety"
MAX_SEGMENTS = 256
MAX_CHECK_CHARS = 2_000_000  # 一次最多查这么多字；宿主送整段历史时自己分批


class TokenVerifier(Protocol):
    def verify(self, token: str) -> ToolTokenClaims: ...


MemberCheck = Callable[[str, str], bool]
# 认模型网关转来的令牌：是带归属的模型令牌就返回（空间，人），否则 None
OwnerOf = Callable[[str], tuple[str, str] | None]


class PolicyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=0)
    input_enabled: bool
    output_enabled: bool
    # 勾了哪几种词表：custom（自定义）与扩展登记的来源；不带时只用自定义词表（开源版只有这一种）
    word_sources: list[Annotated[str, Field(max_length=32)]] = Field(default=[CUSTOM_SOURCE], max_length=16)
    words: str = Field(max_length=4 * 1024 * 1024)  # 规范化之前的原文，规范化以后再按 1MB、3 万个词校验
    block_message: str = Field(max_length=MAX_MESSAGE_CHARS * 2)


class CheckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    direction: Literal["input", "output", "tool_args"]
    # 只写进审计（审计按长度截断）；不校验格式：哪个工具名对不上格式，它的每次调用都会因为「查不成」被拦
    tool: str | None = Field(default=None, max_length=256)
    segments: list[str] = Field(max_length=MAX_SEGMENTS)


def policy_json(policy: Policy, sources: WordSources) -> dict[str, Any]:
    """available_sources 是扩展登记的现成词表（配置页列出来让管理员勾），开源版是空的。"""
    return {"version": policy.version, "input_enabled": policy.input_enabled, "output_enabled": policy.output_enabled,
            "word_sources": list(policy.word_sources),
            "available_sources": [{"id": source.id, "label": source.label, "description": source.description,
                                   "word_count": len(source.words())} for source in sources.all()],
            "words": "\n".join(policy.words), "word_count": len(policy.words), "block_message": policy.block_message,
            "updated_by": policy.updated_by,
            "updated_at": policy.updated_at.isoformat() if policy.updated_at is not None else None}


def build_router(service: ContentSafetyService, current_principal: Callable[..., Awaitable[Principal]]) -> APIRouter:
    router = APIRouter(prefix="/api/workspaces/{workspace_id}/content-safety", tags=["content-safety"])
    Who = Annotated[Principal, Depends(current_principal)]
    denied = "只有空间所有者和管理员能配置内容安全"

    @router.get("")
    async def view(workspace_id: IdPath, who: Who) -> dict:
        """内容安全策略
        这个空间的敏感词、检查范围和阻断提示；没保存过时 version 为 0（关闭）。"""
        try:
            return policy_json(await service.view(who.user_id, workspace_id), service.sources)
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error

    @router.put("")
    async def save(workspace_id: IdPath, body: PolicyBody, who: Who) -> Any:
        """保存内容安全策略
        带上打开页面时的 version；别的管理员先改过时回 409，带着最新的一份。5 秒内生效，进行中的对话从下一次调用模型起
        按新规则检查。"""
        try:
            draft = draft_of(input_enabled=body.input_enabled, output_enabled=body.output_enabled, words=body.words,
                             block_message=body.block_message, word_sources=body.word_sources,
                             available=service.sources.ids())
            saved = await service.save(who.user_id, workspace_id, version=body.version, draft=draft)
            return policy_json(saved, service.sources)
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error
        except PolicyInvalid as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Conflict as error:
            return JSONResponse(status_code=409, content={"detail": str(error),
                                                          "latest": policy_json(error.latest, service.sources)})

    @router.get("/sources/{source_id}/words")
    async def source_words(workspace_id: IdPath, source_id: Annotated[str, Path(max_length=32)],
                           who: Who) -> PlainTextResponse:
        """下载现成词表
        扩展登记的某份词表，一行一个词，管理员下载下来看里面有什么。"""
        try:
            words = service.source_words(who.user_id, workspace_id, source_id)
        except Forbidden as error:
            raise HTTPException(status_code=403, detail=denied) from error
        source = service.sources.get(source_id)
        if words is None or source is None:
            raise HTTPException(status_code=404, detail="没有这份词表")
        name = quote(f"{source.label}.txt")
        return PlainTextResponse("\n".join(words) + "\n", headers={
            "Content-Disposition": f"attachment; filename=words.txt; filename*=UTF-8''{name}"})

    return router


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_internal_router(service: ContentSafetyService, tokens: TokenVerifier, is_member: MemberCheck, *,
                          path_prefix: str = PREFIX, owner_of: OwnerOf | None = None) -> APIRouter:
    """owner_of 只在内部端口给：模型网关凭专属宿主的模型令牌来查（不带会话）。"""
    router = APIRouter(prefix=path_prefix, include_in_schema=False)

    def caller_of(authorization: str | None) -> Caller:
        if authorization is None or not authorization.startswith("Bearer "):
            raise ToolAccessError(401, "unauthorized", "令牌无效")
        token = authorization[7:]
        owner = owner_of(token) if owner_of is not None else None
        if owner is not None:
            caller = Caller(workspace_id=owner[0], user_id=owner[1], session_id="")
        else:
            claims = tokens.verify(token)
            caller = Caller(workspace_id=claims.workspace_id, user_id=claims.user_id, session_id=claims.session_id)
        if not is_member(caller.user_id, caller.workspace_id):
            raise ToolAccessError(403, "forbidden", "已经不是这个空间的成员")
        return caller

    @router.get("/policy")
    async def policy(authorization: Annotated[str | None, Header()] = None) -> Any:
        try:
            current = await service.summary(caller_of(authorization).workspace_id)
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        return {"enabled": current.enabled, "input": current.input, "output": current.output,
                "holdback": current.holdback, "version": current.version}

    @router.post("/check")
    async def check(body: CheckBody, authorization: Annotated[str | None, Header()] = None) -> Any:
        if sum(len(segment) for segment in body.segments) > MAX_CHECK_CHARS:
            return _error(413, "too_large", f"一次最多检查 {MAX_CHECK_CHARS} 个字，请分批送")
        try:
            caller = caller_of(authorization)
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        result = await service.check(caller, direction=body.direction, tool=body.tool, segments=body.segments)
        return {"blocked": True, "message": result.message} if result.blocked else {"blocked": False}

    return router
