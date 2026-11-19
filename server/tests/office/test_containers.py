"""按人起的 Office 容器：起、找回、按人隔离、上限、空闲回收、文件进出。

Docker 用内存里的假引擎（只实现通用容器管理与这里用到的几个方法），容器里的网页服务用 httpx 的模拟传输。
真起容器的联调放在部署那一步（镜像在 deploy/docker/office）。
"""

import asyncio
import hashlib
import io
import tarfile
import time
from collections import Counter
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from knowwork_server.office.containers import (
    COMPONENT,
    MCP_GATE_PORT,
    WEB_PORT,
    OfficeBusy,
    OfficeContainers,
    OfficeCopy,
    OfficeKey,
    OfficeSpec,
    endpoint_of,
    office_location,
    office_mount,
)
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.docker_engine import DockerConflict
from knowwork_server.sandbox.execution import RawExec
from knowwork_server.workpath import ROOT, parse

pytestmark = pytest.mark.anyio
ALICE = OfficeKey.of("ws1", "alice")
BOB = OfficeKey.of("ws1", "bob")


class FakeEngine:
    """够通用容器管理与编辑器容器池用的 Docker：容器、环境变量、网络地址、put_archive 放进去的文件。"""

    def __init__(self, *, late_ports: int = 0) -> None:
        self.late_ports = late_ports  # 刚起来的容器，前几次读容器信息时发布的端口还没出来
        self.containers: dict[str, dict[str, Any]] = {}
        self.files: dict[tuple[str, str], bytes] = {}
        self.mtimes: dict[tuple[str, str], float] = {}  # put_archive 放进去的文件的修改时间（取自包里的 mtime，同真 Docker）
        self.images: list[str] = []
        self.removed: list[str] = []
        self.counts: Counter[str] = Counter()  # 各个 Docker 调用调了几次
        self._seq = 0

    def add(self, name: str, labels: dict[str, str], *, status: str = "running", created: float = 1000.0,
            env: list[str] | None = None) -> dict[str, Any]:
        self._seq += 1
        container = {"Id": f"c{self._seq}", "Name": name, "Labels": dict(labels), "Status": status,
                     "Created": created, "Options": {"environment": env or [], "network": "kw-office"}}
        self.containers[name] = container
        return container

    def _find(self, ref: str) -> dict[str, Any] | None:
        return self.containers.get(ref) or next((c for c in self.containers.values() if c["Id"] == ref), None)

    async def inspect_container(self, ref: str) -> dict[str, Any] | None:
        self.counts["inspect_container"] += 1
        c = self._find(ref)
        if c is None:
            return None
        options = c["Options"]
        networks = {options["network"]: {"IPAddress": f"192.0.2.{c['Id'][1:]}"}} if options.get("network") else {}
        ports = {f"{port}/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(40000 + port % 1000)}]
                 for port in (WEB_PORT, MCP_GATE_PORT)} if options.get("ports") else {}
        if ports and c["Status"] == "running" and self.late_ports > 0:
            self.late_ports -= 1
            ports = {name: None for name in ports}
        return {"Id": c["Id"], "Config": {"Labels": c["Labels"], "Env": list(options.get("environment") or [])},
                "State": {"Status": c["Status"]}, "NetworkSettings": {"Networks": networks, "Ports": ports}}

    async def list_containers(self, labels: dict[str, str]) -> list[dict[str, Any]]:
        self.counts["list_containers"] += 1
        return [{"Id": c["Id"], "Labels": c["Labels"], "State": c["Status"], "Created": c["Created"]}
                for c in self.containers.values() if all(c["Labels"].get(k) == v for k, v in labels.items())]

    async def create_container(self, name: str, options: dict[str, Any]) -> str:
        if name in self.containers:
            raise DockerConflict("exists")
        container = self.add(name, options["labels"], status="created", created=2000.0)
        container["Options"] = options
        return container["Id"]

    async def start_container(self, container_id: str) -> None:
        self._find(container_id)["Status"] = "running"

    async def unpause_container(self, container_id: str) -> None:
        self._find(container_id)["Status"] = "running"

    async def remove_container(self, container_id: str) -> None:
        c = self._find(container_id)
        if c is not None:
            self.removed.append(c["Name"])
            del self.containers[c["Name"]]

    async def ensure_image(self, image: str) -> None:
        self.images.append(image)

    async def stat_path(self, container_id: str, path: str) -> dict[str, Any] | None:
        self.counts["stat_path"] += 1
        mtime = self.mtimes.get((container_id, path))
        if mtime is None:
            return None
        return {"name": path.rsplit("/", 1)[-1], "mtime": datetime.fromtimestamp(mtime, UTC).isoformat()}

    async def put_archive(self, container_id: str, path: str, tar_bytes: bytes) -> None:
        self.counts["put_archive"] += 1
        with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
            for member in tar.getmembers():
                extracted = tar.extractfile(member) if member.isfile() else None
                if extracted is not None:
                    self.files[(container_id, f"{path}/{member.name}")] = extracted.read()
                    self.mtimes[(container_id, f"{path}/{member.name}")] = float(member.mtime)

    async def read_file(self, container_id: str, path: str, *, max_bytes: int) -> bytes | None:
        data = self.files.get((container_id, path))
        if data is not None and len(data) > max_bytes:
            raise SandboxError("文件太大")
        return data


class Clock:
    def __init__(self, now: float = 10_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def spec(**overrides: Any) -> OfficeSpec:
    values: dict[str, Any] = {"image": "knowwork-office:test", "memory_bytes": 2 << 30, "shm_bytes": 512 << 20,
                              "nano_cpus": 2_000_000_000, "network": "kw-office", "idle_ttl_seconds": 1800}
    return OfficeSpec(**{**values, **overrides})


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def make():
    built: list[tuple[OfficeContainers, httpx.AsyncClient]] = []

    def factory(*, engine: FakeEngine | None = None, max_containers: int = 5, clock: Clock | None = None,
                ready: bool = True, refused: int = 0, run=None, **spec_overrides: Any):
        engine = engine or FakeEngine()
        hits: list[str] = []

        async def answer(request: httpx.Request) -> httpx.Response:
            hits.append(str(request.url))
            await asyncio.sleep(0.001)  # 像真的网络一样让出去：同时到的请求才会真的同时在等
            if not ready or len(hits) <= refused:  # refused：网页服务还没起来的前几次
                raise httpx.ConnectError("refused")
            return httpx.Response(401)  # 没带口令：网页服务答话了就算好

        http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
        editors = OfficeContainers(engine, spec(**spec_overrides), max_containers=max_containers, http=http,
                                author_of=_author, clock=clock or Clock(),
                                **({"run": run} if run else {}))
        built.append((editors, http))
        return engine, editors, hits

    yield factory
    for editors, http in built:
        await editors.aclose()
        await http.aclose()


async def _author(user_id: str) -> str:
    return {"alice": "张三"}.get(user_id, "")


async def test_first_open_creates_one_container_per_person_with_its_own_secrets(make):
    engine, editors, hits = make()
    found = await editors.endpoint(ALICE)
    (container,) = engine.containers.values()
    options = container["Options"]
    env = dict(item.split("=", 1) for item in options["environment"])
    assert container["Name"].startswith("kw-office-")
    assert container["Labels"]["io.knowwork.component"] == COMPONENT
    assert container["Labels"]["io.knowwork.user"] == "alice"
    assert env["KNOWWORK_OFFICE_AUTHOR"] == "张三"
    assert len(env["GENOFFICE_WEB_TOKEN"]) >= 32 and len(env["KNOWWORK_OFFICE_MCP_TOKEN"]) >= 32
    assert env["GENOFFICE_WEB_TOKEN"] != env["KNOWWORK_OFFICE_MCP_TOKEN"]
    assert options["shm_size"] == 512 << 20 and options["cap_drop"] == ["ALL"] and options["network"] == "kw-office"
    assert found.web_token == env["GENOFFICE_WEB_TOKEN"] and found.mcp_token == env["KNOWWORK_OFFICE_MCP_TOKEN"]
    assert found.web_url.endswith(f":{WEB_PORT}") and found.mcp_url.endswith(f":{MCP_GATE_PORT}/mcp")
    assert hits and hits[0].startswith(found.web_url)


async def test_same_person_reuses_the_container_and_others_get_their_own(make):
    engine, editors, _ = make()
    first = await editors.endpoint(ALICE)
    editors.forget(ALICE)  # 不走缓存，按名字找回
    again = await editors.endpoint(ALICE)
    other = await editors.endpoint(BOB)
    assert again == first
    assert other.web_token != first.web_token
    assert len(engine.containers) == 2
    # 换了空间是另一个编辑器：空间之间的文件不放在一起
    await editors.endpoint(OfficeKey.of("ws2", "alice"))
    assert len(engine.containers) == 3


async def test_stopped_container_is_started_again_and_changed_config_rebuilds(make):
    engine, editors, _ = make()
    await editors.endpoint(ALICE)
    (container,) = engine.containers.values()
    container["Status"] = "exited"
    editors.forget(ALICE)
    await editors.endpoint(ALICE)
    assert container["Status"] == "running" and not engine.removed

    _, upgraded, _ = make(engine=engine, image="knowwork-office:newer")
    await upgraded.endpoint(ALICE)
    assert engine.removed == [container["Name"]]
    (rebuilt,) = engine.containers.values()
    assert rebuilt["Options"]["image"] == "knowwork-office:newer"


async def test_container_with_our_name_but_someone_elses_labels_is_refused(make):
    engine, editors, _ = make()
    engine.add(spec().name(ALICE), {"io.knowwork.component": COMPONENT, "io.knowwork.user": "mallory"})
    with pytest.raises(SandboxError):
        await editors.endpoint(ALICE)


async def test_cap_counts_only_other_peoples_running_editors(make):
    engine, editors, _ = make(max_containers=1)
    await editors.endpoint(ALICE)
    await editors.endpoint(ALICE)  # 自己的不算
    with pytest.raises(OfficeBusy):
        await editors.endpoint(BOB)
    next(iter(engine.containers.values()))["Status"] = "exited"  # 停了的不占内存，不算
    await editors.endpoint(BOB)


async def settle() -> None:
    """等打开编辑器时顺带触发的后台清扫跑完，免得它的 Docker 调用算到后面。"""
    await asyncio.sleep(0.01)


async def test_a_running_editor_is_not_refused_when_others_fill_the_cap(make):
    """上限只拦新建：自己的编辑器已经在跑，别人的数到了上限（几个进程同时建会略超），刷新地址也不能说「满了」，
    不然正在用的人编辑器会突然断开。"""
    clock = Clock()
    engine, editors, _ = make(max_containers=1, clock=clock)
    await editors.endpoint(ALICE)
    engine.add("kw-office-bob", {"io.knowwork.component": COMPONENT, "io.knowwork.user": "bob"})
    clock.now += 31  # 地址缓存过期
    await editors.endpoint(ALICE)


async def test_refreshing_the_address_does_not_count_everyone_again(make):
    """地址过期了重新查：自己的编辑器在跑就不再把全部编辑器容器列一遍（那是新建前数人数用的）。"""
    clock = Clock()
    engine, editors, _ = make(clock=clock)
    await editors.endpoint(ALICE)
    await settle()
    listed = engine.counts["list_containers"]
    clock.now += 31
    await editors.endpoint(ALICE)
    assert engine.counts["list_containers"] == listed


async def test_requests_arriving_together_refresh_the_address_once(make):
    """页面一加载几十个请求同时到：地址只刷新一次（问一次 Docker、探一次编辑器），其余等它的结果。"""
    clock = Clock()
    _, editors, hits = make(clock=clock)
    await editors.endpoint(ALICE)
    clock.now += 31
    before = len(hits)
    found = await asyncio.gather(*(editors.endpoint(ALICE) for _ in range(10)))
    assert len(set(found)) == 1 and len(hits) == before + 1


async def test_agent_calls_reuse_the_known_address(make):
    """智能体每调一次编辑器工具都要它的 MCP 地址：刚查过就直接用，不再每次问两遍 Docker。"""
    engine, editors, _ = make()
    await editors.endpoint(ALICE)
    await settle()
    inspected = engine.counts["inspect_container"]
    for _ in range(3):
        assert await editors.mcp(ALICE) is not None
    assert engine.counts["inspect_container"] == inspected


async def test_idle_editors_are_swept_before_a_newcomer_is_turned_away(make):
    """到了上限先清扫一次空闲的再判：人走了的编辑器不能一直占着名额，把新来的人挡在外面。"""
    clock = Clock()
    engine, editors, _ = make(max_containers=1, clock=clock)
    await editors.endpoint(BOB)
    await settle()
    clock.now += 1801  # 李四 30 分钟没动静
    await editors.endpoint(ALICE)
    assert [c["Labels"]["io.knowwork.user"] for c in engine.containers.values()] == ["alice"]


async def test_idle_sweep_runs_on_a_timer(make):
    """清扫按时跑：人都走了、也没人再打开编辑器时，空闲的编辑器照样收掉（以前只在有人用编辑器时顺带跑）。"""
    _, editors, _ = make()
    triggered: list[int] = []
    editors.sweeper.trigger = lambda: triggered.append(1)
    editors.start(interval_seconds=0.01)
    await asyncio.sleep(0.05)
    assert len(triggered) >= 2


async def test_an_editor_that_comes_up_is_noticed_right_away(make):
    """冷启动时网页服务几秒后才答话：隔太久才问一次，起来了人还要白等（以前 0.5 秒一次，平均多等 0.25 秒、最多 0.5 秒）。"""
    _, editors, hits = make(refused=2)
    began = time.monotonic()
    await editors.endpoint(ALICE)
    assert len(hits) == 3
    assert time.monotonic() - began < 0.4


async def test_editor_that_never_answers_times_out(make, monkeypatch):
    monkeypatch.setattr("knowwork_server.office.containers.READY_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr("knowwork_server.office.containers.READY_POLL_SECONDS", 0.01)
    _, editors, _ = make(ready=False)
    with pytest.raises(SandboxError, match="超时"):
        await editors.endpoint(ALICE)


async def test_idle_sweep_counts_editor_traffic_and_gives_grace_after_restart(make):
    clock = Clock(10_000.0)
    engine, editors, _ = make(clock=clock)
    await editors.endpoint(ALICE)
    await editors.endpoint(BOB)
    clock.now += 1000
    editors.touch(ALICE)  # 张三还在编辑（WebSocket 消息），李四 1000 秒没动静
    await settle()  # 活动标记在后台写进容器
    clock.now += 1000
    assert await editors.sweeper.sweep() == 1
    assert [c["Labels"]["io.knowwork.user"] for c in engine.containers.values()] == ["alice"]

    # 重启后进程里没有活动记录：从重启时算起给满一段，不把正在用的当空闲
    _, restarted, _ = make(engine=engine, clock=clock)
    assert await restarted.sweeper.sweep() == 0
    clock.now += 1801
    assert await restarted.sweeper.sweep() == 1
    assert not engine.containers


async def test_an_address_that_shows_up_late_is_waited_for(make, monkeypatch):
    """刚起来的容器，读容器信息时发布的端口可能还没出来（本机 Docker Desktop 上遇到过：第一次打开报「地址或口令读不出来」，
    重开就好）：等一会儿再读，不让人白白打不开。"""
    monkeypatch.setattr("knowwork_server.office.containers.READY_POLL_SECONDS", 0.01)
    engine = FakeEngine(late_ports=2)
    _, editors, _ = make(engine=engine, network=None)
    found = await editors.endpoint(ALICE)
    assert found.web_url.startswith("http://127.0.0.1:")


async def test_published_ports_when_there_is_no_network(make):
    engine, editors, _ = make(network=None)
    found = await editors.endpoint(ALICE)
    (container,) = engine.containers.values()
    assert "network" not in container["Options"]
    assert container["Options"]["ports"] == {f"{WEB_PORT}/tcp": ("127.0.0.1", None),
                                             f"{MCP_GATE_PORT}/tcp": ("127.0.0.1", None)}
    assert found.web_url == f"http://127.0.0.1:{40000 + WEB_PORT % 1000}"


async def test_files_go_in_per_conversation_and_come_back(make):
    _, editors, _ = make()
    report = parse("output/报告.docx")
    target = await editors.place(ALICE, "conv-1", report, b"docx bytes")
    assert target == "/work/conv-1/output/报告.docx"  # 照搬对话里的目录：别的目录下的同名文件不会撞
    assert await editors.fetch(ALICE, "conv-1", report, max_bytes=100) == b"docx bytes"
    assert await editors.fetch(ALICE, "conv-1", parse("没有.docx"), max_bytes=100) is None
    # 工作区路径只经 parse 得到，越界的写法在那里就被拒；工作区根放不了文件，对话标识不对也不行
    with pytest.raises(SandboxError):
        await editors.place(ALICE, "conv-1", ROOT, b"x")
    with pytest.raises(SandboxError):
        await editors.place(ALICE, "../conv", parse("a.docx"), b"x")
    # 同一个人另一段对话、别人的编辑器里都没有这份
    assert await editors.fetch(ALICE, "conv-2", report, max_bytes=100) is None
    assert await editors.fetch(BOB, "conv-1", report, max_bytes=100) is None


async def test_fetch_does_not_start_a_container(make):
    engine, editors, _ = make()
    assert await editors.fetch(ALICE, "c", parse("a.docx"), max_bytes=10) is None
    assert not engine.containers


def record_of(engine: FakeEngine, user: str, conversation_id: str, path: str) -> str | None:
    """这个人容器里这份「和对话一致」的记录。"""
    for container in engine.containers.values():
        if container["Labels"].get("io.knowwork.user") == user:
            value = engine.files.get((container["Id"], f"/work/.knowwork/{conversation_id}/{path}.sha256"))
            return value.decode() if value is not None else None
    return None


async def test_the_sync_record_lives_in_the_persons_container(make):
    """「这份和对话里的版本一致」记在容器里：业务服务开几个进程都读得到，容器回收了也就跟着没了。"""
    engine, editors, _ = make()
    report = parse("output/报告.docx")
    await editors.place(ALICE, "conv-1", report, b"docx bytes")
    assert record_of(engine, "alice", "conv-1", "output/报告.docx") == hashlib.sha256(b"docx bytes").hexdigest()
    await editors.mark(ALICE, "conv-1", report, "abc")
    assert record_of(engine, "alice", "conv-1", "output/报告.docx") == "abc"
    assert record_of(engine, "alice", "conv-2", "output/报告.docx") is None
    assert record_of(engine, "bob", "conv-1", "output/报告.docx") is None
    assert all(not path.startswith("/work/conv-1/") or path == "/work/conv-1/output/报告.docx"
               for _, path in engine.files), "记录不放在对话的目录里"
    # 容器没了：不为记一笔新建
    await editors.mark(BOB, "conv-1", report, "abc")
    assert [c["Labels"]["io.knowwork.user"] for c in engine.containers.values()] == ["alice"]


def local_runner(engine: FakeEngine, root, calls: list):
    """在容器里跑命令的替身：假容器里放进去的文件落到本机目录，/work/... 换成那里，再用本机的 sh 真跑。"""

    async def run(docker, container_id, request):
        calls.append(request)
        base = root / container_id
        for (owner, path), data in engine.files.items():
            if owner == container_id:
                target = base / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
        argv = [str(base / arg.lstrip("/")) if arg.startswith("/work/") else arg for arg in request.argv]
        process = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                       stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await process.communicate()
        return RawExec(exit_code=process.returncode or 0, stdout=stdout, stderr=stderr, timed_out=False,
                       truncated=False)

    return run


async def test_copies_are_worked_out_in_the_container_without_moving_files(make, tmp_path):
    """编辑器里几份文件的摘要与各自的记录：在容器里一次算完（不像 fetch 那样整份取回），编辑器没在跑不为此新建。"""
    calls: list = []
    engine = FakeEngine()
    _, editors, _ = make(engine=engine, run=local_runner(engine, tmp_path, calls))
    report, sheet, missing = parse("output/报告.docx"), parse("数据.xlsx"), parse("没有.pptx")
    assert await editors.copies(ALICE, "conv-1", [report]) is None and not calls and not engine.containers
    await editors.place(ALICE, "conv-1", report, b"docx bytes")
    await editors.place(ALICE, "conv-1", sheet, b"xlsx bytes")
    await editors.mark(ALICE, "conv-1", sheet, "f" * 64)

    found = await editors.copies(ALICE, "conv-1", [report, sheet, missing])

    def sha(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    assert found == {"output/报告.docx": OfficeCopy(sha(b"docx bytes"), sha(b"docx bytes")),
                     "数据.xlsx": OfficeCopy(sha(b"xlsx bytes"), "f" * 64),
                     "没有.pptx": OfficeCopy(None, None)}
    assert len(calls) == 1 and calls[0].user == "office", "几份一起、用编辑器的账号跑一次"


async def test_reclaim_hands_the_editor_over_before_removing_it(make):
    """回收前把这个人编辑器的 MCP（带门卫口令）和读容器里文件的办法交出去存盘；不算一次活动，存完照样删。"""
    clock = Clock(10_000.0)
    engine, editors, _ = make(clock=clock)
    await editors.endpoint(ALICE)
    await editors.place(ALICE, "conv-1", parse("a.docx"), b"in editor")
    (container,) = engine.containers.values()
    env = dict(item.split("=", 1) for item in container["Options"]["environment"])
    seen = []

    async def before_reclaim(key, mcp, read):
        seen.append((key, mcp.url, mcp.headers, await read("/work/conv-1/a.docx", 100)))

    editors.before_reclaim = before_reclaim
    clock.now += 1801
    assert await editors.sweeper.sweep() == 1
    assert seen == [(ALICE, f"http://192.0.2.1:{MCP_GATE_PORT}/mcp",
                     {"authorization": f"Bearer {env['KNOWWORK_OFFICE_MCP_TOKEN']}"}, b"in editor")]
    assert not engine.containers


async def test_reclaim_skips_saving_for_a_container_that_is_not_running(make):
    clock = Clock(10_000.0)
    engine, editors, _ = make(clock=clock)
    await editors.endpoint(ALICE)
    next(iter(engine.containers.values()))["Status"] = "exited"
    called = []

    async def before_reclaim(key, mcp, read):
        called.append(key)

    editors.before_reclaim = before_reclaim
    clock.now += 1801
    assert await editors.sweeper.sweep() == 1
    assert called == [] and not engine.containers


def test_editor_paths_tell_their_conversation():
    assert office_location("/work/conv-1/output/报告.docx") == ("conv-1", parse("output/报告.docx"))
    for outside in ("/tmp/a.docx", "/work/conv-1", "/work/.knowwork/conv-1/a.docx.sha256", "/work/../etc/x",
                    "/workspace/conv-1/a.docx"):
        assert office_location(outside) is None, outside


def test_keys_reject_odd_ids_and_names_are_stable():
    with pytest.raises(SandboxError):
        OfficeKey.of("ws1", "../x")
    assert OfficeKey.from_labels({"io.knowwork.workspace": "ws1"}) is None
    assert spec().name(ALICE) == spec(image="other").name(ALICE) != spec().name(BOB)
    assert spec().digest != spec(memory_bytes=1 << 30).digest
    assert spec().digest == spec(idle_ttl_seconds=60).digest  # 改空闲时间不必重建
    assert office_mount("c").at(parse("output/a.docx")) == "/work/c/output/a.docx"


def test_endpoint_needs_a_token_and_an_address():
    with pytest.raises(SandboxError):
        endpoint_of({"Config": {"Env": []}, "NetworkSettings": {}}, "kw-office")


MARKER = "/work/.knowwork/.active"


def marker_of(engine: FakeEngine, user: str) -> float | None:
    found = [c for c in engine.containers.values() if c["Labels"].get("io.knowwork.user") == user]
    return engine.mtimes.get((found[0]["Id"], MARKER)) if found else None


async def test_use_is_recorded_in_the_container_for_the_other_processes(make):
    """api 与 worker（或多开的 api）各有一个容器池、各记各的活动：用过的同时记在容器里的标记文件上（同会话沙箱），
    别的进程清扫时照它算，不把这边正在用的当成空闲收掉。同一个人一分钟最多写一次，转发层每个请求都 touch 也不多花。"""
    clock = Clock(10_000.0)
    engine = FakeEngine()
    _, other, _ = make(engine=engine, clock=clock)  # 另一个进程：从没见过张三用
    _, here, _ = make(engine=engine, clock=clock)
    await here.endpoint(ALICE)
    await settle()
    assert marker_of(engine, "alice") == 10_000.0
    writes = engine.counts["put_archive"]
    clock.now += 30
    here.touch(ALICE)
    await settle()
    assert engine.counts["put_archive"] == writes, "一分钟内不再写"
    clock.now += 970
    here.touch(ALICE)  # 张三 1000 秒后还在编辑
    await settle()
    assert marker_of(engine, "alice") == 11_000.0

    clock.now += 1000  # 离上次用 1000 秒：另一个进程不该收
    assert await other.sweeper.sweep() == 0
    clock.now += 801  # 1801 秒没人用了
    assert await other.sweeper.sweep() == 1
    assert not engine.containers


async def test_a_marker_from_the_future_is_not_trusted(make):
    """标记时间在未来（时钟不准或被人改过）：不认它，照创建时间算，免得容器永远收不掉。"""
    clock = Clock(10_000.0)
    engine = FakeEngine()
    _, other, _ = make(engine=engine, clock=clock)
    _, editors, _ = make(engine=engine, clock=clock)
    await editors.endpoint(ALICE)
    await settle()
    (container,) = engine.containers.values()
    engine.mtimes[(container["Id"], MARKER)] = clock.now + 86_400
    clock.now += 1801
    assert await other.sweeper.sweep() == 1
