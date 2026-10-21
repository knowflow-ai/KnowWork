"""一次性部署密钥生成：bootstrap 容器生成一个主密钥，其余由 HKDF 派生。

- 主密钥只在缺失时生成，之后永不覆盖；
- 其余文件（宿主令牌、数据库口令、pgpass）都由主密钥派生，每次运行重写，丢了重跑即可恢复；
- 每个文件只给用得到它的容器用户读：主密钥与 pgpass 归业务服务，宿主令牌归宿主，口令文件归 postgres。
  三类文件放进三个卷，容器只挂自己那一个，宿主拿不到主密钥。

用法：`python -m knowwork_server.ops.bootstrap`（以 root 运行才能改属主；非 root 时只写文件不改属主）。
"""

import os
import secrets
import sys
from dataclasses import dataclass
from pathlib import Path

from ..config import MIN_MASTER_KEY_LENGTH, derive_secret

MASTER_KEY_FILE = "master.key"
# 两个连库角色：owner 是库与 public 的所有者，只用来迁移；app 是业务运行账号，只有迁移授予的增删改查。
OWNER_ROLE = "knowwork_owner"
APP_ROLE = "knowwork_app"


class BootstrapError(Exception):
    """密钥文件不可用；信息里不含任何密钥内容。"""


@dataclass(frozen=True)
class Layout:
    secrets_dir: Path = Path("/run/knowwork/secrets")
    host_dir: Path = Path("/run/knowwork/host")
    db_dir: Path = Path("/run/knowwork/db")
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "knowwork"
    api_uid: int = 10001
    host_uid: int = 1000
    postgres_uid: int = 70
    # 需要改属主的数据卷根目录（例如 RustFS 以 uid 10001 运行，新建的命名卷根目录归 root）
    data_dirs: tuple[tuple[Path, int], ...] = ()


@dataclass(frozen=True)
class SecretFile:
    path: Path
    content: str
    uid: int
    mode: int


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    if not raw.isdigit():
        raise BootstrapError(f"{name} 必须是非负整数")
    return int(raw)


def _parse_data_dirs(raw: str) -> tuple[tuple[Path, int], ...]:
    """格式：`/路径=uid,/路径=uid`。"""
    items: list[tuple[Path, int]] = []
    for part in filter(None, (piece.strip() for piece in raw.split(","))):
        path, sep, uid = part.rpartition("=")
        if not sep or not path.startswith("/") or not uid.isdigit():
            raise BootstrapError("KNOWWORK_BOOTSTRAP_DATA_DIRS 格式应为 /路径=uid，多个用逗号分隔")
        items.append((Path(path), int(uid)))
    return tuple(items)


def layout_from_env() -> Layout:
    base = Layout()
    env = os.environ.get
    return Layout(
        secrets_dir=Path(env("KNOWWORK_BOOTSTRAP_SECRETS_DIR", str(base.secrets_dir))),
        host_dir=Path(env("KNOWWORK_BOOTSTRAP_HOST_DIR", str(base.host_dir))),
        db_dir=Path(env("KNOWWORK_BOOTSTRAP_DB_DIR", str(base.db_dir))),
        db_host=env("KNOWWORK_BOOTSTRAP_DB_HOST", base.db_host),
        db_port=_int_env("KNOWWORK_BOOTSTRAP_DB_PORT", base.db_port),
        db_name=env("KNOWWORK_BOOTSTRAP_DB_NAME", base.db_name),
        api_uid=_int_env("KNOWWORK_BOOTSTRAP_API_UID", base.api_uid),
        host_uid=_int_env("KNOWWORK_BOOTSTRAP_HOST_UID", base.host_uid),
        postgres_uid=_int_env("KNOWWORK_BOOTSTRAP_POSTGRES_UID", base.postgres_uid),
        data_dirs=_parse_data_dirs(env("KNOWWORK_BOOTSTRAP_DATA_DIRS", "")),
    )


def _write_atomic(item: SecretFile) -> None:
    item.path.parent.mkdir(parents=True, exist_ok=True)
    temp = item.path.with_name(f".{item.path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(item.content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, item.mode)
        if os.geteuid() == 0:
            os.chown(temp, item.uid, item.uid)
        os.replace(temp, item.path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def load_or_create_master_key(layout: Layout) -> str:
    path = layout.secrets_dir / MASTER_KEY_FILE
    if path.exists():
        try:
            existing = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError) as error:
            raise BootstrapError("已有主密钥文件无法读取") from error
        if len(existing) < MIN_MASTER_KEY_LENGTH:
            raise BootstrapError("已有主密钥过短，拒绝继续；请人工核对后再运行")
        return existing
    created = secrets.token_urlsafe(48)
    _write_atomic(SecretFile(path, created, layout.api_uid, 0o400))
    return created


def plan_derived(layout: Layout, master: str) -> tuple[SecretFile, ...]:
    owner_pw = derive_secret(master, "db-owner-password")
    app_pw = derive_secret(master, "db-app-password")
    prefix = f"{layout.db_host}:{layout.db_port}:{layout.db_name}"
    pgpass = f"{prefix}:{OWNER_ROLE}:{owner_pw}\n{prefix}:{APP_ROLE}:{app_pw}\n"
    return (
        SecretFile(layout.secrets_dir / "pgpass", pgpass, layout.api_uid, 0o600),
        SecretFile(layout.host_dir / "host_token", derive_secret(master, "host-token"), layout.host_uid, 0o400),
        SecretFile(layout.db_dir / "postgres_password", derive_secret(master, "db-postgres-password"),
                   layout.postgres_uid, 0o400),
        SecretFile(layout.db_dir / "owner_password", owner_pw, layout.postgres_uid, 0o400),
        SecretFile(layout.db_dir / "app_password", app_pw, layout.postgres_uid, 0o400),
    )


def prepare_data_dirs(layout: Layout) -> None:
    """只改卷根目录本身的属主（不递归），已是目标属主时不动。非 root 运行时跳过。"""
    for path, uid in layout.data_dirs:
        path.mkdir(parents=True, exist_ok=True)
        if os.geteuid() == 0 and path.stat().st_uid != uid:
            os.chown(path, uid, uid)


def run(layout: Layout) -> str:
    master = load_or_create_master_key(layout)
    for item in plan_derived(layout, master):
        _write_atomic(item)
    prepare_data_dirs(layout)
    return master


def main() -> int:
    try:
        run(layout_from_env())
    except (BootstrapError, OSError) as error:
        print(f"bootstrap 失败：{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    print("KnowWork 部署密钥已就绪。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
