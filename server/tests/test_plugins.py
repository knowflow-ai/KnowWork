"""插件：管理员给空间定预置插件的档，成员在可选的里面自己开关；编运行配置时算出每个人生效的插件，进开会话的合同。"""

import json
import uuid

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.kernel.contract import SessionSpec
from knowwork_server.kernel.wire import session_body
from knowwork_server.plugins import CatalogPlugin, PluginService, build_me_router, build_router
from knowwork_server.plugins.catalog import CatalogCommand
from knowwork_server.profile.compiler import ProfileFacts, compile_profile

pytestmark = pytest.mark.anyio
CATALOG = (CatalogPlugin("goal", "长任务目标", "记目标", commands=(CatalogCommand("goal", "记目标", definition_id="g"),)),
           CatalogPlugin("plan", "规划模式", "先出计划"))


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="插件", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    await access.add_member("boss", ws, "bob", "editor")
    service = PluginService(sessionmaker, access, catalog=CATALOG)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_me_router(service, principal))
    app.include_router(build_router(service, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        yield ws, http, service, sessionmaker, access


async def test_admin_sets_modes_and_members_switch_optional_ones(env):
    ws, http, service, *_ = env
    admin, alice, bob = {"x-user": "boss"}, {"x-user": "alice"}, {"x-user": "bob"}
    base = f"/api/workspaces/{ws}"

    listed = (await http.get(f"{base}/plugins", headers=admin)).json()["plugins"]
    assert [(p["key"], p["mode"]) for p in listed] == [("goal", "off"), ("plan", "off")], "没设过就是关闭"
    assert (await http.get(f"{base}/me/plugins", headers=alice)).json() == {"plugins": []}
    assert await service.effective(ws, "alice") == ()

    assert (await http.put(f"{base}/plugins/goal", json={"mode": "on"}, headers=admin)).status_code == 200
    assert (await http.put(f"{base}/plugins/plan", json={"mode": "optional"}, headers=admin)).json()["mode"] == "optional"
    mine = (await http.get(f"{base}/me/plugins", headers=alice)).json()["plugins"]
    assert [(p["key"], p["enabled"], p["locked"]) for p in mine] == [("goal", True, True), ("plan", False, False)]
    assert mine[0]["commands"] == [{"name": "goal", "description": "记目标", "hint": None, "definition_id": "g"}], \
        "插件带的 / 命令一起给（新对话页列命令用）"

    assert (await http.put(f"{base}/me/plugins/plan", json={"enabled": True}, headers=alice)).json()["enabled"] is True
    assert await service.effective(ws, "alice") == ("goal", "plan")
    assert await service.effective(ws, "bob") == ("goal",), "别人的开关不影响他"

    locked = await http.put(f"{base}/me/plugins/goal", json={"enabled": False}, headers=bob)
    assert locked.status_code == 422 and "全员启用" in locked.json()["detail"]

    # 管理员改成关闭：个人开着的也不再生效；再打开成可选时，之前开过的人还是开着
    await http.put(f"{base}/plugins/plan", json={"mode": "off"}, headers=admin)
    assert await service.effective(ws, "alice") == ("goal",)
    off = await http.put(f"{base}/me/plugins/plan", json={"enabled": True}, headers=alice)
    assert off.status_code == 422
    await http.put(f"{base}/plugins/plan", json={"mode": "optional"}, headers=admin)
    assert await service.effective(ws, "alice") == ("goal", "plan")


async def test_permissions_and_validation(env):
    ws, http, *_ = env
    base = f"/api/workspaces/{ws}"
    assert (await http.get(f"{base}/plugins", headers={"x-user": "alice"})).status_code == 403, "成员不能管理空间的插件"
    assert (await http.put(f"{base}/plugins/goal", json={"mode": "on"}, headers={"x-user": "alice"})).status_code == 403
    assert (await http.get(f"{base}/me/plugins", headers={"x-user": "mallory"})).status_code == 403
    assert (await http.put(f"{base}/plugins/nope", json={"mode": "on"}, headers={"x-user": "boss"})).status_code == 422
    assert (await http.put(f"{base}/plugins/goal", json={"mode": "maybe"}, headers={"x-user": "boss"})).status_code == 422


async def test_workspaces_do_not_see_each_other(env):
    ws, http, service, _, access = env
    other = "ws_" + uuid.uuid4().hex[:10]
    await access.create_workspace(workspace_id=other, name="另一个", owner_user_id="boss")
    await http.put(f"/api/workspaces/{ws}/plugins/goal", json={"mode": "on"}, headers={"x-user": "boss"})
    assert await service.effective(ws, "boss") == ("goal",)
    assert await service.effective(other, "boss") == ()


async def test_an_empty_catalog_never_touches_the_database():
    class Exploding:
        def __call__(self):
            raise AssertionError("清单为空时不该查库")

        begin = __call__

    service = PluginService(Exploding(), access=None, catalog=())
    assert await service.effective("ws", "u") == ()


def test_builtin_components_copy_the_official_chinese_meta():
    """预置插件组件清单的标题和说明照抄官方包 locale/zh.json 的 meta：升级 DSH 后对不上就跟着改。"""
    from pathlib import Path

    from knowwork_server.plugins.catalog import CATALOG as REAL_CATALOG

    modules = Path(__file__).resolve().parents[2] / "runtime-host" / "node_modules"
    for plugin in REAL_CATALOG:
        assert plugin.detail and plugin.components, plugin.key
        for component in plugin.components:
            meta = json.loads((modules / component.module / "locale" / "zh.json").read_text("utf-8"))["meta"]
            assert (component.title, component.description) == (meta["title"], meta["description"]), component.module


def test_plugins_enter_the_profile_and_the_session_contract():
    plain = compile_profile(ProfileFacts(workspace_id="w", user_id="u", role="editor", member=True))
    with_plugins = compile_profile(ProfileFacts(workspace_id="w", user_id="u", role="editor", member=True,
                                                plugins=("plan", "goal", "goal")))
    assert with_plugins.plugins == ("goal", "plan"), "排序去重"
    assert "plugins" not in plain.payload() and "plugins" not in plain.host_payload(), \
        "没有插件时快照里不带这一项：加字段之前的版本号不变"
    assert plain.version != with_plugins.version and plain.host_version != with_plugins.host_version, \
        "插件变了，宿主会话要重开"

    assert "plugins" not in session_body(SessionSpec(session_id="c1"))
    assert session_body(SessionSpec(session_id="c1", plugins=("goal",)))["plugins"] == ["goal"]
    with pytest.raises(ValueError):
        SessionSpec(session_id="c1", plugins=("Bad Key",))


async def test_host_gives_plugin_tools_only_to_sessions_that_enable_it():
    """端到端（真实宿主 + 官方模拟模型）：合同里带 goal 的会话，模型看得到目标工具；不带的看不到；不认识的键被拒。"""
    import asyncio

    from knowwork_server.kernel.contract import TurnInput
    from knowwork_server.kernel.host_client import HostKernel

    from .support import HostProcess
    from .support_mock_llm import OVERLAY, MockLlmProcess

    async def turn(kernel: HostKernel, session_id: str) -> None:
        await kernel.send(session_id, TurnInput(text="你好"))

        async def until_end() -> None:
            async for frame in kernel.subscribe(session_id):
                if frame.kind == "session/event" and (frame.data or {}).get("type") == "turn/end":
                    return

        await asyncio.wait_for(until_end(), timeout=30)

    mock = MockLlmProcess(sequence=["success", "success", "tool_call_success", "success"], successText="好的",
                          toolName="create_goal", toolArguments='{"objective": "把三季度的合同都核对一遍"}')
    async with mock, HostProcess(model_base_url="http://127.0.0.1:1/unused", model_name="deepseek-chat",
                                 patch=OVERLAY, extra_env=mock.host_env()) as host:
        kernel = HostKernel(f"http://127.0.0.1:{host.port}", host.token)
        try:
            await kernel.open_session(SessionSpec(session_id="conv-with-goal", plugins=("goal",)))
            await turn(kernel, "conv-with-goal")
            await kernel.open_session(SessionSpec(session_id="conv-plain"))
            await turn(kernel, "conv-plain")
            # 模型真的调一次目标工具：这一轮照常结束，工具结果回到下一次模型请求
            await kernel.open_session(SessionSpec(session_id="conv-goal-call", plugins=("goal",)))
            await turn(kernel, "conv-goal-call")
            # / 命令：开了「长任务目标」的会话列得出 /goal，执行不经模型；别的会话没有
            listed = await kernel.commands("conv-with-goal")
            goal = next(command for command in listed if command.name == "goal")
            assert goal.definition_id == "@deepseek-ai/dsh-command-goal"
            # 业务服务清单里写的命令（新对话页用）要和宿主实际登记的一致
            from knowwork_server.plugins.catalog import CATALOG as REAL_CATALOG

            declared = next(p for p in REAL_CATALOG if p.key == "goal").commands
            assert [(c.name, c.definition_id, c.hint) for c in declared] == [(goal.name, goal.definition_id, goal.hint)]
            outcome = await kernel.run_command("conv-with-goal", "/goal 把三季度的合同都核对一遍")
            assert outcome.kind == "success" and outcome.command_id.startswith("cmd-")
            assert "三季度" in ((await kernel.run_command("conv-with-goal", "/goal")).text or "")
            assert not any(command.name == "goal" for command in await kernel.commands("conv-plain"))
            from knowwork_server.kernel.contract import CommandUnknown

            with pytest.raises(CommandUnknown):
                await kernel.run_command("conv-plain", "/goal 不该成功")
            with pytest.raises(Exception, match="不认识插件"):
                await kernel.open_session(SessionSpec(session_id="conv-bad", plugins=("no-such-plugin",)))
        finally:
            await kernel.aclose()
        requests = await mock.requests()
    with_goal, plain = ({tool["name"] for tool in request["body"]["tools"]} for request in requests[:2])
    assert {"create_goal", "get_goal", "update_goal"} <= with_goal, with_goal
    assert not {"create_goal", "get_goal", "update_goal"} & plain, "没启用插件的会话不该看到目标工具"
    assert len(requests) == 4, "命令不发给模型"
    assert "把三季度的合同都核对一遍" in json.dumps(requests[3]["body"]["messages"], ensure_ascii=False), \
        "目标工具的结果没有回到模型"
