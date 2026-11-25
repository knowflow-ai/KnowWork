"""内容安全的接口：管理员读写策略（版本冲突、校验、权限）；宿主回调取策略摘要、送文字来查，命中写审计（不记原文、
同一段只记一次）；保存后本进程马上按新词查，别的进程最多晚几秒。"""

import uuid

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.content_safety import WordSource, WordSources, build_content_safety, build_router, internal_router
from knowwork_server.content_safety.service import ContentSafetyService
from knowwork_server.tools.service import ToolAccessError
from knowwork_server.tools.tokens import ToolTokenClaims

pytestmark = pytest.mark.anyio


class Sink:
    def __init__(self) -> None:
        self.events = []

    async def record(self, event) -> None:
        self.events.append(event)


class Tokens:
    """令牌就是「空间:人:会话」，认不出的按令牌无效拒绝。"""

    def verify(self, token: str) -> ToolTokenClaims:
        parts = token.split(":")
        if len(parts) != 3:
            raise ToolAccessError(401, "unauthorized", "令牌无效")
        return ToolTokenClaims(workspace_id=parts[0], user_id=parts[1], session_id=parts[2], tools=frozenset(),
                               expires_at=0, token_id="t")


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="安全", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    sink = Sink()
    sources = WordSources()
    sources.register(WordSource(id="builtin", label="内置词表", description="常见违规词", words=lambda: ("违禁词甲", "违禁词乙")))
    module = build_content_safety(sessionmaker, access, sink, sources=sources)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    def member(user_id: str, workspace_id: str) -> bool:
        return access.check(user_id, workspace_id, "workspace", "use")

    app = FastAPI()
    app.include_router(build_router(module.service, principal))
    app.include_router(internal_router(module, Tokens(), member))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        yield ws, http, sink, module, sessionmaker, access


def policy_body(version: int, **overrides) -> dict:
    return {"version": version, "input_enabled": True, "output_enabled": True,
            "words": "星河计划\n某某银行，星河计划", "block_message": "当前对话内容违反公司规范", **overrides}


async def test_admin_reads_unset_policy_then_saves_and_conflicts(env):
    ws, http, *_ = env
    path = f"/api/workspaces/{ws}/content-safety"
    first = await http.get(path, headers={"x-user": "boss"})
    assert first.status_code == 200 and first.json()["version"] == 0 and first.json()["input_enabled"] is False

    saved = await http.put(path, json=policy_body(0), headers={"x-user": "boss"})
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    assert saved.json()["words"] == "星河计划\n某某银行" and saved.json()["word_count"] == 2, "去重后一行一个"

    stale = await http.put(path, json=policy_body(0, words="别的词"), headers={"x-user": "boss"})
    assert stale.status_code == 409
    assert stale.json()["latest"]["version"] == 1 and stale.json()["latest"]["words"] == "星河计划\n某某银行"

    invalid = await http.put(path, json=policy_body(1, words=""), headers={"x-user": "boss"})
    assert invalid.status_code == 422 and "敏感词" in invalid.json()["detail"]

    member = await http.get(path, headers={"x-user": "alice"})
    assert member.status_code == 403, "普通成员看不到词表"


async def test_host_policy_summary_and_check_blocks_and_audits_once(env):
    ws, http, sink, *_ = env
    token = {"authorization": f"Bearer {ws}:alice:conv-1"}
    off = await http.get("/internal/extensions/content-safety/policy", headers=token)
    assert off.json() == {"enabled": False, "input": False, "output": False, "holdback": 0, "version": 0}
    clean = await http.post("/internal/extensions/content-safety/check", headers=token,
                            json={"direction": "input", "segments": ["星河计划的进度"]})
    assert clean.json() == {"blocked": False}, "没开就不查"
    assert sink.events == []

    await http.put(f"/api/workspaces/{ws}/content-safety", json=policy_body(0, output_enabled=False),
                   headers={"x-user": "boss"})
    on = await http.get("/internal/extensions/content-safety/policy", headers=token)
    assert on.json() == {"enabled": True, "input": True, "output": False, "holdback": 3, "version": 1}

    body = {"direction": "input", "segments": ["把星河", "计划的进度整理一下", "某某银行"]}
    hit = await http.post("/internal/extensions/content-safety/check", headers=token, json=body)
    assert hit.json() == {"blocked": True, "message": "当前对话内容违反公司规范"}, "不回命中的词"
    again = await http.post("/internal/extensions/content-safety/check", headers=token, json=body)
    assert again.json()["blocked"] is True
    assert len(sink.events) == 1, "同一段文字只记一次审计"
    event = sink.events[0].normalized()
    assert event.action == "content_safety.blocked" and event.result == "denied"
    assert event.actor_id == "alice" and event.resource_id == "conv-1"
    assert event.detail == {"direction": "input", "words": ["某某银行"], "word_count": 1, "policy_version": 1}, \
        "分段之间不会拼出词（「星河」「计划」在两段里）"
    assert "进度" not in str(event.row()), "不记原文"

    output = await http.post("/internal/extensions/content-safety/check", headers=token,
                             json={"direction": "tool_args", "tool": "send_email", "segments": ["星河计划"]})
    assert output.json() == {"blocked": False}, "没勾「检查输出」，工具参数不查"


async def test_tool_args_audit_names_the_tool(env):
    ws, http, sink, *_ = env
    await http.put(f"/api/workspaces/{ws}/content-safety", json=policy_body(0), headers={"x-user": "boss"})
    hit = await http.post("/internal/extensions/content-safety/check", headers={"authorization": f"Bearer {ws}:alice:c"},
                          json={"direction": "tool_args", "tool": "send_email", "segments": ['{"to":"星河计划组"}']})
    assert hit.json()["blocked"] is True
    assert sink.events[0].normalized().detail["tool"] == "send_email"


async def test_host_callbacks_need_a_valid_token_of_a_current_member(env):
    ws, http, *_ = env
    bad = await http.get("/internal/extensions/content-safety/policy", headers={"authorization": "Bearer nope"})
    assert bad.status_code == 401 and bad.json()["error"]["code"] == "unauthorized"
    gone = await http.post("/internal/extensions/content-safety/check",
                           headers={"authorization": f"Bearer {ws}:mallory:c"},
                           json={"direction": "input", "segments": ["x"]})
    assert gone.status_code == 403
    missing = await http.get("/internal/extensions/content-safety/policy")
    assert missing.status_code == 401


async def test_another_process_sees_the_new_version_after_the_cache_window(env):
    ws, http, _, module, sessionmaker, access = env
    now = [100.0]
    other = ContentSafetyService(sessionmaker, access, None, clock=lambda: now[0])
    assert (await other.summary(ws)).version == 0
    await http.put(f"/api/workspaces/{ws}/content-safety", json=policy_body(0), headers={"x-user": "boss"})
    assert (await module.service.summary(ws)).version == 1, "本进程保存后马上作废"
    assert (await other.summary(ws)).version == 0, "别的进程在缓存时间里还用旧的"
    now[0] += 2.5
    assert (await other.summary(ws)).version == 1


async def test_extension_word_sources_are_listed_merged_when_ticked_and_downloadable(env):
    ws, http, *_ = env
    path = f"/api/workspaces/{ws}/content-safety"
    token = {"authorization": f"Bearer {ws}:alice:conv-src"}
    viewed = (await http.get(path, headers={"x-user": "boss"})).json()
    assert viewed["word_sources"] == ["custom"], "没保存过时只用自定义词表"
    assert viewed["available_sources"] == [{"id": "builtin", "label": "内置词表", "description": "常见违规词",
                                            "word_count": 2}]

    # 只勾内置词表：不用填自定义词；自定义词留着但不查
    only = await http.put(path, json=policy_body(0, word_sources=["builtin"]), headers={"x-user": "boss"})
    assert only.status_code == 200, only.text
    assert only.json()["word_sources"] == ["builtin"]
    check = "/internal/extensions/content-safety/check"
    assert (await http.post(check, headers=token, json={"direction": "input", "segments": ["违禁词甲"]})).json()[
        "blocked"] is True
    assert (await http.post(check, headers=token, json={"direction": "input", "segments": ["星河计划"]})).json() == {
        "blocked": False}, "没勾自定义词表，自定义词不查"
    summary = (await http.get("/internal/extensions/content-safety/policy", headers=token)).json()
    assert summary["holdback"] == 3, "按勾上的词表算要留几个字"

    both = await http.put(path, json=policy_body(1, word_sources=["builtin", "custom"]), headers={"x-user": "boss"})
    assert both.status_code == 200
    assert (await http.post(check, headers=token, json={"direction": "input", "segments": ["星河计划"]})).json()[
        "blocked"] is True

    unknown = await http.put(path, json=policy_body(2, word_sources=["nope"]), headers={"x-user": "boss"})
    assert unknown.status_code == 422 and "没有这种词表" in unknown.json()["detail"]
    none = await http.put(path, json=policy_body(2, word_sources=[]), headers={"x-user": "boss"})
    assert none.status_code == 422 and "至少选一种词表" in none.json()["detail"]

    download = await http.get(f"{path}/sources/builtin/words", headers={"x-user": "boss"})
    assert download.status_code == 200 and download.text == "违禁词甲\n违禁词乙\n"
    assert "attachment" in download.headers["content-disposition"]
    assert (await http.get(f"{path}/sources/nope/words", headers={"x-user": "boss"})).status_code == 404
    assert (await http.get(f"{path}/sources/builtin/words", headers={"x-user": "alice"})).status_code == 403
