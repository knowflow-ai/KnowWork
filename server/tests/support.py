"""端到端测试支撑：真实宿主进程、嵌入式 PostgreSQL、OpenAI 兼容的上游桩，业务服务两个端口都用 uvicorn 起。"""

import asyncio
import json
import os
import re
import secrets
import shutil
import socket
import tempfile
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

RUNTIME_HOST = Path(__file__).resolve().parents[2] / "runtime-host"
PINNED_NODE_BIN = Path.home() / ".nvm/versions/node/v24.18.0/bin"  # 与 scripts/dev_stack.py 同一版本


def _node_bin() -> Path:
    """Node 所在目录：环境变量优先；本机有 nvm 装的固定版本就用它；没有（CI 用 setup-node）就取 PATH 里的 node。"""
    configured = os.environ.get("KNOWWORK_TEST_NODE_BIN")
    if configured:
        return Path(configured)
    if (PINNED_NODE_BIN / "node").exists():
        return PINNED_NODE_BIN
    found = shutil.which("node")
    return Path(found).parent if found else PINNED_NODE_BIN


NODE_BIN = _node_bin()


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class UpstreamStub:
    """扮演真实模型厂商：按脚本流式返回，记录收到的请求。脚本每一步是一段文字，
    或 {"tool": 工具名, "args": {...}}——这一步改为发起一次工具调用。"""

    def __init__(self, texts: list, api_key: str) -> None:
        self.texts = texts
        self.api_key = api_key
        self.requests: list[dict] = []

    def app(self) -> Starlette:
        async def completions(request: Request):
            body = await request.json()
            self.requests.append({"headers": dict(request.headers), "body": body})
            if request.headers.get("authorization") != f"Bearer {self.api_key}":
                return JSONResponse({"error": {"message": "bad key"}}, status_code=401)
            step = self.texts[min(len(self.requests) - 1, len(self.texts) - 1)]

            async def stream():
                base = {"id": "x", "object": "chat.completion.chunk", "created": 0, "model": body["model"]}
                yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}}]})}\n\n"
                if isinstance(step, dict):
                    call = {"index": 0, "id": f"call_{len(self.requests)}", "type": "function",
                            "function": {"name": step["tool"], "arguments": json.dumps(step.get("args", {}))}}
                    yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'tool_calls': [call]}}]})}\n\n"
                    finish = "tool_calls"
                else:
                    for piece in re.findall(r".{1,6}", step):
                        yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {'content': piece}}]})}\n\n"
                    finish = "stop"
                yield f"data: {json.dumps({**base, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})}\n\n"
                yield f"data: {json.dumps({**base, 'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 3, 'total_tokens': 10}})}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(stream(), media_type="text/event-stream")

        return Starlette(routes=[Route("/v1/chat/completions", completions, methods=["POST"])])


class Served:
    """在当前事件循环里用 uvicorn 起一个 ASGI 应用。"""

    def __init__(self, app, port: int) -> None:
        self.port = port
        self.server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))
        self.task: asyncio.Task | None = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self.server.serve())
        while not self.server.started:
            await asyncio.sleep(0.02)
        return self

    async def __aexit__(self, *exc):
        self.server.should_exit = True
        await self.task


class HostProcess:
    """以生产配置档启动真实的 KnowWork 宿主（不叠加测试工具层）。patch 与 extra_env 供个别用例叠加配置。"""

    def __init__(self, model_base_url: str, model_name: str, *, token: str | None = None, port: int = 0,
                 home: Path | None = None, patch: Path | None = None,
                 extra_env: dict[str, str] | None = None) -> None:
        self.patch = patch
        self.extra_env = extra_env or {}
        self.token = token or secrets.token_hex(24)
        self.model_base_url = model_base_url
        self.model_name = model_name
        self.reuse_home = home is not None
        self.home = home or Path(tempfile.mkdtemp(prefix="kw-py-dsh-"))
        self.requested_port = port
        self.keep_home = False
        self.proc: asyncio.subprocess.Process | None = None
        self.port: int | None = None
        self.stderr = b""

    def _prepare_home(self) -> None:
        profile = self.home / "profiles" / "knowwork-host"
        if self.reuse_home:
            return
        profile.mkdir(parents=True)
        for name in ("package.json", "cordis.yml", "cordis.patch.yml", "pnpm-workspace.yaml"):
            shutil.copy(RUNTIME_HOST / "profile" / name, profile / name)
        modules = profile / "node_modules" / "@knowwork"
        modules.mkdir(parents=True)
        (modules / "dsh-host").symlink_to(RUNTIME_HOST / "plugins" / "dsh-host")
        (modules / "dsh-host-bundle").symlink_to(RUNTIME_HOST / "plugins" / "host-bundle")

    async def __aenter__(self):
        self._prepare_home()
        env = {
            **os.environ,
            "PATH": f"{NODE_BIN}:{os.environ['PATH']}",
            "DSH_HOME": str(self.home),
            "KNOWWORK_HOST_TOKEN": self.token,
            "KNOWWORK_HOST_PORT": str(self.requested_port),
            "KNOWWORK_MODEL_BASE_URL": self.model_base_url,
            "KNOWWORK_MODEL": self.model_name,
            **self.extra_env,
        }
        args = ["--profile", "knowwork-host", *(["--patch", str(self.patch)] if self.patch else [])]
        self.proc = await asyncio.create_subprocess_exec(
            str(RUNTIME_HOST / "node_modules" / ".bin" / "dsh"), *args,
            cwd=str(RUNTIME_HOST), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        async def read_ready():
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("宿主退出：" + (await self.proc.stderr.read()).decode(errors="replace")[-2000:])
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if parsed.get("knowworkHostReady"):
                    return parsed["port"]
        self.port = await asyncio.wait_for(read_ready(), timeout=60)
        # 之后持续读走宿主输出，否则管道写满会把宿主卡住
        self._drains = [asyncio.create_task(self._drain(self.proc.stdout)), asyncio.create_task(self._drain(self.proc.stderr))]
        return self

    async def _drain(self, pipe) -> None:
        while chunk := await pipe.read(65536):
            self.stderr = (self.stderr + chunk)[-20000:]

    async def __aexit__(self, *exc):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()
        for task in getattr(self, "_drains", []):
            await task
        if not self.keep_home:
            shutil.rmtree(self.home, ignore_errors=True)
