"""一人一个 Office 容器（镜像 knowwork-office：GenOffice 网页模式 + MCP 门卫，见 deploy/docker/office）。

按人起，不按对话：同一个人多开几份文档内存几乎不涨（实测空闲约 250MB），和桌面端一台电脑一个 GenOffice 子进程同一个形态。
扩展在这个容器上做在线编辑（浏览器经它的转发连进来、智能体经 MCP 改开着的文档）；格式转换不用它，仍在会话沙箱里。
同一个人的两段对话互相看不到：容器里按对话分目录，工具层只认「这段对话的那几份」。模型不在这里执行命令，所以不必像
会话沙箱那样一段对话一个容器。

起停、按名字找回、配置变了重建、空闲回收复用会话沙箱的容器管理（knowwork_server.sandbox 的 ManagedContainers、
IdleSweeper），只换规格：镜像、参数、按人的名字与标记；活动按用到这个容器的流量算（每次用 touch 一下）。

容器只在容器网络里（KNOWWORK_OFFICE_NETWORK）、只有业务服务连得到；不设网络时（本机开发）把两个端口发布到 127.0.0.1 的随机端口。
口令（网页编辑器的、MCP 门卫的）建容器时随机生成、只放在容器环境变量里，用时从容器信息读回，不另存。

每份文件「和对话里的版本一致」时的摘要也记在容器里（/work/.knowwork/<对话>/<路径>.sha256，放进去、存回成功时写）：
在线编辑据此分清一轮结束时是外面改了，还是编辑器里有没存进对话的。记在容器里而不是进程内存里，是因为业务服务会开
多个进程；容器回收了，记录跟着没了，正好对应容器里的文件也没了。
回收前（空闲清扫停、删容器之前）把这个人容器的 MCP 和读文件的办法交给 before_reclaim（在线编辑接上它，替人存盘）：
直接用容器信息，不经容器池（清扫持着这个人的锁），也不算一次活动。
空闲先停、停满保留期才删（keep_stopped_seconds，配置 KNOWWORK_OFFICE_KEEP_STOPPED_DAYS）：停着的不占内存，人回来再打开时
拉起来约 1 秒，新建要几秒到十秒（2026-11-24 本机实测 0.8 秒对 3.7 秒）。顺带的查看（智能体找 MCP、一轮结束查改动、取回文件、
记一致）不把停着的拉起来（revive=False）。删对话时清掉这段对话在编辑器里的目录（erase），停着的拉起来删完再停回去。
"""

import asyncio
import hashlib
import io
import json
import logging
import re
import secrets
import tarfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any

import httpx

from knowwork_server.sandbox import execution
from knowwork_server.sandbox.containers import LABEL_COMPONENT, LABEL_IDLE_TTL, LABEL_POSTURE, ManagedContainers
from knowwork_server.sandbox.contract import SandboxError
from knowwork_server.sandbox.docker_engine import DockerEngine
from knowwork_server.sandbox.execution import ExecRequest, RawExec
from knowwork_server.sandbox.sweeper import CLOCK_SKEW_SECONDS, IdleSweeper
from knowwork_server.tools.mcp.client import Endpoint
from knowwork_server.workpath import Mount, WorkPath

log = logging.getLogger(__name__)

COMPONENT = "office"
LABEL_WORKSPACE = "io.knowwork.workspace"
LABEL_USER = "io.knowwork.user"
WEB_PORT = 8931
MCP_GATE_PORT = 8932
WORK_DIR = "/work"
SPEC_VERSION = 1
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# 端点读回来后多久内直接用（转发层每个请求都要找上游，不能每次都问 Docker）
ENDPOINT_TTL_SECONDS = 30.0
READY_TIMEOUT_SECONDS = 60.0
# 等网页服务答话、等发布的端口出现时隔多久问一次：冷启动只有几秒，隔太久起来了人还要白等（以前 0.5 秒，平均多等 0.25 秒）
READY_POLL_SECONDS = 0.1
ERASE_TIMEOUT = 30  # 删一段对话在编辑器里的目录
# 提前起编辑器时给真要打开的人留的名额：别人在跑的到了「上限 − 预留」就不再提前起（上限 20 时留 4 个，至少留 1 个）
PREWARM_RESERVE_FRACTION = 0.2
# 到了人数上限时先等一次清扫最多这么久（回收前要替人存盘）；没跑完的接着在后台跑
CAPACITY_SWEEP_SECONDS = 15.0
# 刚起来的容器，地址最多等这么久出现在容器信息里
ADDRESS_TIMEOUT_SECONDS = 10.0
MCP_TIMEOUT = 120.0  # 一次编辑器调用：长文档整篇改写也在几秒内，留足余量
SYNC_DIR = ".knowwork"  # 文件根下放「和对话一致」记录的目录（不是合法的对话标识，不会和对话目录撞）
SYNC_MAX_BYTES = 128
# 最后一次用的时间也记在容器里（文件根下 .knowwork/.active 的修改时间，同会话沙箱的活动标记）：api、worker、多开的 api
# 各有一个容器池、各记各的，清扫时照它算，别的进程才不会把这边正在用的当成空闲收掉。同一个人每个进程一分钟最多写一次
ACTIVE_MARKER = f"{SYNC_DIR}/.active"
MARK_INTERVAL_SECONDS = 60.0
# 回收前存盘：一个人的编辑器最多花这么久（清扫一轮总共 2 分钟，几个人的要一起回收）；每次编辑器调用更短
RECLAIM_SECONDS = 45.0
RECLAIM_MCP_TIMEOUT = 20.0
OFFICE_USER = "office"  # 镜像里跑编辑器的账号（uid 1000），容器里的文件都归它
# 编辑器里几份文件此刻的摘要与各自「和对话一致」的记录，在容器里一次算完、不把文件搬出来。参数成对：（文件, 记录）；
# 每对输出一行「摘要 记录」，没有的写 -
COPIES_SCRIPT = r"""
while [ "$#" -ge 2 ]; do
  d=-; r=-
  if [ -f "$1" ]; then d=$(sha256sum < "$1" | cut -c1-64); fi
  if [ -f "$2" ]; then r=$(head -c 128 "$2" | tr -d ' \t\r\n'); fi
  printf '%s %s\n' "$d" "${r:--}"
  shift 2
done
"""
COPIES_TIMEOUT = 60.0  # 20 份 64MiB 的也在几秒内
SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# 回收前存盘：（谁的, 编辑器 MCP, 按真实路径读容器里的文件）
FileReader = Callable[[str, int], Awaitable[bytes | None]]
ReclaimHook = Callable[["OfficeKey", Endpoint, FileReader], Awaitable[None]]
Runner = Callable[[DockerEngine, str, ExecRequest], Awaitable[RawExec]]


@dataclass(frozen=True)
class OfficeCopy:
    """编辑器里的一份：此刻内容的摘要（不在编辑器里是 None）、上次和对话一致时的摘要（没记过是 None）。"""

    digest: str | None
    synced: str | None


def copies_of(output: bytes, paths: list[WorkPath]) -> dict[str, OfficeCopy]:
    """COPIES_SCRIPT 的输出 → 按工作区路径的 OfficeCopy；行数对不上、不像摘要的当没有。"""
    lines = output.decode("ascii", "replace").splitlines()
    if len(lines) != len(paths):
        raise SandboxError("编辑器里的文件读不出来")

    def value(raw: str) -> str | None:
        return raw if SHA256_HEX.match(raw) else None

    found: dict[str, OfficeCopy] = {}
    for path, line in zip(paths, lines, strict=True):
        digest, _, synced = line.partition(" ")
        found[path.relative] = OfficeCopy(digest=value(digest), synced=value(synced.strip()))
    return found


class OfficeBusy(Exception):
    """同时运行的编辑器到了管理员设的上限。"""


@dataclass(frozen=True)
class OfficeKey:
    """谁的编辑器：（工作空间, 人）。author 是建容器时写进去的修订作者名，不算身份。"""

    workspace_id: str
    user_id: str
    author: str = field(default="", compare=False)

    @classmethod
    def of(cls, workspace_id: str, user_id: str, author: str = "") -> "OfficeKey":
        if not (ID_PATTERN.match(workspace_id or "") and ID_PATTERN.match(user_id or "")):
            raise SandboxError("编辑器标识不合法")
        return cls(workspace_id, user_id, author[:100])

    @classmethod
    def from_labels(cls, labels: dict[str, str] | None) -> "OfficeKey | None":
        labels = labels or {}
        try:
            return cls.of(labels.get(LABEL_WORKSPACE, ""), labels.get(LABEL_USER, ""))
        except SandboxError:
            return None


@dataclass(frozen=True)
class OfficeEndpoint:
    """一个人的编辑器：网页编辑器的地址与口令（给转发层），MCP 门卫的地址与口令（给智能体改文档）。"""

    web_url: str
    web_token: str
    mcp_url: str
    mcp_token: str


@dataclass(frozen=True)
class OfficeSpec:
    """Office 编辑器容器长什么样；摘要写在标记上，对不上就重建（容器里的文件不保留，原件在业务服务的存储里）。"""

    image: str
    memory_bytes: int
    shm_bytes: int
    nano_cpus: int
    network: str | None
    idle_ttl_seconds: int
    pids_limit: int = 1024
    noun: str = "编辑器"

    @property
    def digest(self) -> str:
        fields = {key: value for key, value in asdict(self).items() if key not in ("noun", "idle_ttl_seconds")}
        payload = json.dumps({"v": SPEC_VERSION, **fields}, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:32]

    def name(self, key: OfficeKey) -> str:
        digest = hashlib.sha256(f"{key.workspace_id}\x00{key.user_id}".encode()).hexdigest()[:32]
        return f"kw-office-{digest}"

    def labels(self, key: OfficeKey) -> dict[str, str]:
        return {LABEL_COMPONENT: COMPONENT, LABEL_WORKSPACE: key.workspace_id, LABEL_USER: key.user_id}

    def options(self, key: OfficeKey) -> dict[str, Any]:
        """docker-py containers.create 的参数。入口用镜像自己的（tini → entrypoint.sh），账号是镜像里的 office（uid 1000）。"""
        options: dict[str, Any] = {
            "image": self.image,
            "environment": [f"GENOFFICE_WEB_TOKEN={secrets.token_urlsafe(32)}",
                            f"KNOWWORK_OFFICE_MCP_TOKEN={secrets.token_urlsafe(32)}",
                            f"KNOWWORK_OFFICE_AUTHOR={key.author}"],
            "labels": {**self.labels(key), LABEL_POSTURE: self.digest, LABEL_IDLE_TTL: str(self.idle_ttl_seconds)},
            "cap_drop": ["ALL"], "security_opt": ["no-new-privileges"],
            # Chromium 用 /dev/shm，Docker 默认 64MB，转 PDF 时另起的实例会把它用满后崩溃（第 0 步实测）
            "shm_size": self.shm_bytes,
            "mem_limit": self.memory_bytes, "memswap_limit": self.memory_bytes,
            "nano_cpus": self.nano_cpus, "pids_limit": self.pids_limit, "restart_policy": {"Name": "no"},
        }
        if self.network:
            options["network"] = self.network
        else:
            # 本机开发：业务服务在宿主上，连不进容器网络，两个端口发布到 127.0.0.1 的随机端口
            options["ports"] = {f"{WEB_PORT}/tcp": ("127.0.0.1", None), f"{MCP_GATE_PORT}/tcp": ("127.0.0.1", None)}
        return options


def _env(info: dict[str, Any]) -> dict[str, str]:
    pairs = ((item.split("=", 1) + [""])[:2] for item in (info.get("Config") or {}).get("Env") or [])
    return {name: value for name, value in pairs}


def endpoint_of(info: dict[str, Any], network: str | None) -> OfficeEndpoint:
    """从容器信息读出两个端口的地址与口令。"""
    env = _env(info)
    settings = info.get("NetworkSettings") or {}
    if network:
        address = ((settings.get("Networks") or {}).get(network) or {}).get("IPAddress") or ""
        web, mcp = f"http://{address}:{WEB_PORT}", f"http://{address}:{MCP_GATE_PORT}"
    else:
        ports = settings.get("Ports") or {}

        def published(port: int) -> str:
            binding = (ports.get(f"{port}/tcp") or [{}])[0]
            return f"http://127.0.0.1:{binding.get('HostPort', '')}"

        web, mcp = published(WEB_PORT), published(MCP_GATE_PORT)
    if not env.get("GENOFFICE_WEB_TOKEN") or web.endswith(":") or "//:" in web:
        raise SandboxError("编辑器容器的地址或口令读不出来")
    return OfficeEndpoint(web_url=web, web_token=env["GENOFFICE_WEB_TOKEN"], mcp_url=f"{mcp}/mcp",
                          mcp_token=env.get("KNOWWORK_OFFICE_MCP_TOKEN", ""))


def office_mount(conversation_id: str) -> Mount:
    """Office 容器里这段对话的工作区挂在哪：按对话分目录，同一个人的两段对话各放各的；里面照搬对话工作区的目录，
    两个目录下的同名文件不会撞。"""
    if not ID_PATTERN.match(conversation_id or ""):
        raise SandboxError("对话标识不合法")
    return Mount(f"{WORK_DIR}/{conversation_id}")


def sync_record(conversation_id: str, path: WorkPath) -> str:
    """这份文件「和对话一致」记录的位置，相对文件根（容器的 /work，本机开发的 files_root）。"""
    office_mount(conversation_id)  # 对话标识照同一个规矩校验
    if path.is_root:
        raise SandboxError("文件路径不对")
    return f"{SYNC_DIR}/{conversation_id}/{path.relative}.sha256"


def sync_value(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def office_location(physical: str) -> tuple[str, WorkPath] | None:
    """编辑器里的真实路径是哪段对话的哪份文件（回收前存盘用）；不在某段对话目录里的（记录、编辑器外面的）是 None。"""
    prefix = f"{WORK_DIR}/"
    if not isinstance(physical, str) or not physical.startswith(prefix):
        return None
    conversation_id = physical[len(prefix):].split("/", 1)[0]
    try:
        mount = office_mount(conversation_id)
    except SandboxError:
        return None
    if not mount.holds(physical):
        return None
    path = mount.of(physical)
    return None if path.is_root else (conversation_id, path)


def _tar(files: dict[str, bytes], *, mtime: float | None = None) -> bytes:
    """装几个文件的 tar（带上级目录，put_archive 要求目标目录已存在，放到 /work 下自己建）。
    文件的修改时间取 mtime（活动标记靠它；Docker 展开时照包里的时间设），不给就是现在。"""
    buffer = io.BytesIO()
    folders: set[str] = set()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        for relative, data in files.items():
            parts = relative.split("/")
            for depth in range(1, len(parts)):
                name = "/".join(parts[:depth])
                if name in folders:
                    continue
                folders.add(name)
                folder = tarfile.TarInfo(name)
                folder.type, folder.mode, folder.uid, folder.gid = tarfile.DIRTYPE, 0o755, 1000, 1000
                tar.addfile(folder)
            member = tarfile.TarInfo(relative)
            member.size, member.mode, member.uid, member.gid = len(data), 0o644, 1000, 1000
            member.mtime = int(time.time() if mtime is None else mtime)
            tar.addfile(member, io.BytesIO(data))
    return buffer.getvalue()


class OfficeContainers:
    """按人起的编辑器容器：endpoint() 拿到（必要时建好、等它能答话），touch() 记活动，place()/fetch() 搬文件。"""

    def __init__(self, engine: DockerEngine, spec: OfficeSpec, *, max_containers: int, http: httpx.AsyncClient,
                 author_of: Callable[[str], Awaitable[str]] | None = None,
                 clock: Callable[[], float] = time.time, sweep_interval_seconds: float = 60.0,
                 run: Runner = execution.run, prewarm: bool = True,
                 keep_stopped_seconds: int | None = None) -> None:
        self._engine = engine
        self._prewarm = prewarm
        self._run = run
        self._spec = spec
        self._max = max_containers
        self._http = http
        self._author_of = author_of  # 用户 id → 修订作者名，建容器时写进去
        self._clock = clock
        self._containers: ManagedContainers[OfficeKey] = ManagedContainers(engine, spec)
        # 活动只记在进程里：重启后还没人来过的容器，从重启时算起给满一段空闲时间，不把别人正在用的当成空闲收掉
        self._started = clock()
        self._seen: dict[OfficeKey, float] = {}
        self._marked: dict[OfficeKey, float] = {}  # 上次往容器里写活动标记的时间（每个进程一分钟最多写一次）
        self._cached: dict[OfficeKey, tuple[float, OfficeEndpoint]] = {}
        # 正在刷新的地址：过期后同时到的请求（页面一加载几十个）只刷新一次
        self._refreshing: dict[OfficeKey, asyncio.Future[OfficeEndpoint]] = {}
        self._background: set[asyncio.Future] = set()
        self._timer: asyncio.Task | None = None
        self._sweep_interval = sweep_interval_seconds
        # 回收前存盘（扩展装配时接上 sync.py 的 save_before_reclaim）；没接就直接回收
        self.before_reclaim: ReclaimHook | None = None
        self.sweeper = IdleSweeper(engine, self._containers, default_ttl_seconds=spec.idle_ttl_seconds,
                                   min_interval_seconds=sweep_interval_seconds, clock=clock,
                                   labels={LABEL_COMPONENT: COMPONENT}, key_of=OfficeKey.from_labels,
                                   activity=self._activity, before_remove=self._before_remove,
                                   keep_stopped_seconds=keep_stopped_seconds, name="office-idle-sweep")
        self._containers.on_sweep = self.sweeper.trigger

    async def _before_remove(self, item: dict[str, Any]) -> None:
        """空闲要删了：在跑的编辑器交给 before_reclaim 存盘（清扫持着锁，这里不经容器池、不记活动）。"""
        key = OfficeKey.from_labels(item.get("Labels"))
        if key is not None:
            self.forget(key)  # 要停或删了：缓存的地址不再能用（拉起来地址可能变）
        if self.before_reclaim is None or key is None or str(item.get("State") or "").lower() != "running":
            return
        container_id = str(item["Id"])
        found = await self._address(container_id)
        mcp = Endpoint(found.mcp_url, {"authorization": f"Bearer {found.mcp_token}"},
                       timeout_seconds=RECLAIM_MCP_TIMEOUT)

        async def read(physical: str, max_bytes: int) -> bytes | None:
            return await self._engine.read_file(container_id, physical, max_bytes=max_bytes)

        await asyncio.wait_for(self.before_reclaim(key, mcp, read), RECLAIM_SECONDS)

    def touch(self, key: OfficeKey) -> None:
        """这个人的 Office 有动静（在线编辑转发层的每个请求、智能体每次调用）：空闲清扫按它算。
        已知容器在跑（有缓存的地址）时顺带在后台把活动标记写进容器；第一次找到或建好容器时在 _refresh 里写。"""
        now = self._clock()
        self._seen[key] = now
        if key in self._cached and self._mark_due(key, now):
            self._keep(asyncio.ensure_future(self._mark_later(key, now)))

    def _mark_due(self, key: OfficeKey, now: float) -> bool:
        last = self._marked.get(key)
        if last is not None and now - last < MARK_INTERVAL_SECONDS:
            return False
        self._marked[key] = now
        return True

    def _unmark(self, key: OfficeKey, at: float) -> None:
        """没写成：下次有动静再写。"""
        if self._marked.get(key) == at:
            del self._marked[key]

    async def _mark_later(self, key: OfficeKey, at: float) -> None:
        try:
            async with self._containers.using(key, create=False, revive=False) as container_id:
                if container_id is None:
                    self._unmark(key, at)
                    return
                await self._engine.put_archive(str(container_id), WORK_DIR, _tar({ACTIVE_MARKER: b""}, mtime=at))
        except SandboxError:
            log.debug("office activity marker not written", exc_info=True)
            self._unmark(key, at)

    async def _mark_now(self, key: OfficeKey, container_id: str) -> None:
        now = self._clock()
        if not self._mark_due(key, now):
            return
        try:
            await self._engine.put_archive(container_id, WORK_DIR, _tar({ACTIVE_MARKER: b""}, mtime=now))
        except SandboxError:
            log.debug("office activity marker not written", exc_info=True)
            self._unmark(key, now)

    def _with_marker(self, key: OfficeKey, files: dict[str, bytes]) -> tuple[dict[str, bytes], float]:
        """放文件时把活动标记并进同一个包（不多一次 Docker 调用）；到时候才带上。返回（要放的文件, 包里的时间）。"""
        now = self._clock()
        return ({**files, ACTIVE_MARKER: b""} if self._mark_due(key, now) else files), now

    async def _activity(self, item: dict[str, Any]) -> float | None:
        """最后一次用：本进程记的与容器里的活动标记，取晚的；都没有时按创建时间，重启后也至少从重启时算起
        （给满一段空闲时间，不把别人正在用的当成空闲收掉）。"""
        key = OfficeKey.from_labels(item.get("Labels"))
        seen = self._seen.get(key) if key is not None else None
        marked = await self._marker_time(str(item["Id"]))
        known = [moment for moment in (seen, marked) if moment is not None]
        if known:
            return max(known)
        created = item.get("Created")
        created = float(created) if isinstance(created, int | float) and created > 0 else 0.0
        return max(created, self._started)

    async def _marker_time(self, container_id: str) -> float | None:
        stat = await self._engine.stat_path(container_id, f"{WORK_DIR}/{ACTIVE_MARKER}")
        raw = (stat or {}).get("mtime")
        if not isinstance(raw, str) or not raw:
            return None
        try:
            moment = datetime.fromisoformat(raw).timestamp()
        except ValueError:
            return None
        if moment > self._clock() + CLOCK_SKEW_SECONDS:  # 时钟不准或被改过：不认，免得容器永远收不掉
            log.warning("office activity marker is in the future, falling back to the creation time")
            return None
        return moment

    async def endpoint(self, key: OfficeKey) -> OfficeEndpoint:
        """这个人的编辑器；没有就建（新建前同时运行的到了上限抛 OfficeBusy），等网页服务能答话再给。
        地址缓存 ENDPOINT_TTL_SECONDS（转发层每个请求都要找上游）；过期后同时到的请求只刷新一次。"""
        self.touch(key)
        cached = self._fresh(key)
        if cached is not None:
            return cached
        flight = self._refreshing.get(key)
        if flight is None:
            flight = asyncio.ensure_future(self._refresh(key))
            self._refreshing[key] = flight
            flight.add_done_callback(lambda done: self._landed(key, done))
        return await asyncio.shield(flight)

    def _fresh(self, key: OfficeKey) -> OfficeEndpoint | None:
        cached = self._cached.get(key)
        return cached[1] if cached is not None and self._clock() - cached[0] < ENDPOINT_TTL_SECONDS else None

    def _landed(self, key: OfficeKey, flight: asyncio.Future) -> None:
        if self._refreshing.get(key) is flight:
            del self._refreshing[key]
        if not flight.cancelled():
            flight.exception()  # 等它的请求都走了也不报「异常没人取」：各个请求自己已经拿到了

    async def _refresh(self, key: OfficeKey) -> OfficeEndpoint:
        info = await self._engine.inspect_container(self._spec.name(key))
        if not self._running_as_configured(info):
            # 上限只拦新建（或拉起停了的、按新配置重建的）：自己的已经在跑，别人的数到了上限也照常用
            await self._check_capacity(key)
            if not key.author and self._author_of is not None:
                key = replace(key, author=(await self._author_of(key.user_id))[:100])
        async with self._containers.using(key) as container_id:
            found = await self._address(str(container_id))
            await self._mark_now(key, str(container_id))
        await self._wait_ready(found)
        self._cached[key] = (self._clock(), found)
        return found

    async def _address(self, container_id: str) -> OfficeEndpoint:
        """容器信息里的地址与口令。刚起来的容器，发布的端口可能还没出现在容器信息里（本机 Docker Desktop 上遇到过：
        第一次打开报「地址或口令读不出来」，重开就好），等一会儿再读；一直读不出来把读到的记进日志（不含口令）。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ADDRESS_TIMEOUT_SECONDS
        while True:
            info = await self._engine.inspect_container(container_id) or {}
            try:
                return endpoint_of(info, self._spec.network)
            except SandboxError:
                if loop.time() >= deadline:
                    settings = info.get("NetworkSettings") or {}
                    log.warning("office editor %s has no usable address: ports=%s networks=%s", container_id[:12],
                                settings.get("Ports"), list((settings.get("Networks") or {}).keys()))
                    raise
            await asyncio.sleep(READY_POLL_SECONDS)

    def _running_as_configured(self, info: dict[str, Any] | None) -> bool:
        if info is None:
            return False
        status = str((info.get("State") or {}).get("Status") or "").lower()
        labels = (info.get("Config") or {}).get("Labels") or {}
        return status == "running" and labels.get(LABEL_POSTURE) == self._spec.digest

    def forget(self, key: OfficeKey) -> None:
        """转发失败（容器被回收、重建过）：下次重新读地址与口令。"""
        self._cached.pop(key, None)

    async def _check_capacity(self, key: OfficeKey) -> None:
        """只数别人在跑的（停了的不占内存）；到了上限先清扫一次空闲的再数（人走了的编辑器不能一直占着名额），还满才拒绝。
        几个进程同时建会略超一点，这是给管理员控资源的软上限。"""
        if await self._others_running(key) < self._max:
            return
        await self._sweep_now()
        if await self._others_running(key) >= self._max:
            raise OfficeBusy(f"同时在用编辑器的人数到了上限（{self._max}），请稍后再试")

    async def _others_running(self, key: OfficeKey) -> int:
        listed = await self._engine.list_containers({LABEL_COMPONENT: COMPONENT})
        mine = self._spec.labels(key)
        return sum(1 for item in listed if str(item.get("State") or "").lower() in ("running", "created", "restarting")
                   and any((item.get("Labels") or {}).get(name) != value for name, value in mine.items()))

    async def _sweep_now(self) -> None:
        """等一次清扫，最多 CAPACITY_SWEEP_SECONDS；没跑完的接着在后台跑，不中途打断（回收前在替人存盘）。"""
        sweep = asyncio.ensure_future(self.sweeper.sweep())
        self._keep(sweep)
        await asyncio.wait({sweep}, timeout=CAPACITY_SWEEP_SECONDS)

    def _keep(self, task: asyncio.Future) -> None:
        self._background.add(task)

        def settled(done: asyncio.Future) -> None:
            self._background.discard(done)
            if not done.cancelled() and done.exception() is not None:
                log.warning("office idle sweep failed", exc_info=done.exception())

        task.add_done_callback(settled)

    def start(self, *, interval_seconds: float | None = None) -> None:
        """定时清扫：以前只在有人用编辑器时顺带跑，人都走了、也没人再打开编辑器时，空闲的一直占着内存和名额。"""
        if self._timer is None:
            self._timer = asyncio.create_task(self._tick(interval_seconds or self._sweep_interval),
                                              name="office-idle-timer")

    async def _tick(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            self.sweeper.trigger()

    async def _wait_ready(self, found: OfficeEndpoint) -> None:
        """容器起来约 10 秒（第 0 步实测）：网页服务答话了（没带口令回 401 也算）才算好。"""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + READY_TIMEOUT_SECONDS
        while True:
            try:
                await self._http.get(f"{found.web_url}/", timeout=2.0)
                return
            except httpx.HTTPError:
                if loop.time() >= deadline:
                    raise SandboxError("编辑器启动超时，请稍后重试") from None
                await asyncio.sleep(READY_POLL_SECONDS)

    def mount(self, conversation_id: str) -> Mount:
        return office_mount(conversation_id)

    async def mcp(self, key: OfficeKey) -> Endpoint | None:
        """这个人编辑器的 MCP（经容器里的门卫，带口令）；编辑器没在跑是 None，不为此起一个。"""
        found = self._fresh(key)  # 刚查过就直接用：智能体一轮几十次调用，不每次问 Docker
        if found is None:
            async with self._containers.using(key, create=False, revive=False) as container_id:
                if container_id is None:
                    return None
                found = await self._address(str(container_id))
        self.touch(key)
        return Endpoint(found.mcp_url, {"authorization": f"Bearer {found.mcp_token}"}, timeout_seconds=MCP_TIMEOUT)

    async def ready(self, key: OfficeKey) -> None:
        """打开文件前：编辑器建好、能答话（同时在用的到了上限抛 OfficeBusy）。"""
        await self.endpoint(key)

    async def prewarm(self, key: OfficeKey) -> bool:
        """提前起这个人的编辑器：页面上出现了能在线编辑的文件，他很可能要点开，点开时就不用再等编辑器起来（冷启动要几秒
        到十秒）。起来以后和打开过的一样按空闲回收。关着（KNOWWORK_OFFICE_PREWARM=false）不起；已经在跑只算一次使用；
        要新起时，别人在跑的到了「上限 − 预留」就不起，也不为它先清扫一遍——提前起的不跟真要打开的人抢名额。
        返回这个人的编辑器现在是不是在跑。"""
        if not self._prewarm:
            return False
        if self._fresh(key) is None:
            info = await self._engine.inspect_container(self._spec.name(key))
            reserve = max(1, int(self._max * PREWARM_RESERVE_FRACTION))
            if not self._running_as_configured(info) and await self._others_running(key) >= self._max - reserve:
                return False
        await self.endpoint(key)
        return True

    async def place(self, key: OfficeKey, conversation_id: str, path: WorkPath, data: bytes) -> str:
        """把文件放进这个人的编辑器容器（按对话分目录），连同「和对话一致」的记录，返回编辑器里的路径。"""
        target = office_mount(conversation_id).at(path)
        record = sync_record(conversation_id, path)
        files, now = self._with_marker(key, {target[len(WORK_DIR) + 1:]: data, record: sync_value(data).encode()})
        async with self._containers.using(key) as container_id:
            # 包相对 /work 展开：上级目录跟着建出来（put_archive 要求目标目录已经存在）
            await self._engine.put_archive(str(container_id), WORK_DIR, _tar(files, mtime=now))
        self.touch(key)
        return target

    async def mark(self, key: OfficeKey, conversation_id: str, path: WorkPath, value: str) -> None:
        """记下这份此刻和对话里的版本一致（存回成功之后）；容器已经没了就不记，不为此新建。"""
        record = sync_record(conversation_id, path)
        async with self._containers.using(key, create=False, revive=False) as container_id:
            if container_id is not None:
                await self._engine.put_archive(str(container_id), WORK_DIR, _tar({record: value.encode()}))

    async def copies(self, key: OfficeKey, conversation_id: str,
                     paths: list[WorkPath]) -> dict[str, OfficeCopy] | None:
        """这几份在编辑器里的样子（摘要与记录），在容器里一次算完、不搬文件；编辑器没在跑是 None，不为此新建。"""
        mount = office_mount(conversation_id)
        args = [item for path in paths for item in (mount.at(path), f"{WORK_DIR}/{sync_record(conversation_id, path)}")]
        async with self._containers.using(key, create=False, revive=False) as container_id:
            if container_id is None:
                return None
            request = ExecRequest(argv=("sh", "-c", COPIES_SCRIPT, "kw", *args), user=OFFICE_USER, workdir=WORK_DIR,
                                  timeout_seconds=COPIES_TIMEOUT, max_output_bytes=200 * max(1, len(paths)))
            result = await self._run(self._engine, str(container_id), request)
        if result.exit_code != 0 or result.timed_out or result.truncated:
            raise SandboxError("编辑器里的文件读不出来")
        return copies_of(result.stdout, paths)

    async def fetch(self, key: OfficeKey, conversation_id: str, path: WorkPath, *, max_bytes: int) -> bytes | None:
        """取回编辑器里的文件（存盘后存成新的一版用）；容器已经没了或文件不在是 None。不为此新建容器。"""
        target = office_mount(conversation_id).at(path)
        async with self._containers.using(key, create=False, revive=False) as container_id:
            if container_id is None:
                return None
            return await self._engine.read_file(str(container_id), target, max_bytes=max_bytes)

    async def erase(self, key: OfficeKey, conversation_id: str) -> None:
        """删对话时清掉这个人编辑器里这段对话的目录与一致记录。没有编辑器就没事；停着的拉起来删，删完再停回去（这期间有人
        用起来就不停）。删不掉抛 SandboxError：对话留着可以再删，不留下对话删了、文件还在编辑器里的情况。"""
        mount = office_mount(conversation_id)
        info = await self._engine.inspect_container(self._spec.name(key))
        if info is None:
            return
        was_running = self._running_as_configured(info)
        began = self._clock()
        async with self._containers.using(key, create=False) as container_id:
            if container_id is None:
                return
            request = ExecRequest(argv=("rm", "-rf", "--", mount.root, f"{WORK_DIR}/{SYNC_DIR}/{conversation_id}"),
                                  user=OFFICE_USER, workdir=WORK_DIR, timeout_seconds=ERASE_TIMEOUT,
                                  max_output_bytes=4096)
            result = await self._run(self._engine, str(container_id), request)
            if result.exit_code != 0 or result.timed_out:
                raise SandboxError("编辑器里这段对话的文件没能删掉，请稍后重试")
            if not was_running and self._seen.get(key, 0.0) < began:
                self.forget(key)
                await self._engine.stop_container(str(container_id))

    async def aclose(self) -> None:
        for task in [self._timer, *self._background]:
            if task is not None:
                task.cancel()
        await asyncio.gather(*(t for t in [self._timer, *self._background] if t is not None), return_exceptions=True)
        await self.sweeper.aclose()
