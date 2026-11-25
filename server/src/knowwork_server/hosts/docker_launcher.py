"""部署用：一个人的专属宿主是一个容器（和共享宿主同一个镜像）。

- 容器只连得到宿主那张内部网络（模型网关、业务服务的宿主回调入口）；数据库、对象存储不在那张网络上；
- 里面没有服务器的密钥：宿主令牌是这一个容器现生成的，模型令牌是业务服务建好以后推给它的短期令牌；
- 镜像只读、非 root、去掉全部系统能力、限内存与进程数，照共享宿主和会话沙箱的姿态；
- 每人一个具名卷当 DSH_HOME（会话日志、插件写在主目录里的数据）：容器回收后卷留着，下次再起接得上；
- 插件在容器启动前拷进去（/plugins/<摘要>/，匿名卷，随容器删），入口脚本把它们登记进配置档；
- 试装（ephemeral）起的容器不挂这个人的卷，主目录也用匿名卷，删容器时一起删；
- Docker 就是「现在有哪些专属宿主」的登记处：容器名按「空间 + 人」算，标签记着属主和装了哪些插件，宿主令牌在容器的
  环境变量里。几个业务服务进程都从这里找、接过别人建的宿主（find、running），不各建各的；两个进程同时建同一个人的，
  后到的撞上重名（LaunchRaced）改去接先到的那个。业务服务退出时不停宿主，重启后接着用；
- 升级以后镜像变了：旧镜像起的容器不接，当成退出了的删掉重建（卷还在），新旧版本的宿主不混用。
  先这样做：每个容器各拷一份，磁盘上不共用；以后量大了再换成宿主节点上按摘要共用的只读缓存。
"""

import asyncio
import contextlib
import hashlib
import io
import logging
import secrets
import tarfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import docker.types
import httpx

from ..sandbox.docker_engine import DockerConflict
from .launcher import HostKey, HostPlugin, Launched, LaunchFailed, LaunchRaced, signature_of

log = logging.getLogger(__name__)

LABEL = "com.knowwork.plugin-host"
PORT = 7300
PLUGIN_ROOT = "/plugins"
READY_TIMEOUT_SECONDS = 90.0


class Engine(Protocol):
    """用到的 Docker 操作（sandbox.docker_engine.DockerEngine 满足它）。"""

    async def ensure_image(self, image: str) -> None: ...

    async def inspect_container(self, ref: str) -> dict[str, Any] | None: ...

    async def create_container(self, name: str, options: dict[str, Any]) -> str: ...

    async def put_archive(self, container_id: str, path: str, tar_bytes: bytes) -> None: ...

    async def start_container(self, container_id: str) -> None: ...

    async def remove_container(self, container_id: str) -> None: ...

    async def container_logs(self, container_id: str, *, tail: int = 300) -> str: ...

    async def remove_volume(self, name: str) -> None: ...

    async def list_containers(self, labels: dict[str, str]) -> list[dict[str, Any]]: ...


@dataclass(frozen=True)
class HostPosture:
    image: str
    network: str  # 宿主那张内部网络的名字（编排里的 host-link）
    memory_bytes: int = 768 * 1024 * 1024
    pids_limit: int = 512
    nano_cpus: int = 2_000_000_000
    # 业务服务不在 Docker 里跑时（本机用 Docker Desktop 试）：容器的内部地址从外面连不到，改成把端口发布到本机回环口
    publish: bool = False


def _state(info: dict[str, Any]) -> str:
    return str((info.get("State") or {}).get("Status") or "")


def _image(info: dict[str, Any]) -> str:
    return str((info.get("Config") or {}).get("Image") or "")


def _labels(info: dict[str, Any]) -> dict[str, str]:
    return (info.get("Config") or {}).get("Labels") or {}


def container_name(key: HostKey) -> str:
    """容器名和卷名：空间加人的摘要（编号里可能有容器名不认的字符，也不把人的编号露在容器名上）。"""
    digest = hashlib.sha256(f"{key.workspace_id}\0{key.user_id}".encode()).hexdigest()[:24]
    return f"kw-plugin-host-{digest}"


def repack(archive: bytes, prefix: str) -> bytes:
    """把插件压缩包（.tgz，内容在唯一的顶层目录下）改成放进容器用的 tar：顶层目录换成 prefix。只留普通文件和目录。"""
    out = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source, tarfile.open(fileobj=out, mode="w") as target:
        for member in source:
            _, _, inner = member.name.partition("/")
            if ".." in member.name.split("/") or member.name.startswith("/") or not (member.isfile() or member.isdir()):
                raise LaunchFailed("插件压缩包里有不能装的条目")
            info = tarfile.TarInfo(f"{prefix}/{inner}" if inner else prefix)
            info.mode = 0o755 if member.isdir() else 0o644
            info.type = tarfile.DIRTYPE if member.isdir() else tarfile.REGTYPE
            info.size = 0 if member.isdir() else member.size
            target.addfile(info, None if member.isdir() else source.extractfile(member))
    return out.getvalue()


class _Container:
    def __init__(self, engine: Engine, container_id: str, base_url: str, token: str, *, signature: str = "",
                 plugins: int = 0) -> None:
        self._engine = engine
        self._id = container_id
        self._alive = True
        self.base_url = base_url
        self.token = token
        self.signature = signature
        self.plugins = plugins

    def alive(self) -> bool:
        return self._alive

    async def logs(self) -> str:
        return await self._engine.container_logs(self._id)

    async def refresh(self) -> None:
        """回收巡检时看一眼容器还在不在跑（被系统杀掉、被人手动删掉）。"""
        info = await self._engine.inspect_container(self._id)
        self._alive = bool(info and (info.get("State") or {}).get("Running"))

    async def stop(self) -> None:
        self._alive = False
        await self._engine.remove_container(self._id)


class DockerLauncher:
    def __init__(self, engine: Engine, posture: HostPosture, *, env: Mapping[str, str]) -> None:
        self._engine = engine
        self._posture = posture
        self._env = dict(env)

    def _options(self, key: HostKey, token: str, *, signature: str = "", plugins: int = 0,
                 ephemeral: bool = False) -> dict[str, Any]:
        name = container_name(key)
        environment = {**self._env, "KNOWWORK_HOST_TOKEN": token, "KNOWWORK_HOST_PLUGIN_DIR": PLUGIN_ROOT,
                       "NARB_DISABLE_NATIVE_CACHE": "1"}
        return {
            "image": self._posture.image,
            "environment": [f"{k}={v}" for k, v in environment.items()],
            "labels": {LABEL: "1", f"{LABEL}.workspace": key.workspace_id, f"{LABEL}.user": key.user_id,
                       f"{LABEL}.signature": signature, f"{LABEL}.plugins": str(plugins),
                       **({f"{LABEL}.trial": "1"} if ephemeral else {})},
            "read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges"], "init": True,
            "mem_limit": self._posture.memory_bytes, "memswap_limit": self._posture.memory_bytes,
            "nano_cpus": self._posture.nano_cpus, "pids_limit": self._posture.pids_limit,
            "network": self._posture.network, "restart_policy": {"Name": "no"},
            **({"ports": {f"{PORT}/tcp": ("127.0.0.1", None)}} if self._posture.publish else {}),
            # 具名卷：这个人的 DSH_HOME，容器删了还在；试装的用匿名卷，随容器删
            **({} if ephemeral else {"volumes": {name: {"bind": "/data/dsh", "mode": "rw"}}}),
            # 匿名卷放插件：启动前拷进去，随容器删（engine.remove_container 连匿名卷一起删）
            "mounts": [docker.types.Mount(target=PLUGIN_ROOT, source=None, type="volume"),
                       *([docker.types.Mount(target="/data/dsh", source=None, type="volume")] if ephemeral else [])],
            "tmpfs": {"/tmp": "uid=1000,gid=1000,mode=1777", "/run/knowwork-host-private": "uid=1000,gid=1000,mode=0700"},
        }

    async def launch(self, key: HostKey, plugins: Sequence[HostPlugin], *, ephemeral: bool = False) -> Launched:
        name = container_name(key)
        token = secrets.token_hex(32)
        signature = signature_of(plugins)
        try:
            await self._engine.ensure_image(self._posture.image)
            stale = await self._engine.inspect_container(name)
            if (stale is not None and not ephemeral and _state(stale) in ("running", "created", "restarting")
                    and not self._outdated(_image(stale))):
                raise LaunchRaced()  # 别的进程建好了或正在建：去接它
            if stale is not None:  # 已经退出的（崩溃、被系统杀掉）、旧镜像起的、上次没收干净的试装：删掉重建，卷还在
                await self._engine.remove_container(str(stale["Id"]))
            try:
                container_id = await self._engine.create_container(name, self._options(
                    key, token, signature=signature, plugins=len(plugins), ephemeral=ephemeral))
            except DockerConflict as error:  # 和别的进程同时建，它先到
                raise LaunchRaced() from error
            try:
                for plugin in plugins:
                    tar = await asyncio.to_thread(repack, await plugin.load(), plugin.digest)
                    await self._engine.put_archive(container_id, PLUGIN_ROOT, tar)
                await self._engine.start_container(container_id)
                base_url = await self._address(container_id)
                await self._wait_ready(base_url)
            except LaunchFailed as error:  # 起不来的原因多半在容器输出里（试装时给上传的人看）
                detail = await self._engine.container_logs(container_id)
                await self._engine.remove_container(container_id)
                raise LaunchFailed(str(error), detail[-4000:]) from error
            except BaseException:
                await self._engine.remove_container(container_id)
                raise
        except (LaunchFailed, LaunchRaced):
            raise
        except Exception as error:
            log.warning("plugin host container for %s/%s did not start", key.workspace_id, key.user_id, exc_info=True)
            raise LaunchFailed("专属宿主的容器没有起来") from error
        return _Container(self._engine, container_id, base_url, token, signature=signature, plugins=len(plugins))

    async def find(self, key: HostKey) -> Launched | None:
        """这个人在跑的宿主，谁建的都算。别的进程正在建（还没启动）的等它起来；一直没起来的当成没收干净的删掉。"""
        name = container_name(key)
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_SECONDS
        while True:
            info = await self._engine.inspect_container(name)
            if info is None or _labels(info).get(f"{LABEL}.trial"):
                return None
            state = _state(info)
            if self._outdated(_image(info)):  # 升级前的旧镜像：不接，删掉让调用方按新镜像重建
                await self._engine.remove_container(str(info["Id"]))
                return None
            if state == "running":
                return await self._adopt(info)
            if state not in ("created", "restarting"):
                return None
            if asyncio.get_running_loop().time() >= deadline:
                await self._engine.remove_container(str(info["Id"]))
                return None
            await asyncio.sleep(0.5)

    async def running(self) -> list[tuple[HostKey, Launched]]:
        found: list[tuple[HostKey, Launched]] = []
        for item in await self._engine.list_containers({LABEL: "1"}):
            labels = item.get("Labels") or {}
            if labels.get(f"{LABEL}.trial") or not labels.get(f"{LABEL}.user"):
                continue
            if item.get("State") in ("exited", "dead") or self._outdated(str(item.get("Image") or "")):
                # 退出了的、升级前旧镜像起的删掉（卷留着，下次再起接得上）
                await self._engine.remove_container(str(item["Id"]))
                continue
            if item.get("State") != "running":
                continue
            info = await self._engine.inspect_container(str(item["Id"]))
            if info is None or _state(info) != "running":
                continue
            with contextlib.suppress(LaunchFailed):
                found.append((HostKey(labels[f"{LABEL}.workspace"], labels[f"{LABEL}.user"]), await self._adopt(info)))
        return found

    async def aclose(self) -> None:
        return None

    def _outdated(self, image: str) -> bool:
        return bool(image) and image != self._posture.image

    async def _adopt(self, info: dict[str, Any]) -> _Container:
        """接过一个在跑的容器：地址从网络信息取，令牌从它的环境变量取，等它就绪。"""
        env = dict(item.split("=", 1) for item in (info.get("Config") or {}).get("Env") or [] if "=" in item)
        token = env.get("KNOWWORK_HOST_TOKEN", "")
        if not token:
            raise LaunchFailed("专属宿主的容器里没有宿主令牌")
        container_id = str(info["Id"])
        base_url = self._address_of(info) or await self._address(container_id)
        await self._wait_ready(base_url)
        labels = _labels(info)
        return _Container(self._engine, container_id, base_url, token, signature=labels.get(f"{LABEL}.signature", ""),
                          plugins=int(labels.get(f"{LABEL}.plugins") or 0))

    async def remove_home(self, key: HostKey) -> None:
        """删这个人的具名卷（定期清理用，hosts/cleanup.py）。同名的容器还留着（没收干净的）先删掉，不然卷删不掉。"""
        name = container_name(key)
        stale = await self._engine.inspect_container(name)
        if stale is not None:
            await self._engine.remove_container(str(stale["Id"]))
        await self._engine.remove_volume(name)

    def _address_of(self, info: dict[str, Any]) -> str | None:
        settings = info.get("NetworkSettings") or {}
        if self._posture.publish:
            bound = (settings.get("Ports") or {}).get(f"{PORT}/tcp") or []
            return f"http://127.0.0.1:{bound[0]['HostPort']}" if bound else None
        address = ((settings.get("Networks") or {}).get(self._posture.network) or {}).get("IPAddress")
        return f"http://{address}:{PORT}" if address else None

    async def _address(self, container_id: str) -> str:
        """容器的地址。刚启动的那一下网络信息可能还没填上，等一会儿；这期间容器退了（入口脚本失败）直接报错。"""
        for _ in range(40):
            info = await self._engine.inspect_container(container_id) or {}
            if not (info.get("State") or {}).get("Running"):
                raise LaunchFailed("专属宿主的容器启动后就退出了")
            address = self._address_of(info)
            if address:
                return address
            await asyncio.sleep(0.25)
        raise LaunchFailed("专属宿主的容器没有拿到地址")

    @staticmethod
    async def _wait_ready(base_url: str) -> None:
        deadline = asyncio.get_running_loop().time() + READY_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            while asyncio.get_running_loop().time() < deadline:
                try:
                    response = await client.get(f"{base_url}/health")
                    if response.is_success and response.json().get("ok") is True:
                        return
                except (httpx.HTTPError, ValueError):
                    pass
                await asyncio.sleep(0.5)
        raise LaunchFailed("专属宿主的容器起来以后一直没有就绪")
