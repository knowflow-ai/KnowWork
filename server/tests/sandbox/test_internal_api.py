"""宿主远程执行器回调的沙箱内部接口：令牌、成员身份、会话路径映射、错误码。容器行为另由真实 Docker 测试覆盖。"""

import base64

import httpx
import pytest
from fastapi import FastAPI

from knowwork_server.sandbox.contract import ExecResult, SandboxError
from knowwork_server.sandbox.docker_sandbox import FileChunk, PathStat, SandboxFileError
from knowwork_server.sandbox.internal_api import build_internal_router
from knowwork_server.tools.service import ToolAccessError
from knowwork_server.tools.tokens import ToolTokenClaims

pytestmark = pytest.mark.anyio
PREFIX = "/internal/extensions/sandbox"


class FakeTokens:
    def verify(self, token: str) -> ToolTokenClaims:
        if token != "good":
            raise ToolAccessError(401, "unauthorized", "令牌无效")
        return ToolTokenClaims(workspace_id="ws1", session_id="s1", user_id="u1", tools=frozenset(), expires_at=0,
                               token_id="t")


class FakeSandbox:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.files: dict[str, bytes] = {"/workspace/a.txt": "第一行\n".encode()}

    async def exec(self, ws, sid, argv, *, timeout_seconds, max_output_bytes, workdir, env):
        self.calls.append(("exec", ws, sid, tuple(argv), workdir, dict(env), timeout_seconds))
        return ExecResult(exit_code=0, stdout="ok\n", stderr="", timed_out=False, truncated=False)

    async def stat_path(self, ws, sid, path, *, follow):
        self.calls.append(("stat", path, follow))
        data = self.files.get(path)
        return None if data is None else PathStat(kind="f", size=len(data), version="v1")

    async def read_file(self, ws, sid, path, *, offset, limit):
        if path not in self.files:
            raise SandboxFileError("文件不存在", "not_found")
        data = self.files[path]
        return FileChunk(path=path, size=len(data), offset=offset, data=data[offset:offset + limit])

    async def list_dir(self, ws, sid, path):
        raise SandboxError("目录不存在或无法读取")

    async def write_file(self, ws, sid, path, data):
        self.files[path] = data
        return len(data)


def _client(sandbox: FakeSandbox, *, member: bool = True) -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(build_internal_router(sandbox, FakeTokens(), lambda _u, _w: member, max_timeout_seconds=600))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal")


def _auth(token: str = "good") -> dict[str, str]:
    return {"authorization": f"Bearer {token}"}


async def test_exec_maps_the_session_path_to_the_workspace_and_caps_the_timeout():
    sandbox = FakeSandbox()
    async with _client(sandbox) as client:
        response = await client.post(f"{PREFIX}/exec", headers=_auth(), json={
            "argv": ["bash", "-c", "pwd"], "cwd": "/sessions/s1/sub", "timeoutMs": 3_600_000,
            "maxOutputBytes": 1024, "env": {"DSH_SESSION_ID": "s1"}})
    assert response.status_code == 200, response.text
    assert response.json()["stdout"] == "ok\n"
    _, ws, sid, argv, workdir, env, timeout = sandbox.calls[0]
    assert (ws, sid, argv, workdir, env) == ("ws1", "s1", ("bash", "-c", "pwd"), "/workspace/sub", {"DSH_SESSION_ID": "s1"})
    assert timeout == 600


@pytest.mark.parametrize("path", ["/sessions/s2/a.txt", "/etc/passwd", "/sessions/s1/../s2/a.txt", "relative.txt"])
async def test_paths_outside_this_session_are_refused(path):
    sandbox = FakeSandbox()
    async with _client(sandbox) as client:
        response = await client.post(f"{PREFIX}/stat", headers=_auth(), json={"path": path})
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    assert sandbox.calls == []


async def test_bad_token_and_non_member_are_refused():
    sandbox = FakeSandbox()
    async with _client(sandbox) as client:
        assert (await client.post(f"{PREFIX}/stat", headers=_auth("bad"), json={"path": "/sessions/s1"})).status_code == 401
        assert (await client.post(f"{PREFIX}/stat", json={"path": "/sessions/s1"})).status_code == 401
    async with _client(sandbox, member=False) as client:
        response = await client.post(f"{PREFIX}/stat", headers=_auth(), json={"path": "/sessions/s1"})
        assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"
    assert sandbox.calls == []


async def test_stat_read_write_round_trip_and_error_codes():
    sandbox = FakeSandbox()
    async with _client(sandbox) as client:
        stat = (await client.post(f"{PREFIX}/stat", headers=_auth(), json={"path": "/sessions/s1/a.txt"})).json()
        assert stat == {"stat": {"kind": "f", "size": len("第一行\n".encode()), "version": "v1"}}
        assert (await client.post(f"{PREFIX}/stat", headers=_auth(), json={"path": "/sessions/s1/none"})).json() == {"stat": None}

        read = (await client.post(f"{PREFIX}/read", headers=_auth(),
                                  json={"path": "/sessions/s1/a.txt", "offset": 0, "length": 100})).json()
        assert base64.b64decode(read["data"]).decode() == "第一行\n"
        missing = await client.post(f"{PREFIX}/read", headers=_auth(), json={"path": "/sessions/s1/x", "offset": 0, "length": 1})
        assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"

        written = await client.post(f"{PREFIX}/write", headers=_auth(),
                                    json={"path": "/sessions/s1/b.txt", "data": base64.b64encode(b"x").decode()})
        assert written.status_code == 200 and sandbox.files["/workspace/b.txt"] == b"x"
        bad = await client.post(f"{PREFIX}/write", headers=_auth(), json={"path": "/sessions/s1/b.txt", "data": "%%%"})
        assert bad.status_code == 400

        listing = await client.post(f"{PREFIX}/list", headers=_auth(), json={"path": "/sessions/s1/none"})
        assert listing.status_code == 400 and listing.json()["error"]["code"] == "sandbox_error"


async def test_exec_adds_env_of_loaded_skills_and_masks_their_values_in_output():
    class Echoing(FakeSandbox):
        async def exec(self, ws, sid, argv, *, timeout_seconds, max_output_bytes, workdir, env):
            await super().exec(ws, sid, argv, timeout_seconds=timeout_seconds, max_output_bytes=max_output_bytes,
                               workdir=workdir, env=env)
            return ExecResult(exit_code=0, stdout=f"key={env.get('REPORT_API_KEY')}\n", stderr="x1 9",
                              timed_out=False, truncated=False)

    asked: list[tuple] = []

    async def skill_env(workspace_id, user_id, loaded):
        asked.append((workspace_id, user_id, tuple(loaded)))
        return {"REPORT_API_KEY": "sk-live-123456", "SHORT": "x1"}

    sandbox = Echoing()
    app = FastAPI()
    app.include_router(build_internal_router(sandbox, FakeTokens(), lambda _u, _w: True, max_timeout_seconds=600,
                                             skill_env=skill_env))
    body = {"argv": ["bash", "-c", "printenv"], "cwd": "/sessions/s1", "timeoutMs": 1000, "maxOutputBytes": 1024}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://internal") as client:
        plain = await client.post(f"{PREFIX}/exec", headers=_auth(), json=body)
        loaded = await client.post(f"{PREFIX}/exec", headers=_auth(), json={**body, "skills": ["report-sync"]})
        bad = await client.post(f"{PREFIX}/exec", headers=_auth(), json={**body, "skills": ["../x"]})
    assert plain.json()["stdout"] == "key=None\n"
    assert asked == [("ws1", "u1", ("report-sync",))], "没报技能时不去取环境变量"
    assert sandbox.calls[1][5]["REPORT_API_KEY"] == "sk-live-123456", "命令里带上了值"
    assert loaded.json()["stdout"] == "key=******\n", "交回去的输出里不能有原文"
    assert loaded.json()["stderr"] == "x1 9", "太短的值不替换"
    assert bad.status_code == 400
