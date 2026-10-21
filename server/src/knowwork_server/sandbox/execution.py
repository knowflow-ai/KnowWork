"""在容器里跑一条命令：输出上限、超时整组杀进程、调用方取消时同样杀掉。

Docker 的一个事实决定了这里的写法（WeKnora 在 docker/poc 里验证过）：客户端断开 exec 连接**不会**停掉容器里的
进程。所以超时有两道：
- 本进程到点后另起一个 exec，按包装脚本记下的进程组号整组 SIGKILL（比 WeKnora 的容器内 timeout(1) 更彻底：
  timeout 只杀直接子进程，后台子进程会留下）；
- 包装脚本自带的容器内看门狗在「超时 + 宽限」后同样整组 SIGKILL，业务服务中途退出时也停得下来。
"""

import asyncio
import contextlib
import logging
import math
import secrets
from dataclasses import dataclass

from .contract import SandboxError
from .docker_engine import DockerEngine, ExecStream
from .output import CappedOutput
from .scripts import EXEC_WRAPPER, KILL_GROUP

log = logging.getLogger(__name__)

KILL_TIMEOUT_SECONDS = 10.0
SETTLE_SECONDS = 2.0
DRAIN_SECONDS = 5.0
WATCHDOG_GRACE_SECONDS = 10


@dataclass(frozen=True)
class RawExec:
    exit_code: int
    stdout: bytes
    stderr: bytes
    timed_out: bool
    truncated: bool


@dataclass(frozen=True)
class ExecRequest:
    argv: tuple[str, ...]
    user: str
    workdir: str
    timeout_seconds: float
    max_output_bytes: int


async def _drain(job: asyncio.Future, stream: ExecStream) -> None:
    """等读线程收尾；进程被杀后连接本该自己断开，断不开就关掉它。"""
    done, _ = await asyncio.wait({job}, timeout=DRAIN_SECONDS)
    if not done:
        stream.close()
        await asyncio.wait({job}, timeout=DRAIN_SECONDS)
    if job.done() and not job.cancelled():
        job.exception()  # 已按超时处理，读线程的错误不再上抛


async def _kill_group(engine: DockerEngine, container_id: str, user: str, token: str) -> None:
    stream = ExecStream()
    try:
        exec_id = await engine.exec_create(container_id, ["sh", "-c", KILL_GROUP, "kw", token], user=user,
                                           workdir="/")
        await asyncio.wait_for(engine.exec_run(exec_id, stream, lambda _s, _p: None), KILL_TIMEOUT_SECONDS)
    except (SandboxError, TimeoutError):
        log.warning("沙箱命令超时后未能确认已终止")
    finally:
        stream.close()


async def _abort(engine: DockerEngine, container_id: str, request: ExecRequest, token: str,
                 job: asyncio.Future, stream: ExecStream) -> None:
    await _kill_group(engine, container_id, request.user, token)
    await _drain(job, stream)


async def _exit_code(engine: DockerEngine, exec_id: str, *, settle: bool) -> int:
    """进程刚被杀时 inspect 可能还显示运行中，稍等它落定；拿不到退出码就记 -1。"""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + (SETTLE_SECONDS if settle else 0)
    while True:
        info = await engine.exec_inspect(exec_id)
        if not info.get("Running") and isinstance(info.get("ExitCode"), int):
            return int(info["ExitCode"])
        if loop.time() >= deadline:
            return -1
        await asyncio.sleep(0.1)


def wrap(token: str, request: ExecRequest) -> list[str]:
    watchdog = math.ceil(request.timeout_seconds) + WATCHDOG_GRACE_SECONDS
    return ["sh", "-c", EXEC_WRAPPER, "kw", token, str(watchdog), *request.argv]


async def run(engine: DockerEngine, container_id: str, request: ExecRequest) -> RawExec:
    token = secrets.token_hex(12)
    exec_id = await engine.exec_create(container_id, wrap(token, request), user=request.user,
                                       workdir=request.workdir)
    output = CappedOutput(limit=request.max_output_bytes)
    stream = ExecStream()
    job = asyncio.ensure_future(engine.exec_run(exec_id, stream, output))
    timed_out = False
    try:
        await asyncio.wait_for(asyncio.shield(job), request.timeout_seconds)
    except TimeoutError:
        timed_out = True
        await _abort(engine, container_id, request, token, job, stream)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.shield(_abort(engine, container_id, request, token, job, stream))
        raise
    finally:
        stream.close()
    exit_code = await _exit_code(engine, exec_id, settle=timed_out)
    return RawExec(exit_code=exit_code, stdout=bytes(output.stdout), stderr=bytes(output.stderr),
                   timed_out=timed_out, truncated=output.truncated)
