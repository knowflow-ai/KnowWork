"""个人专属宿主：用到才建、空闲回收、数量上限、插件变了换新的；按对话路由；真起宿主进程的端到端。"""

import asyncio
import contextlib
import io
import json
import os
import tarfile

import httpx
import pytest

from knowwork_server.hosts import (
    DedicatedHosts,
    HostKey,
    HostPlugin,
    HostUnavailable,
    MemoryPlacement,
    ProcessLauncher,
    RoutedKernel,
)
from knowwork_server.hosts.launcher import LaunchFailed, LaunchRaced, install_profile, signature_of, unpack_plugin
from knowwork_server.kernel.contract import SessionNotFound, SessionOwner, SessionSpec, TurnInput
from knowwork_server.kernel.host_client import HostActivity, HostKernel

from .support import NODE_BIN, RUNTIME_HOST, HostProcess
from .support_mock_llm import OVERLAY, MockLlmProcess

pytestmark = pytest.mark.anyio


def plugin_archive(name: str = "kw-demo-notes", tool: str = "demo_notes_echo") -> bytes:
    """一个能装进宿主的最小插件包：登记一个工具。官方包写成 peerDependencies，由宿主提供。"""
    files = {
        "package.json": json.dumps({"name": name, "version": "1.0.0", "type": "module", "main": "index.mjs",
                                    "peerDependencies": {"@deepseek-ai/dsh-tools": "*"},
                                    "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}}),
        "cordis.patch.yml": f"- insert:\n    - id: {tool.replace('_', '-')}\n      name: '{name}'\n",
        "index.mjs": f"""import {{ defineTool }} from '@deepseek-ai/dsh-tools'
export const name = '{name}'
export const inject = ['tools']
export function apply(ctx) {{
  ctx.tools.register(defineTool({{
    name: '{tool}',
    description: 'Echo the text back (test plugin).',
    parameters: {{ text: {{ type: 'string', required: true, description: 'text' }} }},
    output: {{ schema: {{ type: 'string' }}, render: (_a, v) => [{{ type: 'text', text: String(v) }}] }},
    async execute(args) {{ return 'echo: ' + args.text }},
  }}))
}}
""",
    }
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, content in files.items():
            info = tarfile.TarInfo(f"package/{path}")
            data = content.encode()
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def host_plugin(digest: str = "a" * 64, name: str = "kw-demo-notes") -> HostPlugin:
    async def load() -> bytes:
        return plugin_archive(name)

    return HostPlugin(name=name, digest=digest, load=load)


# ---- 管理：用假的启动器（当成 Docker：几个业务服务进程看到同一份）和会报活动情况的假宿主 ----

class FakeLaunched:
    def __init__(self, n: int, signature: str, plugins: int) -> None:
        self.base_url, self.token = f"http://127.0.0.1:{9000 + n}", "t" * 64
        self.signature, self.plugins = signature, plugins
        self.stopped = self.dead = False
        self.activity = HostActivity(idle_seconds=0.0, sessions=0, busy=0)  # 宿主自己报的

    def alive(self) -> bool:
        return not self.stopped and not self.dead

    async def stop(self) -> None:
        self.stopped = True


class FakeLauncher:
    """几个 DedicatedHosts 共用一个：它就是「现在有哪些宿主」的登记处（同 Docker）。"""

    def __init__(self) -> None:
        self.launched: list[tuple[HostKey, FakeLaunched]] = []
        self.fail = False
        self.removed: list[HostKey] = []

    def _live(self) -> dict[HostKey, FakeLaunched]:
        return {key: made for key, made in self.launched if made.alive()}

    async def launch(self, key, plugins, *, ephemeral=False):
        if self.fail:
            raise LaunchFailed("起不来")
        if key in self._live():
            raise LaunchRaced()
        made = FakeLaunched(len(self.launched), signature_of(plugins), len(plugins))
        self.launched.append((key, made))
        return made

    async def find(self, key):
        return self._live().get(key)

    async def running(self):
        return list(self._live().items())

    async def remove_home(self, key):
        self.removed.append(key)

    async def aclose(self):
        return None


class FakeKernel:
    def __init__(self, launched: FakeLaunched) -> None:
        self.launched, self.closed = launched, False

    async def activity(self) -> HostActivity:
        if not self.launched.alive():
            raise httpx.ConnectError("gone")
        return self.launched.activity

    async def aclose(self) -> None:
        self.closed = True


def manager(launcher: FakeLauncher, **options) -> DedicatedHosts:
    return DedicatedHosts(launcher, prepare=no_prepare, kernel_for=FakeKernel, **options)


def idle(made: FakeLaunched, seconds: float, busy: int = 0) -> None:
    made.activity = HostActivity(idle_seconds=seconds, sessions=1, busy=busy)


async def no_prepare(_key, _kernel) -> None:
    return None


async def test_hosts_are_created_on_demand_shared_across_processes_and_reclaimed_when_idle():
    launcher = FakeLauncher()
    one, two = manager(launcher, max_hosts=2, idle_seconds=600), manager(launcher, max_hosts=2, idle_seconds=600)
    alice, bob, carol = HostKey("ws", "alice"), HostKey("ws", "bob"), HostKey("ws", "carol")
    first = await one.ensure(alice, [host_plugin()])
    assert await one.ensure(alice, [host_plugin()]) is first, "同一个人的两段对话共用一个宿主"
    taken = await two.ensure(alice, [host_plugin()])
    assert len(launcher.launched) == 1 and taken.launched is first.launched, "另一个进程接过来，不另建"
    await two.ensure(bob, [host_plugin()])

    # 满了、都在用：拒绝，不排队
    with pytest.raises(HostUnavailable, match="满了"):
        await one.ensure(carol, [host_plugin()])
    # alice 的空了一会儿：收她的，给 carol（谁建的都算）
    idle(launcher.launched[0][1], 100)
    await one.ensure(carol, [host_plugin()])
    assert launcher.launched[0][1].stopped and await two.kernel_of(alice) is None

    # 空闲超过时限的回收；有对话没停下来（正在跑、有订阅者）的哪怕很久没请求也不收
    idle(launcher.launched[1][1], 601)
    idle(launcher.launched[2][1], 3600, busy=1)
    assert await two.sweep() == 1
    assert await one.kernel_of(bob) is None and await one.kernel_of(carol) is not None
    await one.aclose()
    await two.aclose()
    assert not launcher.launched[2][1].stopped, "业务服务退出不停宿主（容器起法），别的进程、重启后接着用"


async def test_two_processes_creating_at_once_end_up_on_one_host():
    launcher = FakeLauncher()
    one, two = manager(launcher), manager(launcher)
    alice = HostKey("ws", "alice")
    made = await one.ensure(alice, [host_plugin()])
    two._clients.clear()
    original = launcher.find

    async def not_yet(key):  # two 先看时还没有，去建时 one 已经建好了：撞上 LaunchRaced，改去接
        launcher.find = original

    launcher.find = not_yet
    assert (await two.ensure(alice, [host_plugin()])).launched is made.launched
    assert len(launcher.launched) == 1


async def test_plugin_changes_restart_only_a_quiet_host_and_dead_hosts_come_back():
    launcher = FakeLauncher()
    hosts = manager(launcher)
    alice = HostKey("ws", "alice")
    old = await hosts.ensure(alice, [host_plugin("a" * 64)])
    assert await hosts.ensure(alice, [host_plugin("b" * 64)]) is old, "正在用：先不换"
    idle(old.launched, 31)
    fresh = await hosts.ensure(alice, [host_plugin("b" * 64)])
    assert fresh is not old and old.launched.stopped and fresh.launched.signature == signature_of([host_plugin("b" * 64)])

    fresh.launched.dead = True  # 宿主自己退了
    assert await hosts.kernel_of(alice) is None
    assert await hosts.ensure(alice, [host_plugin("b" * 64)]) is not fresh
    assert len(launcher.launched) == 3

    launcher.fail = True
    with pytest.raises(HostUnavailable, match="没有起来"):
        await hosts.ensure(HostKey("ws", "bob"), [])
    await hosts.aclose()


async def test_a_host_reclaimed_by_another_process_sends_its_conversations_through_recovery():
    """one 连着 alice 的宿主，two 把它收了：one 再用时连不上，报「会话不在」让对话服务恢复，恢复时新建一个。"""
    launcher = FakeLauncher()
    one, two = manager(launcher, idle_seconds=600), manager(launcher, idle_seconds=600)
    alice = HostKey("ws", "alice")
    await one.ensure(alice, [host_plugin()])
    idle(launcher.launched[0][1], 601)
    assert await two.sweep() == 1
    placement = MemoryPlacement()
    await placement.put("c1", "ws", alice)

    async def plugins_of(_workspace, _user):
        return [host_plugin()]

    class Stale:
        async def send(self, session_id, turn):
            raise httpx.ConnectError("refused")

    routed = RoutedKernel(None, one, placement, plugins_of)
    one._clients[alice].kernel = Stale()  # one 手里还缓存着那台的客户端
    one._clients[alice].launched.dead = False
    one._clients[alice].launched.stopped = False
    with pytest.raises(SessionNotFound):
        await routed.send("c1", TurnInput(text="hi"))
    assert alice not in one._clients, "连不上就放掉，下次重新找"


async def test_home_is_kept_while_any_process_runs_the_host():
    launcher = FakeLauncher()
    one, two = manager(launcher), manager(launcher)
    alice = HostKey("ws", "alice")
    await one.ensure(alice, [host_plugin()])
    assert await two.remove_home(alice) is False and launcher.removed == []
    launcher.launched[0][1].stopped = True
    assert await two.remove_home(alice) is True and launcher.removed == [alice]


def test_profile_gets_the_plugins_after_our_own_layers(tmp_path):
    plugin = tmp_path / "cache" / "p1"
    unpack_plugin(plugin_archive(), plugin)
    assert (plugin / "index.mjs").is_file()
    install_profile(tmp_path / "home", template=RUNTIME_HOST / "profile",
                    bundles={"@knowwork/dsh-host": RUNTIME_HOST / "plugins" / "dsh-host"},
                    plugins={"kw-demo-notes": plugin, "@knowwork/dsh-host": plugin})
    profile = tmp_path / "home" / "profiles" / "knowwork-host"
    bundles = json.loads((profile / "package.json").read_text())["dsh"]["profile"]["bundles"]
    assert bundles[-1] == "kw-demo-notes" and bundles.count("@knowwork/dsh-host") == 1
    assert (profile / "node_modules" / "kw-demo-notes").resolve() == plugin.resolve()
    assert (profile / "node_modules/@knowwork/dsh-host").resolve() == (RUNTIME_HOST / "plugins/dsh-host").resolve(), \
        "顶着我们包名的插件装不进来"
    with pytest.raises(tarfile.TarError):
        unpack_plugin(b"not a tarball", tmp_path / "cache" / "bad")


# ---- 端到端：真起共享宿主和专属宿主 ----

async def turn(kernel, session_id: str, text: str) -> None:
    await kernel.send(session_id, TurnInput(text=text))

    async def until_end() -> None:
        # 从头读：积压来自官方会话日志（宿主回收再起后也在），要等的是这句话之后的那个 turn/end
        sent = False
        async for frame in kernel.subscribe(session_id):
            event = frame.data or {} if frame.kind == "session/event" else {}
            sent = sent or (event.get("type") == "user/message" and text in json.dumps(event, ensure_ascii=False))
            if sent and event.get("type") == "turn/end":
                return

    await asyncio.wait_for(until_end(), timeout=60)


def spec(session_id: str, user: str, *, resume: bool = False) -> SessionSpec:
    return SessionSpec(session_id=session_id, resume=resume, owner=SessionOwner(workspace_id="ws1", user_id=user))


async def test_conversations_of_a_plugin_user_run_on_their_own_host(tmp_path):
    mock = MockLlmProcess(sequence=["success"], successText="好的")
    async with mock, HostProcess(model_base_url="http://127.0.0.1:1/unused", model_name="deepseek-chat",
                                 patch=OVERLAY, extra_env=mock.host_env()) as shared:
        launcher = ProcessLauncher(runtime_dir=RUNTIME_HOST, root=tmp_path, node_bin=str(NODE_BIN), patches=[OVERLAY],
                                   env={**mock.host_env(), "KNOWWORK_MODEL": "deepseek-chat",
                                        "KNOWWORK_MODEL_BASE_URL": "http://127.0.0.1:1/unused"})
        hosts = DedicatedHosts(launcher, prepare=no_prepare, idle_seconds=600)

        async def plugins_of(_workspace: str, user: str):
            return [host_plugin()] if user == "alice" else []

        kernel = RoutedKernel(HostKernel(f"http://127.0.0.1:{shared.port}", shared.token), hosts, MemoryPlacement(),
                              plugins_of)
        try:
            await kernel.open_session(spec("conv-alice", "alice"))
            await turn(kernel, "conv-alice", "第一句：我是 alice")
            await kernel.open_session(spec("conv-bob", "bob"))
            await turn(kernel, "conv-bob", "我是 bob")
            status = await hosts.status()
            assert [(s.user_id, s.plugins, s.sessions, s.busy) for s in status] == [("alice", 1, 1, 0)], \
                "只有 alice 有专属宿主；这一轮答完了，宿主报没有没停下的会话"

            # 回收 alice 的宿主：这段对话报「宿主上没有会话」；恢复打开时宿主再起，前面的历史接得上
            assert await hosts.sweep() == 0, "宿主自己报的空闲时长还没到"
            hosts._idle = 0
            assert await hosts.sweep() == 1 and await hosts.status() == ()
            with pytest.raises(SessionNotFound):
                await kernel.send("conv-alice", TurnInput(text="还在吗"))
            await kernel.open_session(spec("conv-alice", "alice", resume=True))
            await turn(kernel, "conv-alice", "第二句")
        finally:
            await kernel.aclose()
        requests = await mock.requests()

    def tools(text: str) -> set[str]:
        request = next(r for r in requests if text in json.dumps(r["body"]["messages"], ensure_ascii=False))
        return {tool["name"] for tool in request["body"]["tools"]}

    assert "demo_notes_echo" in tools("第一句：我是 alice")
    assert "demo_notes_echo" not in tools("我是 bob"), "共享宿主里没有上传的插件"
    last = json.dumps(requests[-1]["body"]["messages"], ensure_ascii=False)
    assert "第二句" in last and "第一句：我是 alice" in last, "宿主回收再起后，会话历史还在"
    assert "demo_notes_echo" in {tool["name"] for tool in requests[-1]["body"]["tools"]}


async def test_placement_is_stored_and_first_writer_wins(sessionmaker):
    from knowwork_server.hosts import StoredPlacement

    store = StoredPlacement(sessionmaker)
    assert not await store.decided("c-new") and await store.get("c-new") is None
    await store.put("c-new", "ws1", HostKey("ws1", "alice"))
    await store.put("c-new", "ws1", None)  # 后到的不覆盖
    assert await store.decided("c-new") and await store.get("c-new") == HostKey("ws1", "alice")
    await store.put("c-shared", "ws1", None)
    assert await store.decided("c-shared") and await store.get("c-shared") is None


async def test_routing_keeps_a_conversation_where_it_started():
    """去处在新开对话时定：之后这个人开了插件，老对话还在共享宿主；恢复打开的老对话（没记过去处）也在共享宿主。"""
    class Recorder:
        def __init__(self) -> None:
            self.opened: list[str] = []

        async def open_session(self, spec):
            self.opened.append(spec.session_id)
            return "epoch"

    shared = Recorder()
    launcher = FakeLauncher()
    hosts = DedicatedHosts(launcher, prepare=no_prepare)
    enabled = {"alice": False}

    async def plugins_of(_workspace, user):
        return [host_plugin()] if enabled.get(user) else []

    kernel = RoutedKernel(shared, hosts, MemoryPlacement(), plugins_of)
    await kernel.open_session(spec("c-old", "alice"))
    enabled["alice"] = True
    await kernel.open_session(spec("c-old", "alice", resume=True))
    await kernel.open_session(spec("c-legacy", "alice", resume=True))
    assert shared.opened == ["c-old", "c-old", "c-legacy"] and launcher.launched == []
    await kernel.open_session(SessionSpec(session_id="c-internal"))
    assert shared.opened[-1] == "c-internal", "没有归属的会话在共享宿主"
    await hosts.aclose()


def test_repack_moves_the_plugin_under_its_digest():
    from knowwork_server.hosts.docker_launcher import container_name, repack

    with tarfile.open(fileobj=io.BytesIO(repack(plugin_archive(), "d" * 64)), mode="r") as tar:
        names = tar.getnames()
        assert f"{'d' * 64}/package.json" in names and f"{'d' * 64}/index.mjs" in names
        assert all(member.isfile() or member.isdir() for member in tar)
    assert container_name(HostKey("ws", "alice")) == container_name(HostKey("ws", "alice"))
    assert container_name(HostKey("ws", "alice")) != container_name(HostKey("ws2", "alice"))
    assert "alice" not in container_name(HostKey("ws", "alice"))


@pytest.mark.skipif(not os.environ.get("KNOWWORK_TEST_PLUGIN_HOST_IMAGE"),
                    reason="要本机 Docker 和构建好的宿主镜像：KNOWWORK_TEST_PLUGIN_HOST_IMAGE=knowwork-runtime-host:dev")
async def test_a_plugin_host_container_starts_with_the_plugin_and_keeps_its_home(tmp_path):
    """真起容器（CI 不跑）：容器起得来、就绪、插件登记进了配置档；另一个业务服务进程（另一个启动器）找得到、接得过来，
    不会重复建；删掉再起，这个人的卷还在；定期清理删得掉卷。"""
    import docker

    from knowwork_server.hosts.docker_launcher import DockerLauncher, HostPosture, container_name
    from knowwork_server.sandbox.docker_engine import DockerEngine

    engine = DockerEngine(lambda: docker.from_env(timeout=30))
    client = docker.from_env()
    network = f"kw-test-host-link-{os.getpid()}"
    client.networks.create(network, internal=False)
    key = HostKey("ws-test", f"user-{os.getpid()}")
    launcher = DockerLauncher(engine, HostPosture(image=os.environ["KNOWWORK_TEST_PLUGIN_HOST_IMAGE"], network=network,
                                                  publish=True),
                              env={"KNOWWORK_MODEL": "default", "KNOWWORK_MODEL_BASE_URL": "http://127.0.0.1:1/v1"})
    try:
        started = asyncio.get_running_loop().time()
        launched = await launcher.launch(key, [host_plugin()])
        print(f"专属宿主容器启动到就绪 {asyncio.get_running_loop().time() - started:.1f} 秒")
        try:
            kernel = HostKernel(launched.base_url, launched.token)
            await kernel.open_session(spec("conv-in-container", key.user_id))
            await kernel.aclose()
            container = client.containers.get(container_name(key))
            logs = container.logs().decode(errors="replace")
            assert "did not activate" not in logs, logs[-1500:]
            manifest = await engine.read_file(container.id, "/data/dsh/profiles/knowwork-host/package.json",
                                              max_bytes=65536)
            assert "kw-demo-notes" in json.loads(manifest)["dsh"]["profile"]["bundles"]
            stats = container.stats(stream=False)
            print(f"容器内存 {stats['memory_stats'].get('usage', 0) / 1024 / 1024:.0f} MB")
            assert container.attrs["HostConfig"]["ReadonlyRootfs"] and container.attrs["HostConfig"]["CapDrop"] == ["ALL"]
            # 在这个人的主目录里留一个记号（会话日志、插件自己存的数据都在这个目录下）
            marker = io.BytesIO()
            with tarfile.open(fileobj=marker, mode="w") as tar:
                info = tarfile.TarInfo("kept.txt")
                info.size = 5
                tar.addfile(info, io.BytesIO(b"alive"))
            await engine.put_archive(container.id, "/data/dsh", marker.getvalue())

            # 另一个业务服务进程：从 Docker 找到这台、拿到令牌接过来；再去建同一个人的会撞上，改去接
            other = DockerLauncher(engine, launcher._posture, env={})
            found = await other.find(key)
            assert found is not None and found.base_url == launched.base_url and found.token == launched.token
            assert found.signature == launched.signature and found.plugins == 1
            assert [k for k, _ in await other.running()] == [key]
            adopted = HostKernel(found.base_url, found.token)
            await adopted.open_session(spec("conv-adopted", key.user_id))
            await adopted.aclose()
            with pytest.raises(LaunchRaced):
                await other.launch(key, [host_plugin()])
        finally:
            await launched.stop()
        assert not launched.alive()
        again = await launcher.launch(key, [])
        try:
            fresh = client.containers.get(container_name(key))
            assert fresh.id != container.id
            assert await engine.read_file(fresh.id, "/data/dsh/kept.txt", max_bytes=64) == b"alive", "容器删了，卷还在"
            bundles = json.loads(await engine.read_file(fresh.id, "/data/dsh/profiles/knowwork-host/package.json",
                                                        max_bytes=65536))["dsh"]["profile"]["bundles"]
            assert "kw-demo-notes" not in bundles, "这次没给插件：配置档按这次的重装，不留上次的"
        finally:
            await again.stop()
        assert await launcher.find(key) is None and await launcher.running() == []
        await launcher.remove_home(key)
        assert not [v for v in client.volumes.list() if v.name == container_name(key)], "定期清理删得掉卷"
    finally:
        await engine.aclose()
        with contextlib.suppress(Exception):
            client.volumes.get(container_name(key)).remove(force=True)
        with contextlib.suppress(Exception):
            client.networks.get(network).remove()


class FakeEngine:
    """容器起法要用到的 Docker 操作，只记调用；一个容器 kw-old 是升级前旧镜像起的、还在跑。"""

    def __init__(self, image: str) -> None:
        self.image, self.removed, self.created = image, [], []

    def _info(self, ref: str) -> dict | None:
        if ref in self.removed:
            return None
        return {"Id": ref, "State": {"Status": "running", "Running": True},
                "Config": {"Image": self.image, "Env": ["KNOWWORK_HOST_TOKEN=" + "t" * 64],
                           "Labels": {"com.knowwork.plugin-host": "1", "com.knowwork.plugin-host.workspace": "ws",
                                      "com.knowwork.plugin-host.user": "alice"}},
                "NetworkSettings": {"Networks": {"net": {"IPAddress": "10.0.0.2"}}}}

    async def inspect_container(self, ref):
        return self._info("kw-old" if ref.startswith("kw-plugin-host-") else ref)

    async def list_containers(self, labels):
        return [{"Id": "kw-old", "State": "running", "Image": self.image,
                 "Labels": self._info("kw-old")["Config"]["Labels"]}] if "kw-old" not in self.removed else []

    async def remove_container(self, container_id):
        self.removed.append(container_id)

    async def ensure_image(self, image):
        return None

    async def create_container(self, name, options):
        self.created.append(options["image"])
        raise RuntimeError("到这里就够了：没有被当成别人在用的宿主（LaunchRaced）")


async def test_hosts_from_an_older_image_are_not_adopted_but_replaced():
    from knowwork_server.hosts.docker_launcher import DockerLauncher, HostPosture

    engine = FakeEngine("registry/knowwork-runtime-host:v0.1.2")
    launcher = DockerLauncher(engine, HostPosture(image="registry/knowwork-runtime-host:v0.1.3", network="net"), env={})
    assert await launcher.running() == [] and engine.removed == ["kw-old"], "升级前的旧容器删掉，不算在跑的"
    engine.removed.clear()
    assert await launcher.find(HostKey("ws", "alice")) is None and engine.removed == ["kw-old"]
    engine.removed.clear()
    with pytest.raises(LaunchFailed):
        await launcher.launch(HostKey("ws", "alice"), [])
    assert engine.removed == ["kw-old"] and engine.created == ["registry/knowwork-runtime-host:v0.1.3"]
