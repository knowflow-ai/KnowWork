"""api 入口：按 Docker 套接字属组补附加组再降权（照抄 WeKnora 的 grant_docker_sock_to_appuser，不 chmod 套接字）。"""

import os
import stat

import pytest

from knowwork_server.ops import entrypoint
from knowwork_server.ops.entrypoint import Identity, resolve_identity, socket_group, socket_paths


def fake_stat(mode: int, uid: int = 0, gid: int = 999):
    return os.stat_result((mode, 0, 0, 1, uid, gid, 0, 0, 0, 0))


SOCK = stat.S_IFSOCK


def stats(table):
    def stat_fn(path):
        if path not in table:
            raise FileNotFoundError(path)
        value = table[path]
        if isinstance(value, Exception):
            raise value
        return value
    return stat_fn


def test_socket_paths_include_docker_host_unix_socket():
    assert socket_paths({}) == ["/var/run/docker.sock"]
    assert socket_paths({"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}) == [
        "/var/run/docker.sock", "/run/user/1000/docker.sock"]
    assert socket_paths({"DOCKER_HOST": "tcp://h:2376"}) == ["/var/run/docker.sock"]


@pytest.mark.parametrize("info,expected", [
    (fake_stat(SOCK | 0o660, gid=999), 999),  # root:docker 0660 → 加 docker 组
    (fake_stat(SOCK | 0o666, gid=999), None),  # 本来就能读写
    (fake_stat(SOCK | 0o600, uid=10001, gid=5), None),  # 属主就是应用账号
    (fake_stat(SOCK | 0o660, gid=0), None),  # root 组：不加，只告警
    (fake_stat(stat.S_IFREG | 0o660, gid=999), None),  # 不是套接字
    (fake_stat(SOCK | 0o640, gid=999), 999),  # 组没有写权限：照加，但告警
])
def test_socket_group_decisions(info, expected):
    assert socket_group("/var/run/docker.sock", 10001, stat_fn=stats({"/var/run/docker.sock": info})) == expected


def test_socket_group_missing_or_unreadable():
    assert socket_group("/nope", 10001, stat_fn=stats({})) is None
    assert socket_group("/x", 10001, stat_fn=stats({"/x": PermissionError("denied")})) is None


def test_resolve_identity_adds_each_socket_group_once():
    table = {"/var/run/docker.sock": fake_stat(SOCK | 0o660, gid=999),
             "/run/docker.sock": fake_stat(SOCK | 0o660, gid=999)}
    identity = resolve_identity({"DOCKER_HOST": "unix:///run/docker.sock"}, stat_fn=stats(table))
    assert identity == Identity(uid=10001, gid=10001, groups=(10001, 999))
    assert resolve_identity({"KNOWWORK_APP_UID": "2000"}, stat_fn=stats({})) == Identity(2000, 2000, (2000,))
    with pytest.raises(ValueError):
        resolve_identity({"KNOWWORK_APP_UID": "0"}, stat_fn=stats({}))


def test_drop_privileges_sets_groups_then_gid_then_uid(monkeypatch):
    calls = []
    monkeypatch.setattr(os, "setgroups", lambda groups: calls.append(("groups", groups)))
    monkeypatch.setattr(os, "setgid", lambda gid: calls.append(("gid", gid)))
    monkeypatch.setattr(os, "setuid", lambda uid: calls.append(("uid", uid)))
    monkeypatch.setattr(os, "getuid", lambda: 10001)
    monkeypatch.setattr(os, "geteuid", lambda: 10001)
    entrypoint.drop_privileges(Identity(10001, 10001, (10001, 999)))
    assert calls == [("groups", [10001, 999]), ("gid", 10001), ("uid", 10001)]
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError):
        entrypoint.drop_privileges(Identity(10001, 10001, (10001,)))


def test_main_execs_command_and_only_drops_when_root(monkeypatch):
    executed, dropped = [], []
    monkeypatch.setattr(entrypoint, "drop_privileges", dropped.append)
    monkeypatch.setattr(os, "geteuid", lambda: 10001)
    entrypoint.main(["python", "-m", "x"], env={"HOME": "/home/me"},
                    execvpe=lambda f, a, e: executed.append((a, e)))
    assert executed == [(["python", "-m", "x"], {"HOME": "/home/me"})] and dropped == []
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(entrypoint, "resolve_identity", lambda env: Identity(10001, 10001, (10001,)))
    monkeypatch.setattr(entrypoint, "home_of", lambda uid: f"/home/{uid}")
    entrypoint.main(["true"], env={"HOME": "/root", "TZ": "Asia/Shanghai"},
                    execvpe=lambda f, a, e: executed.append((a, e)))
    assert dropped == [Identity(10001, 10001, (10001,))]
    # 降权后 HOME 换成应用账号的：还指着 /root 时，asyncpg 探测 ~/.postgresql/ 下的证书会报权限错误，连不上库
    assert executed[-1] == (["true"], {"HOME": "/home/10001", "TZ": "Asia/Shanghai"})
    with pytest.raises(SystemExit):
        entrypoint.main([], env={})


def test_home_of_falls_back_when_account_missing():
    class Entry:
        pw_dir = "/srv/app"

    def lookup(uid):
        if uid == 10001:
            return Entry()
        raise KeyError(uid)

    assert entrypoint.home_of(10001, getpwuid=lookup) == "/srv/app"
    assert entrypoint.home_of(12345, getpwuid=lookup) == "/nonexistent"
