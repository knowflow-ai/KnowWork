"""宿主执行器回调的沙箱内部路由，只挂在内部端口。

宿主的官方 bash、文件工具与 present 跑在 DSH 里，它们经宿主的远程 `ctx.subprocess` 与 `ctx.fs` 落到这里，
再进本会话的容器（执行只在业务服务调度的容器里，宿主不碰本机）：
- `POST /internal/extensions/sandbox/exec`：在容器里执行一条 argv；
- `POST /internal/extensions/sandbox/stat`：路径元数据（follow 决定是否跟随最后一级符号链接）；
- `POST /internal/extensions/sandbox/read`：按字节区间读；
- `POST /internal/extensions/sandbox/list`：列目录的直接子项；
- `POST /internal/extensions/sandbox/write`：整文件写入；
- `POST /internal/extensions/sandbox/ssh-identity`：流式执行的登录账号与本会话容器的主机公钥（打开流式执行才有）；
- `WS /internal/extensions/sandbox/ssh`：流式执行的字节管道，宿主的 ssh 客户端经它连进容器里的 `sshd -i`
  （令牌放 Authorization，本次的临时公钥放 X-KnowWork-SSH-Key，见 ssh_pipe.py）。

鉴权沿用本会话的工具令牌（受众 tools），另按当前授权再判一次是不是空间成员，权限收回立即生效。
exec 带上宿主报的「本会话加载过的技能」（skills），这里按此刻的会话清单取它们的环境变量加进命令（skill_env，技能那边判），
输出交回之前把这些值换成 ******：值只在业务服务与容器里，宿主与模型都看不到原文。
路径只认 `/sessions/<令牌里的会话>` 下面的，换成容器里的 /workspace 再走沙箱自己的校验。
错误统一为 `{"error": {"code", "message"}}`，message 可以直接给模型看。
"""

import asyncio
import base64
import binascii
import contextlib
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol

from fastapi import APIRouter, Header, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..tools.service import ToolAccessError
from ..tools.tokens import ToolTokenClaims
from .contract import SandboxError, SandboxTooLarge
from .docker_sandbox import DockerSessionSandbox, PathStat, SandboxFileError
from .output import mask
from .paths import InvalidPath, from_session_path
from .ssh_pipe import InvalidKey, SshPipes, SshUnavailable

log = logging.getLogger(__name__)

PREFIX = "/internal/extensions/sandbox"
MAX_READ_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
DISCONNECT_POLL_SECONDS = 0.5


class TokenVerifier(Protocol):
    def verify(self, token: str) -> ToolTokenClaims: ...


MemberCheck = Callable[[str, str], bool]
# (空间, 用户, 本会话加载过的技能名) → 要加进命令的环境变量
SkillEnv = Callable[[str, str, list[str]], Awaitable[dict[str, str]]]


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class _ExecBody(_Body):
    argv: list[str] = Field(min_length=1, max_length=4096)
    cwd: str = Field(min_length=1, max_length=4096)
    timeout_ms: int = Field(alias="timeoutMs", ge=1, le=3_600_000)
    max_output_bytes: int = Field(alias="maxOutputBytes", ge=1, le=MAX_OUTPUT_BYTES)
    env: dict[str, str] = Field(default_factory=dict, max_length=256)
    skills: list[Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)]] = Field(
        default_factory=list, max_length=64)


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
    if isinstance(error, SshUnavailable):
        return _error(409, "ssh_unavailable", str(error))
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


class _WebSocketPeer:
    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def receive(self) -> bytes | None:
        while True:
            try:
                message = await self._ws.receive()
            except (WebSocketDisconnect, RuntimeError):
                return None
            if message["type"] == "websocket.disconnect":
                return None
            if message.get("bytes"):
                return message["bytes"]

    async def send(self, data: bytes) -> None:
        try:
            await self._ws.send_bytes(data)
        except (WebSocketDisconnect, RuntimeError) as error:
            raise ConnectionError("宿主已断开") from error


# 关闭码：握手阶段被拒用 4xxx（宿主据此区分「没有权限」与「部署不支持」），之后出错用 1011
WS_UNAUTHORIZED, WS_FORBIDDEN, WS_UNAVAILABLE, WS_INVALID = 4401, 4403, 4409, 4400


def build_internal_router(sandbox: DockerSessionSandbox, tokens: TokenVerifier, is_member: MemberCheck, *,
                          max_timeout_seconds: float, skill_env: SkillEnv | None = None,
                          ssh: SshPipes | None = None) -> APIRouter:
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
        secrets = await skill_env(claims.workspace_id, claims.user_id, body.skills) \
            if skill_env is not None and body.skills else {}
        result = await _until_disconnected(request, sandbox.exec(
            claims.workspace_id, claims.session_id, body.argv, timeout_seconds=timeout,
            max_output_bytes=body.max_output_bytes, workdir=container_path(claims, body.cwd),
            env={**body.env, **secrets}))
        hidden = list(secrets.values())
        return {"exitCode": result.exit_code, "stdout": mask(result.stdout, hidden),
                "stderr": mask(result.stderr, hidden), "timedOut": result.timed_out, "truncated": result.truncated}

    async def do_stat(claims: ToolTokenClaims, body: _StatBody, _request: Request) -> dict[str, Any]:
        stat = await sandbox.stat_path(claims.workspace_id, claims.session_id, container_path(claims, body.path),
                                       follow=body.follow)
        return {"stat": _stat(stat)}

    async def do_read(claims: ToolTokenClaims, body: _ReadBody, _request: Request) -> dict[str, Any]:
        if body.length == 0:
            return {"data": ""}
        chunk = await sandbox.read_file(claims.workspace_id, claims.session_id, container_path(claims, body.path),
                                        offset=body.offset, limit=body.length)
        return {"size": chunk.size, "data": base64.b64encode(chunk.data).decode("ascii"),
                "stat": {"kind": "f", "size": chunk.size, "version": chunk.version}}

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

    async def do_ssh_identity(claims: ToolTokenClaims, _body: _Body, _request: Request) -> dict[str, Any]:
        if ssh is None:
            raise SshUnavailable("这个部署没有打开流式执行")
        identity = await ssh.identity(claims.workspace_id, claims.session_id)
        return {"user": identity.user, "hostKey": identity.host_key}

    routes: dict[str, tuple[type[_Body], Callable[[ToolTokenClaims, Any, Request], Awaitable[dict[str, Any]]]]] = {
        "exec": (_ExecBody, do_exec), "stat": (_StatBody, do_stat), "read": (_ReadBody, do_read),
        "list": (_PathBody, do_list), "write": (_WriteBody, do_write), "ssh-identity": (_Body, do_ssh_identity),
    }

    for operation, (model, run) in routes.items():
        def endpoint(model: type[_Body] = model, run: Callable[..., Awaitable[dict[str, Any]]] = run):
            async def handle(request: Request, authorization: str | None = Header(default=None)) -> Any:
                return await guarded(authorization, request, model, run)
            return handle

        router.add_api_route(f"{PREFIX}/{operation}", endpoint(), methods=["POST"])

    async def ssh_pipe(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization", "")
        public_key = websocket.headers.get("x-knowwork-ssh-key", "")
        try:
            if not authorization.startswith("Bearer "):
                raise ToolAccessError(401, "unauthorized", "令牌无效")
            claims = tokens.verify(authorization[7:])
        except ToolAccessError:
            await websocket.close(code=WS_UNAUTHORIZED)
            return
        if not is_member(claims.user_id, claims.workspace_id):
            await websocket.close(code=WS_FORBIDDEN)
            return
        if ssh is None:
            await websocket.close(code=WS_UNAVAILABLE)
            return
        await websocket.accept()
        try:
            await ssh.relay(claims.workspace_id, claims.session_id, public_key, _WebSocketPeer(websocket),
                            still_allowed=lambda: is_member(claims.user_id, claims.workspace_id))
        except SshUnavailable:
            code = WS_UNAVAILABLE
        except InvalidKey:
            code = WS_INVALID
        except SandboxError as error:
            log.warning("流式执行管道没有建起来：%s", error)
            code = 1011
        else:
            code = 1000
        with contextlib.suppress(Exception):
            await websocket.close(code=code)

    router.add_api_websocket_route(f"{PREFIX}/ssh", ssh_pipe)
    return router
