"""端到端：浏览器 → 业务服务 → 宿主（真实 DSH） → 业务服务模型网关 → 上游模型，再把 events.v1 流回浏览器并落库。"""

import asyncio
import contextlib
import io
import json
import secrets
import zipfile

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from knowwork_server.app import build_runtime, create_internal_app, create_public_app
from knowwork_server.config import Settings
from knowwork_server.conversations.models import RunEvent
from knowwork_server.conversations.replay import parse_cursor
from knowwork_server.db.base import make_workspace_sessionmaker
from knowwork_server.models.tables import UsageRecord

from .mcp_stub import TOKEN as MCP_TOKEN
from .mcp_stub import McpStub
from .support import HostProcess, Served, UpstreamStub, free_port

pytestmark = pytest.mark.anyio


class SseReader:
    """后台读一条 SSE 连接，把事件放进队列，便于“先连上再发消息”。"""

    def __init__(self, client: httpx.AsyncClient, url: str, headers: dict) -> None:
        self.client, self.url, self.headers = client, url, headers
        self.events: asyncio.Queue = asyncio.Queue()
        self.connected = asyncio.Event()
        self.task: asyncio.Task | None = None

    async def _run(self) -> None:
        async with self.client.stream("GET", self.url, headers=self.headers, timeout=60) as response:
            assert response.status_code == 200
            self.connected.set()
            fields: dict[str, str] = {}
            async for line in response.aiter_lines():
                if line == "":
                    if "event" in fields:
                        self.events.put_nowait({"id": fields.get("id"), "event": fields["event"],
                                                "data": json.loads(fields.get("data", "null"))})
                    fields = {}
                elif not line.startswith(":") and ":" in line:
                    key, _, value = line.partition(":")
                    fields[key] = value.strip()

    async def __aenter__(self):
        self.task = asyncio.create_task(self._run())
        await asyncio.wait_for(self.connected.wait(), 10)
        return self

    async def __aexit__(self, *exc):
        self.task.cancel()
        with contextlib.suppress(asyncio.CancelledError, httpx.HTTPError):
            await self.task

    async def until(self, predicate, timeout: float = 30) -> list[dict]:
        got = []
        async with asyncio.timeout(timeout):
            while True:
                event = await self.events.get()
                got.append(event)
                if predicate(event):
                    return got


def run_ended(run_id=None):
    def check(event):
        return event["event"] in ("run.completed", "run.failed", "run.cancelled") and (
            run_id is None or event["data"]["run_id"] == run_id)
    return check


def ordering_key(event):
    cursor = parse_cursor(event["id"])
    return cursor.seq, cursor.index


@contextlib.asynccontextmanager
async def stack(db, texts, *, host_port=0):
    """db 是全新空库的双账号（conftest.app_db）：运行时按生产路径启动，owner 账号迁移并授权，
    业务全程用普通账号（非超级用户、无 BYPASSRLS、非表 owner），行级安全真实生效。"""
    upstream_key = "sk-upstream-" + secrets.token_hex(8)
    upstream = UpstreamStub(texts, api_key=upstream_key)
    internal_port, public_port, upstream_port = free_port(), free_port(), free_port()
    model_base_url = f"http://127.0.0.1:{internal_port}/internal/model/v1"
    tools_url = f"http://127.0.0.1:{internal_port}/internal/extensions/tools"
    host = HostProcess(model_base_url=model_base_url, model_name="default", port=host_port,
                       extra_env={"KNOWWORK_TOOLS_BASE_URL": tools_url,
                                  "KNOWWORK_SKILLS_BASE_URL": tools_url.replace("/tools", "/skills")})
    async with Served(upstream.app(), upstream_port), host:
        settings = Settings(
            database_url=db.app_url, migration_database_url=db.owner_url, app_db_role=db.role,
            master_key=secrets.token_hex(32),
            host_base_url=f"http://127.0.0.1:{host.port}", host_token=host.token,
            model_upstream_base_url=f"http://127.0.0.1:{upstream_port}/v1", model_upstream_api_key=upstream_key,
            model_upstream_model="upstream-model-x",
        )
        runtime = build_runtime(settings)
        async with Served(create_internal_app(runtime), internal_port), Served(create_public_app(runtime), public_port):
            await runtime.start()
            try:
                member = await api_key_for(runtime, member=True)
                outsider = await api_key_for(runtime, member=False)
                async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{public_port}") as client:
                    yield {"client": client, "headers": member, "outsider": outsider, "runtime": runtime,
                           "upstream": upstream, "upstream_key": upstream_key, "host": host}
            finally:
                await runtime.stop()


async def api_key_for(runtime, *, member: bool) -> dict:
    """建一个账号并签发 API Key；member 为真时加入默认空间（首个成员即所有者），并能管理技能。"""
    user = await runtime.identity.users.create_user(email=f"u{secrets.token_hex(4)}@example.com", name="测试")
    if member:
        await runtime.access.ensure_default_workspace(user.id)
    scopes = ["conversations:use", "skills:read", "skills:write", "tools:manage"] if member else ["conversations:use"]
    key = await runtime.identity.api_keys.create(user_id=user.id, name="e2e", scopes=scopes)
    return {"authorization": f"Bearer {key.plaintext}"}


async def stored_rows(runtime, cid):
    await runtime.writer.flush()
    async with make_workspace_sessionmaker(runtime.engine, "default")() as session:
        return (await session.execute(select(RunEvent).where(RunEvent.conversation_id == cid)
                                      .order_by(RunEvent.seq))).scalars().all()


async def test_message_round_trip_and_reconnect(app_db):
    async with stack(app_db, ["这是来自上游模型的回答", "第二轮的回答"]) as s:
        client, headers = s["client"], s["headers"]
        assert (await client.post("/api/conversations", json={}, headers={})).status_code == 401
        assert (await client.post("/api/conversations", json={}, headers=s["outsider"])).status_code == 403
        created = await client.post("/api/conversations", json={"title": "测试"}, headers=headers)
        assert created.status_code == 201, created.text
        cid = created.json()["id"]
        url = f"/api/conversations/{cid}/events"

        async with SseReader(client, url, headers) as sse:  # 先连 SSE，再发消息
            sent = await client.post(f"/api/conversations/{cid}/messages", json={"text": "你好"}, headers=headers)
            assert sent.status_code == 202, sent.text
            events = await sse.until(run_ended())

        types = [e["event"] for e in events]
        assert types[0] == "run.started" and types[-1] == "run.completed", types
        messages = [e["data"]["data"] for e in events if e["event"] == "item.completed" and e["data"]["kind"] == "message"]
        assert {"role": "user", "text": "你好"} in messages, "提问要进事件流，刷新后才看得到"
        assert {"role": "assistant", "text": "这是来自上游模型的回答"} in messages
        assert all(e["data"]["schema_version"] == "events.v1" for e in events)
        keys = [ordering_key(e) for e in events]
        assert keys == sorted(keys) and len(set(keys)) == len(keys), "SSE id 必须严格递增"

        rows = await stored_rows(s["runtime"], cid)
        positions = [(r.host_epoch, r.host_cursor) for r in rows]
        assert len(positions) == len(set(positions)), "库中 (epoch, cursor) 必须唯一"
        assert [r.seq for r in rows] == list(range(1, len(rows) + 1))
        assert [r.seq for r in rows if r.ui] == sorted({e["data"]["seq"] for e in events})
        assert [u["type"] for r in rows for u in r.ui] == types, "SSE 收到的与库里投影的必须一致"
        assert any(r.kind == "session/event" and r.data.get("type") == "turn/end" for r in rows)

        # 断开后带 Last-Event-ID 重连：从中间接上，不漏不重；随后的实时事件照常收到
        resume_from = events[1]
        async with SseReader(client, url, {**headers, "last-event-id": resume_from["id"]}) as again:
            replayed = await again.until(run_ended("run-1"))
            assert [e["id"] for e in replayed] == [e["id"] for e in events[2:]]
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "再问一次"}, headers=headers)
            live = await again.until(run_ended("run-2"))
        assert live[0]["event"] == "run.started" and live[0]["data"]["run_id"] == "run-2"
        assert ordering_key(live[0]) > ordering_key(replayed[-1])

        upstream = s["upstream"]
        assert len(upstream.requests) == 2
        first = upstream.requests[0]
        assert first["headers"]["authorization"] == f"Bearer {s['upstream_key']}", "上游没有收到厂商密钥"
        assert first["body"]["model"] == "upstream-model-x", "模型名没有替换成配置的上游模型"
        assert "你好" in json.dumps(first["body"]["messages"], ensure_ascii=False)
        assert "store" not in first["body"] and "prompt_cache_key" not in first["body"]

        # 用量来自宿主事件流：两轮各一条，归到发起对话的账号，只存数字
        await s["runtime"].usage.drain()
        async with make_workspace_sessionmaker(s["runtime"].engine, "default")() as session:
            usage = (await session.execute(select(UsageRecord).order_by(UsageRecord.turn))).scalars().all()
        assert [(u.conversation_id, u.turn) for u in usage] == [(cid, 1), (cid, 2)]
        assert all(u.input_tokens == 7 and u.output_tokens == 3 for u in usage)
        assert len({u.user_id for u in usage}) == 1

        # 运行账号不是表的 owner：关不掉行级安全
        async with s["runtime"].engine.connect() as connection:
            assert await connection.scalar(text("SELECT rolsuper OR rolbypassrls FROM pg_roles"
                                                " WHERE rolname = current_user")) is False
            with pytest.raises(DBAPIError, match="must be owner"):
                await connection.execute(text("ALTER TABLE conversation DISABLE ROW LEVEL SECURITY"))


async def test_send_recovers_after_host_restart(app_db):
    host_port = free_port()
    async with stack(app_db, ["重启前", "重启后的回答"], host_port=host_port) as s:
        client, headers, host = s["client"], s["headers"], s["host"]
        cid = (await client.post("/api/conversations", json={}, headers=headers)).json()["id"]
        url = f"/api/conversations/{cid}/events"
        async with SseReader(client, url, headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "第一轮"}, headers=headers)
            await sse.until(run_ended())

        host.keep_home = True
        await host.__aexit__(None, None, None)
        restarted = HostProcess(model_base_url=host.model_base_url, model_name="default", token=host.token,
                                port=host_port, home=host.home, extra_env=host.extra_env)
        async with restarted:
            async with SseReader(client, url, headers) as sse:
                sent = await client.post(f"/api/conversations/{cid}/messages", json={"text": "重启后继续"},
                                         headers=headers)
                assert sent.status_code == 202, sent.text
                events = await sse.until(lambda e: e["event"] in ("run.completed", "run.failed")
                                         and e["data"]["run_id"] != "run-1", timeout=60)
            assert events[-1]["event"] == "run.completed", events[-1]
            texts = [e["data"]["data"].get("text") for e in events if e["data"]["kind"] == "message"]
            assert "重启后的回答" in texts
            rows = await stored_rows(s["runtime"], cid)
            assert len({r.host_epoch for r in rows}) == 2, "应当看到两个宿主实例的事件"
            assert [r.seq for r in rows] == list(range(1, len(rows) + 1))


def skill_package(name: str, body: str) -> bytes:
    text_md = f"---\nname: {name}\ndescription: 按公司模板写周报\nversion: 1.0.0\n---\n\n{body}\n"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{name}/SKILL.md", text_md)
    return buffer.getvalue()


async def test_published_skill_reaches_the_model(app_db):
    """管理员上传技能包并发布 → 成员对话里用 /技能名 显式调用，或模型经 skill 工具自己加载，正文都送到模型。"""
    body = "周报分三段：本周完成、下周计划、风险。每段不超过五条。"
    script = ["好的，按模板写。", {"tool": "skill", "args": {"name": "weekly-report"}}, "已按技能写好。"]
    async with stack(app_db, script) as s:
        client, headers, upstream = s["client"], s["headers"], s["upstream"]
        base = "/api/workspaces/default/skills"
        installed = await client.post(base, content=skill_package("weekly-report", body),
                                      headers={**headers, "content-type": "application/zip"})
        assert installed.status_code == 201, installed.text
        skill = installed.json()
        assert skill["status"] == "draft"
        published = await client.post(f"{base}/{skill['id']}/publish", json={},
                                      headers={**headers, "if-match": str(skill["draftRevision"])})
        assert published.status_code == 201, published.text

        cid = (await client.post("/api/conversations", json={"title": "周报"}, headers=headers)).json()["id"]
        async with SseReader(client, f"/api/conversations/{cid}/events", headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "/weekly-report 写本周周报"},
                              headers=headers)
            await sse.until(run_ended("run-1"))
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "再写一份"}, headers=headers)
            events = await sse.until(run_ended("run-2"))

        explicit = json.dumps(upstream.requests[0]["body"]["messages"], ensure_ascii=False)
        assert "weekly-report" in explicit and "按公司模板写周报" in explicit, "技能目录没有送到模型"
        assert body in explicit, "用户 /技能名 显式调用没有注入技能正文"

        assert len(upstream.requests) == 3
        loaded = json.dumps(upstream.requests[2]["body"]["messages"][-1], ensure_ascii=False)
        assert upstream.requests[2]["body"]["messages"][-1]["role"] == "tool"
        assert body in loaded, "模型经 skill 工具加载，工具结果里应是技能正文"
        assert events[-1]["event"] == "run.completed", [e["event"] for e in events]


async def test_mcp_tools_run_through_the_bridge_with_approval(app_db):
    """管理员登记 MCP 服务 → 模型看到它的工具 → 只读工具直接执行，写入工具先审批再执行，结果都回到模型。"""
    script = [{"tool": "mcp__demo__add", "args": {"a": 20, "b": 22}},
              {"tool": "mcp__demo__create_ticket", "args": {"title": "打印机坏了", "priority": "high"}},
              "工单已建好。"]
    async with McpStub(free_port()) as mcp, stack(app_db, script) as s:
        client, headers, upstream = s["client"], s["headers"], s["upstream"]
        created = await client.post("/api/workspaces/default/mcp-servers", headers=headers, json={
            "name": "demo", "title": "演示服务", "url": mcp.url, "headers": {"Authorization": MCP_TOKEN}})
        assert created.status_code == 201, created.text
        server = created.json()
        assert "mcp-test-token" not in created.text, "响应里不能有请求头明文"
        assert server["headers"] == [{"name": "Authorization", "last4": "1234"}]
        assert server["grantPattern"] == "mcp__demo__*" and len(server["tools"]) == 4

        cid = (await client.post("/api/conversations", json={"title": "MCP"}, headers=headers)).json()["id"]
        async with SseReader(client, f"/api/conversations/{cid}/events", headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "算一下再建个工单"}, headers=headers)
            waiting = await sse.until(lambda e: e["event"] == "run.waiting")
            approval_id = waiting[-1]["data"]["item_id"]
            assert mcp.calls == [("add", {"a": 20, "b": 22})], "写入工具在批准前不执行"
            decided = await client.post(f"/api/conversations/{cid}/approvals/{approval_id}", json={"allow": True},
                                        headers=headers)
            assert decided.status_code in (200, 202, 204), decided.text
            events = await sse.until(run_ended())

        assert events[-1]["event"] == "run.completed", [e["event"] for e in events]
        assert mcp.calls[1] == ("create_ticket", {"title": "打印机坏了", "priority": "high", "assignee": None})
        tools = {t["function"]["name"]: t["function"] for t in upstream.requests[0]["body"]["tools"]}
        assert {"mcp__demo__add", "mcp__demo__create_ticket", "mcp__demo__delete_item"} <= set(tools)
        assert "anyOf" in json.dumps(tools["mcp__demo__create_ticket"]["parameters"]), "MCP 的参数结构原样交给模型"
        assert upstream.requests[1]["body"]["messages"][-1]["role"] == "tool"
        assert "42" in json.dumps(upstream.requests[1]["body"]["messages"][-1], ensure_ascii=False)
        assert "已创建工单" in json.dumps(upstream.requests[2]["body"]["messages"][-1], ensure_ascii=False)


def item(kind, type_=None):
    return lambda event: event["data"]["kind"] == kind and (type_ is None or event["event"] == type_)


async def test_agent_keeps_todos_and_asks_the_user(app_db):
    """待办与提问：模型先列待办、再向用户提问，等回答；回答经业务服务交回宿主，进事件流与库，刷新后看得到。"""
    todos = [{"content": "读取合同", "status": "completed"}, {"content": "导出台账", "status": "in_progress"}]
    questions = [{"id": "fmt", "question": "导出成什么格式？", "header": "格式",
                  "options": [{"label": "Excel (Recommended)"}, {"label": "PDF"}]}]
    script = [{"tool": "todo_write", "args": {"todos": todos}}, {"tool": "ask_user_question", "args": {"questions": questions}},
              "已按 PDF 导出台账"]
    async with stack(app_db, script) as s:
        client, headers = s["client"], s["headers"]
        cid = (await client.post("/api/conversations", json={}, headers=headers)).json()["id"]
        url = f"/api/conversations/{cid}/events"
        async with SseReader(client, url, headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "导出合同台账"}, headers=headers)
            asked = await sse.until(item("input_request", "item.started"))
            assert [e["data"]["data"] for e in asked if item("todo")(e)] == [{"items": todos}]
            question = asked[-1]["data"]
            question_id = question["item_id"]
            assert [q["question"] for q in question["data"]["questions"]] == ["导出成什么格式？"]
            waiting = await sse.until(lambda e: e["event"] == "run.waiting")
            assert waiting[-1]["data"]["data"] == {"reason": "input"}

            listed = await client.get(f"/api/conversations/{cid}/questions", headers=headers)
            assert [(q["id"], q["state"]) for q in listed.json()] == [(question_id, "open")]
            answer_url = f"/api/conversations/{cid}/questions/{question_id}"
            outsider = await client.post(answer_url, json={"answers": [{"id": "fmt", "selected": ["PDF"]}]},
                                         headers=s["outsider"])
            assert outsider.status_code in (403, 404), "不是本会话发起人不能回答"
            wrong = await client.post(answer_url, json={"answers": [{"id": "fmt", "selected": ["Word"]}]}, headers=headers)
            assert (wrong.status_code, wrong.json()["detail"]["code"]) == (400, "invalid_answer")
            ok = await client.post(answer_url, json={"answers": [{"id": "fmt", "selected": ["PDF"]}]}, headers=headers)
            assert ok.status_code == 200, ok.text
            rest = await sse.until(run_ended())

        answered = [e["data"] for e in rest if item("input_request", "item.completed")(e)]
        assert [(a["item_id"], a["data"]) for a in answered] == [
            (question_id, {"answers": [{"id": "fmt", "selected": ["PDF"]}], "late": False})]
        assert rest[-1]["event"] == "run.completed"
        assert '\\"selected\\":[\\"PDF\\"]' in json.dumps(s["upstream"].requests[-1]["body"]["messages"]), "模型没有收到回答"
        assert (await client.post(answer_url, json={"answers": [{"id": "fmt", "selected": ["PDF"]}]},
                                  headers=headers)).status_code == 404, "答过的问题不能再答"

        # 刷新：从头回放，提问、回答、待办都还在；原始帧也落了库
        async with SseReader(client, url, headers) as again:
            replayed = await again.until(run_ended())
        assert [e["data"]["data"] for e in replayed if item("input_request", "item.completed")(e)] == [answered[0]["data"]]
        assert [e["data"]["data"] for e in replayed if item("todo")(e)] == [{"items": todos}]
        kinds = [r.kind for r in await stored_rows(s["runtime"], cid)]
        assert {"question/requested", "question/answered"} <= set(kinds)


async def test_question_survives_host_crash(app_db):
    """宿主在提问等待中崩溃：用户回答时业务服务以恢复方式重开宿主会话，官方把问题转入 continued，回答开新一轮。"""
    host_port = free_port()
    questions = [{"id": "fmt", "question": "导出成什么格式？", "options": [{"label": "Excel"}, {"label": "PDF"}]}]
    script = [{"tool": "ask_user_question", "args": {"questions": questions}}, "收到，按 PDF 导出"]
    async with stack(app_db, script, host_port=host_port) as s:
        client, headers, host = s["client"], s["headers"], s["host"]
        cid = (await client.post("/api/conversations", json={}, headers=headers)).json()["id"]
        url = f"/api/conversations/{cid}/events"
        async with SseReader(client, url, headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "导出台账"}, headers=headers)
            asked = await sse.until(item("input_request", "item.started"))
        question_id = asked[-1]["data"]["item_id"]

        host.proc.kill()  # 崩溃：不走正常关闭
        await host.proc.wait()
        host.keep_home = True
        await host.__aexit__(None, None, None)
        restarted = HostProcess(model_base_url=host.model_base_url, model_name="default", token=host.token,
                                port=host_port, home=host.home, extra_env=host.extra_env)
        async with restarted, SseReader(client, url, headers) as sse:
            answered = await client.post(f"/api/conversations/{cid}/questions/{question_id}",
                                         json={"answers": [{"id": "fmt", "selected": ["PDF"]}]}, headers=headers)
            assert answered.status_code == 200, answered.text
            events = await sse.until(lambda e: e["event"] in ("run.completed", "run.failed")
                                     and e["data"]["run_id"] != "run-1", timeout=60)
        assert events[-1]["event"] == "run.completed", events[-1]
        late = [e["data"] for e in events if item("input_request", "item.completed")(e)]
        assert [(e["item_id"], e["data"]["late"]) for e in late] == [(question_id, True)]
        assert "answer_to_pending_question" in json.dumps(s["upstream"].requests[-1]["body"]["messages"])


async def test_session_grant_skips_repeated_approvals(app_db):
    """批准时勾「本会话内同类操作不再询问」：同一对话里再调同一个工具，宿主应答者查到授权直接放行。"""
    script = [{"tool": "mcp__demo__create_ticket", "args": {"title": "第一张"}},
              {"tool": "mcp__demo__create_ticket", "args": {"title": "第二张"}},
              "两张工单都建好了。"]
    async with McpStub(free_port()) as mcp, stack(app_db, script) as s:
        client, headers = s["client"], s["headers"]
        created = await client.post("/api/workspaces/default/mcp-servers", headers=headers, json={
            "name": "demo", "title": "演示服务", "url": mcp.url, "headers": {"Authorization": MCP_TOKEN}})
        assert created.status_code == 201, created.text
        cid = (await client.post("/api/conversations", json={"title": "授权"}, headers=headers)).json()["id"]
        async with SseReader(client, f"/api/conversations/{cid}/events", headers) as sse:
            await client.post(f"/api/conversations/{cid}/messages", json={"text": "建两张工单"}, headers=headers)
            waiting = await sse.until(lambda e: e["event"] == "run.waiting")
            approval_id = waiting[-1]["data"]["item_id"]
            decided = await client.post(f"/api/conversations/{cid}/approvals/{approval_id}", headers=headers,
                                        json={"allow": True, "scope": "session"})
            assert decided.status_code == 200, decided.text
            events = await sse.until(run_ended())
        assert events[-1]["event"] == "run.completed", [e["event"] for e in events]
        assert [e["event"] for e in [*waiting, *events]].count("run.waiting") == 1, "第二次不再询问"
        assert [title for name, args in mcp.calls for title in [args["title"]]] == ["第一张", "第二张"]
