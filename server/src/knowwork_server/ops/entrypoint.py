"""api 容器入口：按挂进来的 Docker 套接字的属组给应用账号补一个附加组，然后降权运行主进程。

照抄 WeKnora 的 scripts/docker-entrypoint.sh（grant_docker_sock_to_appuser）：套接字通常是 root:docker 0660，
应用账号要读写它就得在那个组里。**绝不 chmod 套接字**：那会削弱宿主机上 daemon 的访问控制。属组是 0（root）
时不加：把应用账号放进 root 组的代价不该由入口脚本悄悄替部署方承担，应先在宿主机给套接字配一个非 root 组。

与 WeKnora 的差异：它以 root 运行、改 /etc/group 后用 gosu 降权；我们的根文件系统是只读的，改为直接
setgroups + setgid + setuid 后 exec（数字组号不需要 /etc/group 里有条目），不依赖 gosu。
挂 docker.sock 等于给了宿主机 root，只用于私有化单机部署。

用法：`python -m knowwork_server.ops.entrypoint python -m knowwork_server`
"""

import logging
import os
import pwd
import stat
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass

log = logging.getLogger("knowwork.entrypoint")

DEFAULT_SOCKET = "/var/run/docker.sock"
DEFAULT_APP_ID = 10001
NO_HOME = "/nonexistent"  # 镜像里建应用账号时给的家目录（server.Dockerfile 的 useradd --home-dir）


@dataclass(frozen=True)
class Identity:
    uid: int
    gid: int
    groups: tuple[int, ...]


def socket_paths(env: Mapping[str, str]) -> list[str]:
    paths = [DEFAULT_SOCKET]
    host = env.get("DOCKER_HOST", "")
    if host.startswith("unix://") and host.removeprefix("unix://") not in paths:
        paths.append(host.removeprefix("unix://"))
    return paths


def _accessible(info: os.stat_result, uid: int) -> bool:
    if info.st_mode & 0o006 == 0o006:
        return True
    return info.st_uid == uid and info.st_mode & 0o600 == 0o600


def socket_group(path: str, uid: int, *, stat_fn: Callable[[str], os.stat_result] = os.stat) -> int | None:
    """应用账号要能读写这个套接字需要补的组号；不需要或不该补时为 None（并说明原因）。"""
    try:
        info = stat_fn(path)
    except FileNotFoundError:
        return None
    except OSError:
        log.warning("cannot stat %s; Docker sandbox may be unable to reach the daemon", path)
        return None
    if not stat.S_ISSOCK(info.st_mode) or _accessible(info, uid):
        return None
    if info.st_gid == 0:
        log.warning("%s is owned by GID 0 and not writable by the app user; Docker sandbox needs a "
                    "group-writable socket with a non-root GID", path)
        return None
    if info.st_mode & 0o060 != 0o060:
        log.warning("%s is not group read-writable; Docker sandbox may be unable to reach the daemon", path)
    return info.st_gid


def resolve_identity(env: Mapping[str, str], *, stat_fn: Callable[[str], os.stat_result] = os.stat) -> Identity:
    uid = int(env.get("KNOWWORK_APP_UID", DEFAULT_APP_ID))
    gid = int(env.get("KNOWWORK_APP_GID", uid))
    if uid == 0 or gid == 0:
        raise ValueError("应用账号不能是 root")
    extra = []
    for path in socket_paths(env):
        group = socket_group(path, uid, stat_fn=stat_fn)
        if group is not None and group not in extra and group != gid:
            extra.append(group)
    return Identity(uid=uid, gid=gid, groups=(gid, *extra))


def drop_privileges(identity: Identity) -> None:
    os.setgroups(list(identity.groups))
    os.setgid(identity.gid)
    os.setuid(identity.uid)
    if os.getuid() == 0 or os.geteuid() == 0:
        raise RuntimeError("降权失败")


def home_of(uid: int, *, getpwuid: Callable[[int], object] = pwd.getpwuid) -> str:
    """应用账号的家目录；/etc/passwd 里没有这个账号（自定义了 KNOWWORK_APP_UID）就用 /nonexistent。"""
    try:
        return getpwuid(uid).pw_dir or NO_HOME
    except KeyError:
        return NO_HOME


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None,
         execvpe: Callable[[str, list[str], Mapping[str, str]], object] = os.execvpe) -> None:
    command = list(sys.argv[1:] if argv is None else argv)
    if not command:
        raise SystemExit("用法：python -m knowwork_server.ops.entrypoint <命令> [参数…]")
    environment = dict(os.environ if env is None else env)
    if os.geteuid() == 0:
        identity = resolve_identity(environment)
        drop_privileges(identity)
        # 以 root 起步时 HOME 是 /root，降权后读不了：asyncpg 连库前会探测 ~/.postgresql/ 下的客户端证书，
        # 遇到权限错误不当成「没有」而是直接抛出，api 就起不来。换成应用账号自己的家目录
        environment = {**environment, "HOME": home_of(identity.uid)}
    execvpe(command[0], command, environment)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="knowwork: %(message)s")
    main()
