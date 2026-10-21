"""宿主执行器回调的沙箱内部路由，只挂在内部端口。

宿主的官方 bash、文件工具与 present 跑在 DSH 里，它们经宿主的远程 `ctx.subprocess` 与 `ctx.fs` 落到这里，
再进本会话的容器（技术方案：执行只在业务服务调度的容器里，宿主不碰本机）：
- `POST /internal/extensions/sandbox/exec`：在容器里执行一条 argv；
- `POST /internal/extensions/sandbox/stat`：路径元数据（follow 决定是否跟随最后一级符号链接）；
- `POST /internal/extensions/sandbox/read`：按字节区间读；
- `POST /internal/extensions/sandbox/list`：列目录的直接子项；
- `POST /internal/extensions/sandbox/write`：整文件写入。

鉴权沿用本会话的工具令牌（受众 tools），另按当前授权再判一次是不是空间成员，权限收回立即生效。
路径只认 `/sessions/<令牌里的会话>` 下面的，换成容器里的 /workspace 再走沙箱自己的校验。
错误统一为 `{"error": {"code", "message"}}`，message 可以直接给模型看。
"""

import asyncio
import base64
import binascii
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..tools.service import ToolAccessError
from ..tools.tokens import ToolTokenClaims
from .contract import SandboxError, SandboxTooLarge
from .docker_sandbox import DockerSessionSandbox, PathStat, SandboxFileError
from .paths import InvalidPath, from_session_path

PREFIX = "/internal/extensions/sandbox"
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DISCONNECT_POLL_SECONDS = 0.5


class TokenVerifier(Protocol):
    def verify(self, token: str) -> ToolTokenClaims: ...


MemberCheck = Callable[[str, str], bool]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _ExecBody(_Body):
    argv: list[str] = Field(min_length=1, max_length=4096)
    cwd: str = Field(min_length=1, max_length=4096)
    timeout_ms: int = Field(alias="timeoutMs", ge=1, le=3_600_000)
    max_output_bytes: int = Field(alias="maxOutputBytes", ge=1, le=MAX_OUTPUT_BYTES)
    env: dict[str, str] = Field(default_factory=dict, max_length=256)


class _PathBody(_Body):
    path: str = Field(min_length=1, max_length=4096)


class _StatBody(_PathBody):
    follow: bool = True


class _ReadBody(_PathBody):
    offset: int = Field(ge=0, le=2**53)
    length: int = Field(ge=0, le=MAX_READ_BYTES)


class _WriteBody(_PathBody):
    data: str  # base64


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _sandbox_error(error: SandboxError) -> JSONResponse:
    if isinstance(error, InvalidPath):
        return _error(403, "permission_denied", str(error))
    if isinstance(error, SandboxTooLarge):
        return _error(413, "too_large", str(error))
    if isinstance(error, SandboxFileError):
        return _error(404 if error.code == "not_found" else 400, error.code, str(error))
    return _error(400, "sandbox_error", str(error))


def _stat(stat: PathStat | None) -> dict[str, Any] | None:
    return None if stat is None else {"kind": stat.kind, "size": stat.size, "version": stat.version}


async def _until_disconnected(request: Request, work: Awaitable[Any]) -> Any:
    """宿主中止请求（用户取消、执行器超时）时取消执行：沙箱的取消路径会整组杀掉容器里的进程。
    FastAPI 不会因为客户端断开就取消处理函数，所以这里自己盯着连接。"""
    task = asyncio.ensure_future(work)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=DISCONNECT_POLL_SECONDS)
            if done:
                return task.result()
            if await request.is_disconnected():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
                raise ToolAccessError(499, "cancelled", "调用方已取消")
    finally:
        if not task.done():
            task.cancel()


def build_internal_router(sandbox: DockerSessionSandbox, tokens: TokenVerifier, is_member: MemberCheck, *,
                          max_timeout_seconds: float) -> APIRouter:
    router = APIRouter()

    async def guarded(authorization: str | None, request: Request, model: type[_Body],
                      run: Callable[[ToolTokenClaims, Any, Request], Awaitable[dict[str, Any]]]) -> Any:
        try:
            if authorization is None or not authorization.startswith("Bearer "):
                raise ToolAccessError(401, "unauthorized", "令牌无效")
            claims = tokens.verify(authorization[7:])
            if not is_member(claims.user_id, claims.workspace_id):
                raise ToolAccessError(403, "forbidden", "没有使用沙箱的权限")
            try:
                body = model.model_validate(await request.json())
            except (ValueError, ValidationError) as error:
                raise ToolAccessError(400, "invalid_request", "请求体格式错误") from error
            return await run(claims, body, request)
        except ToolAccessError as error:
            return _error(error.status, error.code, str(error))
        except SandboxError as error:
            return _sandbox_error(error)

    def container_path(claims: ToolTokenClaims, path: str) -> str:
        return from_session_path(path, claims.session_id)

    async def do_exec(claims: ToolTokenClaims, body: _ExecBody, request: Request) -> dict[str, Any]:
        timeout = min(body.timeout_ms / 1000, max_timeout_seconds)
        result = await _until_disconnected(request, sandbox.exec(
            claims.workspace_id, claims.session_id, body.argv, timeout_seconds=timeout,
            max_output_bytes=body.max_output_bytes, workdir=container_path(claims, body.cwd), env=body.env))
        return {"exitCode": result.exit_code, "stdout": result.stdout, "stderr": result.stderr,
                "timedOut": result.timed_out, "truncated": result.truncated}

    async def do_stat(claims: ToolTokenClaims, body: _StatBody, _request: Request) -> dict[str, Any]:
        stat = await sandbox.stat_path(claims.workspace_id, claims.session_id, container_path(claims, body.path),
                                       follow=body.follow)
        return {"stat": _stat(stat)}

    async def do_read(claims: ToolTokenClaims, body: _ReadBody, _request: Request) -> dict[str, Any]:
        if body.length == 0:
            return {"data": ""}
        chunk = await sandbox.read_file(claims.workspace_id, claims.session_id, container_path(claims, body.path),
                                        offset=body.offset, limit=body.length)
        return {"size": chunk.size, "data": base64.b64encode(chunk.data).decode("ascii")}

    async def do_list(claims: ToolTokenClaims, body: _PathBody, _request: Request) -> dict[str, Any]:
        entries = await sandbox.list_dir(claims.workspace_id, claims.session_id, container_path(claims, body.path))
        return {"entries": [{"name": e.name, "kind": e.kind, "size": e.size} for e in entries]}

    async def do_write(claims: ToolTokenClaims, body: _WriteBody, _request: Request) -> dict[str, Any]:
        try:
            data = base64.b64decode(body.data, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ToolAccessError(400, "invalid_request", "写入内容不是合法的 base64") from error
        path = container_path(claims, body.path)
        await sandbox.write_file(claims.workspace_id, claims.session_id, path, data)
        return {"stat": _stat(await sandbox.stat_path(claims.workspace_id, claims.session_id, path, follow=True))}

    routes: dict[str, tuple[type[_Body], Callable[[ToolTokenClaims, Any, Request], Awaitable[dict[str, Any]]]]] = {
        "exec": (_ExecBody, do_exec), "stat": (_StatBody, do_stat), "read": (_ReadBody, do_read),
        "list": (_PathBody, do_list), "write": (_WriteBody, do_write),
    }

    for operation, (model, run) in routes.items():
        def endpoint(model: type[_Body] = model, run: Callable[..., Awaitable[dict[str, Any]]] = run):
            async def handle(request: Request, authorization: str | None = Header(default=None)) -> Any:
                return await guarded(authorization, request, model, run)
            return handle

        router.add_api_route(f"{PREFIX}/{operation}", endpoint(), methods=["POST"])

    return router
