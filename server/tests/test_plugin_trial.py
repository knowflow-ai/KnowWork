"""试装上传的插件：开了专属宿主时上传先是「检查中」，worker 起一次性宿主试装，结果写回（通过的记下新增的工具，没通过的写
原因，都不生效直到通过）；试装判定的规则；真起宿主进程试一个好插件、一个激活就报错的、一个导入不了的。"""

import io
import json
import tarfile
import uuid

import httpx
import pytest
from fastapi import FastAPI, Header

from knowwork_server.access.service import AccessService
from knowwork_server.auth.principal import Principal
from knowwork_server.db.base import in_workspace
from knowwork_server.hosts import HostPlugin, ProcessLauncher
from knowwork_server.hosts.trial import PluginTrial, TrialResult, TrialUnavailable, judge
from knowwork_server.jobs import Claim, JobFailure, JobRunner
from knowwork_server.kernel.host_client import PluginEntry, PluginInventory
from knowwork_server.plugins import build_my_uploads_router, build_plugins
from knowwork_server.plugins.models import PluginInstallation
from knowwork_server.plugins.trial_job import TrialJob
from knowwork_server.skills.blobstore import LocalBlobStore

from .support import NODE_BIN, RUNTIME_HOST

pytestmark = pytest.mark.anyio


def archive(name: str = "kw-demo-trial", *, apply: str | None = None, imports: str = "") -> bytes:
    """一个插件包：配置补丁插一行自己，apply 里登记一个工具（或者给的别的代码）。官方包写成 peerDependencies。"""
    body = apply if apply is not None else """  ctx.tools.register(defineTool({
    name: 'demo_trial_echo',
    description: 'Echo the text back (test plugin).',
    parameters: { text: { type: 'string', required: true, description: 'text' } },
    output: { schema: { type: 'string' }, render: (_a, v) => [{ type: 'text', text: String(v) }] },
    async execute(args) { return 'echo: ' + args.text },
  }))"""
    files = {
        "package.json": json.dumps({"name": name, "version": "1.0.0", "type": "module", "main": "index.mjs",
                                    "description": "试装用的插件",
                                    "peerDependencies": {"@deepseek-ai/dsh-tools": "*"},
                                    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}}),
        "cordis.patch.yml": f"- insert:\n    - id: trial-probe\n      name: '{name}'\n",
        "index.mjs": f"""import {{ defineTool }} from '@deepseek-ai/dsh-tools'
{imports}
export const name = '{name}'
export const inject = ['tools']
export function apply(ctx) {{
{body}
}}
""",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for path, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(f"package/{path}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


# ---- 判定 ----

def inventory(*entries: PluginEntry, tools: tuple[str, ...] = ()) -> PluginInventory:
    return PluginInventory(entries=entries, tools=tools)


def test_judge_needs_every_enabled_component_active_and_reports_new_tools():
    base = frozenset({"bash", "read"})
    ok = judge(inventory(PluginEntry("a", "kw-x", True, "active"), PluginEntry("b", "kw-x/extra", False, None),
                         tools=("bash", "read", "x_one", "x_two")), "", ["kw-x", "kw-x/extra"], base)
    assert ok == TrialResult(True, "", ("x_one", "x_two")), "关着的组件不要求激活；比基线多出来的是新增的工具"

    failed = judge(inventory(PluginEntry("a", "kw-x", True, "failed", "boom")), "", ["kw-x"], base)
    assert not failed.ok and failed.reason == "kw-x 激活失败：boom"

    logs = "\x1b[31m[E] loader\x1b[0m Cannot find package 'left-pad' imported from /plugins/kw-x/index.mjs\nother"
    missing = judge(inventory(PluginEntry("a", "kw-x", True, None)), logs, ["kw-x"], base)
    assert not missing.ok and "kw-x 没有加载起来：[E] loader Cannot find package 'left-pad'" in missing.reason, \
        "导入不了的原因从日志里找、去掉终端颜色"

    absent = judge(inventory(PluginEntry("a", "other", True, "active")), "", ["kw-x"], base)
    assert not absent.ok and "找不到这个插件的组件" in absent.reason


# ---- 上传、排任务、写回 ----

class FakeTrial:
    def __init__(self, result: TrialResult | Exception) -> None:
        self.result, self.calls = result, []

    async def run(self, label, plugin: HostPlugin, modules):
        self.calls.append((label, plugin.name, modules, len(await plugin.load())))
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
async def env(sessionmaker, tmp_path):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="插件", owner_user_id="boss")
    await access.add_member("boss", ws, "alice", "editor")
    blobs = LocalBlobStore(tmp_path)
    plugins = build_plugins(sessionmaker, access, blobs, cloud=True)

    async def principal(x_user: str = Header()) -> Principal:
        return Principal(user_id=x_user, auth_method="session")

    app = FastAPI()
    app.include_router(build_my_uploads_router(plugins.uploads, principal))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://kw") as http:
        yield ws, http, plugins.uploads, blobs, sessionmaker


def runner_with(sessionmaker, blobs, trial) -> JobRunner:
    runner = JobRunner(sessionmaker, pools={"core": 1})
    TrialJob(sessionmaker, blobs, trial).register(runner)
    return runner


async def uploads_row(sessionmaker, ws: str, plugin_id: str) -> PluginInstallation:
    with in_workspace(ws):
        async with sessionmaker() as session:
            return await session.get(PluginInstallation, plugin_id)


async def upload(http, ws, data: bytes) -> dict:
    response = await http.post(f"/api/workspaces/{ws}/me/plugins/uploads", content=data,
                               headers={"x-user": "alice", "content-type": "application/gzip"})
    assert response.status_code == 201, response.text
    return response.json()


async def test_uploads_are_checked_before_they_take_effect(env):
    ws, http, uploads, blobs, sessionmaker = env
    uploaded = await upload(http, ws, archive())
    assert uploaded["status"] == "checking"
    assert await uploads.effective(ws, "alice", "cloud") == (), "检查中的不生效"

    trial = FakeTrial(TrialResult(True, "", ("demo_trial_echo",)))
    assert (await runner_with(sessionmaker, blobs, trial).run_once(ws)).status == "succeeded"
    assert trial.calls == [(uploaded["id"].removeprefix("pi_")[:16], "kw-demo-trial", ("kw-demo-trial",),
                            len(archive()))]
    mine = (await http.get(f"/api/workspaces/{ws}/me/plugins/uploads", headers={"x-user": "alice"})).json()
    assert mine["plugins"][0]["status"] == "ready" and mine["plugins"][0]["tools"] == ["demo_trial_echo"]
    assert [item.name for item in await uploads.effective(ws, "alice", "cloud")] == ["kw-demo-trial"]


async def test_a_rejected_plugin_stays_off_with_the_reason(env):
    ws, http, uploads, blobs, sessionmaker = env
    await upload(http, ws, archive())
    await runner_with(sessionmaker, blobs, FakeTrial(TrialResult(False, "kw-demo-trial 激活失败：boom"))).run_once(ws)
    mine = (await http.get(f"/api/workspaces/{ws}/me/plugins/uploads", headers={"x-user": "alice"})).json()
    assert (mine["plugins"][0]["status"], mine["plugins"][0]["reason"]) == ("failed", "kw-demo-trial 激活失败：boom")
    assert await uploads.effective(ws, "alice", "device") == (), "没通过的哪边都不生效"


async def test_a_result_for_a_replaced_archive_is_dropped(env):
    ws, http, _, blobs, sessionmaker = env
    await upload(http, ws, archive())
    second = await upload(http, ws, archive(apply="  // 第二版\n"))  # 同名再传：换了包，前一条任务合并成这一条
    trial = FakeTrial(TrialResult(True, "", ()))
    await runner_with(sessionmaker, blobs, trial).run_once(ws)
    assert len(trial.calls) == 1 and trial.calls[0][3] == len(archive(apply="  // 第二版\n")), "试的是新包"
    mine = (await http.get(f"/api/workspaces/{ws}/me/plugins/uploads", headers={"x-user": "alice"})).json()
    assert mine["plugins"][0]["id"] == second["id"] and mine["plugins"][0]["status"] == "ready"


async def test_an_unavailable_trial_retries_then_gives_up_with_a_reason(env):
    ws, http, _, blobs, sessionmaker = env
    await upload(http, ws, archive())
    job = TrialJob(sessionmaker, blobs, FakeTrial(TrialUnavailable("宿主起不来")))
    runner = JobRunner(sessionmaker, pools={"core": 1})
    job.register(runner)
    assert (await runner.run_once(ws)).status == "retry"
    mine = (await http.get(f"/api/workspaces/{ws}/me/plugins/uploads", headers={"x-user": "alice"})).json()
    row = mine["plugins"][0]
    assert row["status"] == "checking", "等重试时还是检查中"

    digest = (await uploads_row(sessionmaker, ws, row["id"])).archive_digest
    claim = Claim(job_id="job_x", workspace_id=ws, kind="plugin.trial", subject_id=row["id"],
                  payload={"digest": digest}, tries=3, max_tries=3, token="t")
    async with sessionmaker.begin() as session:
        await job.give_up(session, claim, JobFailure("trial_unavailable", "试装用的宿主起不来", "service"))
    mine = (await http.get(f"/api/workspaces/{ws}/me/plugins/uploads", headers={"x-user": "alice"})).json()
    assert mine["plugins"][0]["status"] == "failed" and "没能试装" in mine["plugins"][0]["reason"]


async def test_without_dedicated_hosts_nothing_is_queued(sessionmaker, tmp_path):
    ws = "ws_" + uuid.uuid4().hex[:10]
    access = AccessService(sessionmaker)
    assert await access.reload()
    await access.create_workspace(workspace_id=ws, name="插件", owner_user_id="boss")
    uploads = build_plugins(sessionmaker, access, LocalBlobStore(tmp_path)).uploads
    installed = await uploads.install("boss", ws, "personal", archive(), ["device"])
    assert installed.status == "ready", "服务端没地方跑没审过的代码，不试装，直接给桌面端用"
    runner = runner_with(sessionmaker, LocalBlobStore(tmp_path), FakeTrial(TrialResult(True)))
    assert await runner.run_once(ws) is None


# ---- 真起宿主进程试装 ----

async def test_real_trials_report_tools_activation_errors_and_import_failures(tmp_path):
    launcher = ProcessLauncher(runtime_dir=RUNTIME_HOST, root=tmp_path, node_bin=str(NODE_BIN),
                               env={"KNOWWORK_MODEL": "deepseek-chat", "KNOWWORK_MODEL_BASE_URL": "http://127.0.0.1:1/x"})
    trial = PluginTrial(launcher)

    def plugin(data: bytes, digest: str) -> HostPlugin:
        async def load() -> bytes:
            return data

        return HostPlugin(name="kw-demo-trial", digest=digest, load=load)

    good = await trial.run("good", plugin(archive(), "1" * 64), ["kw-demo-trial"])
    assert good == TrialResult(True, "", ("demo_trial_echo",)), "基线里没有的工具就是它新增的"

    throws = await trial.run("throws", plugin(archive(apply="  throw new Error('配置不对')"), "2" * 64),
                             ["kw-demo-trial"])
    assert not throws.ok and throws.reason == "kw-demo-trial 激活失败：配置不对"

    missing = await trial.run("missing", plugin(archive(imports="import 'left-pad-not-here'"), "3" * 64),
                              ["kw-demo-trial"])
    assert not missing.ok and "left-pad-not-here" in missing.reason, missing.reason

    assert not (tmp_path / "trials").exists() or not any((tmp_path / "trials").iterdir()), "试装的主目录用完就删"
    assert not (tmp_path / "homes").exists(), "不碰任何人的持久目录"
