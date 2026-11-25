"""定期清理专属宿主的持久目录：不在空间里的人、很久没用的人的删掉，连同对话去处记录；正在用的、最近用过的不动；
保留天数为 0 时只删不在空间里的人。进程起法删的是这个人的 DSH_HOME 目录。"""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from knowwork_server.conversations.models import Conversation
from knowwork_server.hosts import DedicatedHosts, HostKey, ProcessLauncher
from knowwork_server.hosts.cleanup import HomeCleanup
from knowwork_server.hosts.models import SessionHost

pytestmark = pytest.mark.anyio
NOW = datetime(2026, 11, 23, tzinfo=UTC)


class Launched:
    base_url, token, signature, plugins = "http://127.0.0.1:9", "t" * 64, "", 0

    def alive(self) -> bool:
        return True

    async def stop(self) -> None:
        return None


class Launcher:
    def __init__(self) -> None:
        self.removed: list[HostKey] = []
        self.live: dict[HostKey, Launched] = {}

    async def launch(self, key, plugins, *, ephemeral=False):
        self.live[key] = Launched()
        return self.live[key]

    async def find(self, key):
        return self.live.get(key)

    async def running(self):
        return list(self.live.items())

    async def remove_home(self, key: HostKey) -> None:
        self.removed.append(key)

    async def aclose(self) -> None:
        return None


async def no_prepare(_key, _kernel) -> None:
    return None


@pytest.fixture
async def env(sessionmaker):
    ws = "ws_" + uuid.uuid4().hex[:10]
    members = {"alice", "bob", "dave"}  # carol 已经被移出空间
    launcher = Launcher()
    hosts = DedicatedHosts(launcher, prepare=no_prepare)

    async def place(user: str | None, last: datetime | None) -> str:
        """一段落在 user 的专属宿主上（None 是共享宿主）的对话；last 为空表示对话已经删了，只剩去处记录。"""
        cid = f"c_{uuid.uuid4().hex}"
        async with sessionmaker.begin() as session:
            if last is not None:
                session.add(Conversation(id=cid, workspace_id=ws, user_id=user or "eve", updated_at=last))
            session.add(SessionHost(session_id=cid, host_workspace=ws if user else None, host_user=user,
                                    created_at=last or NOW - timedelta(days=400)))
        return cid

    def cleanup(retention_days: int) -> HomeCleanup:
        return HomeCleanup(hosts, sessionmaker, sessionmaker, member=lambda user, _ws: user in members,
                           retention_days=retention_days, clock=lambda: NOW)

    async def placed_users() -> set[str | None]:
        async with sessionmaker() as session:
            rows = await session.scalars(select(SessionHost.host_user).where(
                (SessionHost.host_workspace == ws) | SessionHost.host_workspace.is_(None)))
            return set(rows.all())

    yield ws, launcher, hosts, place, cleanup, placed_users
    await hosts.aclose()


async def test_people_who_left_or_stopped_using_plugins_lose_their_home(env):
    ws, launcher, hosts, place, cleanup, placed_users = env
    await place("alice", NOW - timedelta(days=200))
    await place("alice", NOW - timedelta(days=3))  # 最近还在用：按最近那段算
    await place("bob", NOW - timedelta(days=120))
    await place("carol", NOW - timedelta(days=1))  # 刚用过，但已经不在空间里了
    await place("dave", NOW - timedelta(days=300))
    await place("frank", None)  # 对话都删了：按最后一次落过来的时间算（400 天前）
    await place(None, NOW - timedelta(days=500))  # 共享宿主上的不归这里管
    await hosts.ensure(HostKey(ws, "dave"), [])  # dave 的宿主正在跑（不管哪个进程建的）

    assert await cleanup(90).run_once() == 3
    assert {key.user_id for key in launcher.removed} == {"bob", "carol", "frank"}
    assert {key.workspace_id for key in launcher.removed} == {ws}
    users = await placed_users()
    assert {"alice", "dave", None} <= users and not users & {"bob", "carol", "frank"}, "删了的人去处记录一起删"

    launcher.removed.clear()
    assert await cleanup(90).run_once() == 0 and launcher.removed == [], "下一轮不再看已经删过的人"


async def test_zero_retention_only_removes_people_who_left(env):
    _, launcher, _, place, cleanup, _ = env
    await place("bob", NOW - timedelta(days=3000))
    await place("carol", NOW - timedelta(days=1))
    assert await cleanup(0).run_once() == 1
    assert [key.user_id for key in launcher.removed] == ["carol"]


async def test_process_launcher_removes_the_home_directory(tmp_path):
    launcher = ProcessLauncher(runtime_dir=tmp_path, root=tmp_path / "hosts", env={})
    key = HostKey("ws1", "alice")
    home = launcher.home_of(key)
    (home / "sessions").mkdir(parents=True)
    (home / "sessions" / "log.jsonl").write_text("{}")
    other = launcher.home_of(HostKey("ws1", "bob"))
    other.mkdir(parents=True)
    await launcher.remove_home(key)
    assert not home.exists() and other.exists()
    await launcher.remove_home(key)  # 本来就没有也不算错


class Engine:
    def __init__(self, leftover: bool) -> None:
        self.leftover, self.calls = leftover, []

    async def inspect_container(self, ref: str):
        self.calls.append(("inspect", ref))
        return {"Id": "cid-1"} if self.leftover else None

    async def remove_container(self, container_id: str) -> None:
        self.calls.append(("remove_container", container_id))

    async def remove_volume(self, name: str) -> None:
        self.calls.append(("remove_volume", name))


@pytest.mark.parametrize("leftover", [True, False])
async def test_docker_launcher_removes_the_named_volume_after_any_leftover_container(leftover):
    from knowwork_server.hosts.docker_launcher import DockerLauncher, HostPosture, container_name

    engine = Engine(leftover)
    key = HostKey("ws1", "alice")
    await DockerLauncher(engine, HostPosture(image="img", network="net"), env={}).remove_home(key)
    name = container_name(key)
    expected = [("inspect", name), *([("remove_container", "cid-1")] if leftover else []), ("remove_volume", name)]
    assert engine.calls == expected, "没收干净的同名容器占着卷，先删它"
