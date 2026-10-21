"""启动官方模拟模型服务 @deepseek-ai/dsh-llm-mock-server（经 runtime-host/tests/support/mock-llm-server.mjs）。"""

import asyncio
import json
import os

import httpx

from .support import NODE_BIN, RUNTIME_HOST

LAUNCHER = RUNTIME_HOST / "tests" / "support" / "mock-llm-server.mjs"
OVERLAY = RUNTIME_HOST / "tests" / "overlays" / "official-mock.yml"


class MockLlmProcess:
    def __init__(self, **options) -> None:
        self.options = options
        self.proc: asyncio.subprocess.Process | None = None
        self.base_url = ""
        self.control_url = ""

    def host_env(self) -> dict[str, str]:
        """让宿主经官方 DeepSeek 适配器访问模拟服务。"""
        return {"DEEPSEEK_BASE_URL": f"{self.base_url}/v1" if not self.base_url.endswith("/v1") else self.base_url,
                "DEEPSEEK_API_KEY": "mock-key", "KNOWWORK_MODEL_PROVIDER": "deepseek-official",
                "KNOWWORK_MODEL": "deepseek-chat"}

    async def requests(self) -> list[dict]:
        async with httpx.AsyncClient() as client:
            return (await client.get(f"{self.control_url}/requests")).json()

    async def __aenter__(self):
        env = {**os.environ, "PATH": f"{NODE_BIN}:{os.environ['PATH']}"}
        self.proc = await asyncio.create_subprocess_exec(
            str(NODE_BIN / "node"), str(LAUNCHER), json.dumps(self.options), cwd=str(RUNTIME_HOST), env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

        async def ready():
            while True:
                line = await self.proc.stdout.readline()
                if not line:
                    raise RuntimeError("模拟服务退出：" + (await self.proc.stderr.read()).decode(errors="replace")[-2000:])
                try:
                    parsed = json.loads(line)
                except ValueError:
                    continue
                if parsed.get("mockReady"):
                    return parsed

        parsed = await asyncio.wait_for(ready(), timeout=30)
        self.base_url, self.control_url = parsed["baseURL"], parsed["controlURL"]
        return self

    async def __aexit__(self, *exc):
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            await self.proc.wait()
