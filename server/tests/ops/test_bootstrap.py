"""部署密钥 bootstrap：主密钥只生成一次，其余文件都由主密钥派生，可重复执行。"""

import os
import stat

import pytest

from knowwork_server.config import derive_secret
from knowwork_server.ops.bootstrap import BootstrapError, Layout, layout_from_env, main, run


@pytest.fixture
def layout(tmp_path) -> Layout:
    return Layout(secrets_dir=tmp_path / "secrets", host_dir=tmp_path / "host", db_dir=tmp_path / "db")


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_first_run_creates_everything(layout):
    master = run(layout)
    assert len(master) >= 32
    assert (layout.secrets_dir / "master.key").read_text() == master
    assert (layout.host_dir / "host_token").read_text() == derive_secret(master, "host-token")
    app_password = derive_secret(master, "db-app-password")
    assert (layout.db_dir / "app_password").read_text() == app_password
    assert (layout.db_dir / "postgres_password").read_text() == derive_secret(master, "db-postgres-password")
    owner_password = derive_secret(master, "db-owner-password")
    assert (layout.db_dir / "owner_password").read_text() == owner_password
    assert owner_password != app_password
    # 业务服务两份口令：迁移用 owner，运行用 app
    pgpass = (layout.secrets_dir / "pgpass").read_text().splitlines()
    assert pgpass == [f"postgres:5432:knowwork:knowwork_owner:{owner_password}",
                      f"postgres:5432:knowwork:knowwork_app:{app_password}"]


def test_modes_are_owner_only(layout):
    run(layout)
    assert _mode(layout.secrets_dir / "master.key") == 0o400
    # asyncpg 拒绝组或其他人可读的 pgpass
    assert _mode(layout.secrets_dir / "pgpass") == 0o600
    assert _mode(layout.host_dir / "host_token") == 0o400
    assert _mode(layout.db_dir / "app_password") == 0o400
    assert _mode(layout.db_dir / "owner_password") == 0o400


def test_second_run_keeps_master_key_and_restores_derived_files(layout):
    master = run(layout)
    (layout.host_dir / "host_token").unlink()
    assert run(layout) == master
    assert (layout.host_dir / "host_token").read_text() == derive_secret(master, "host-token")


def test_existing_weak_master_key_fails_closed(layout):
    layout.secrets_dir.mkdir(parents=True)
    (layout.secrets_dir / "master.key").write_text("short")
    with pytest.raises(BootstrapError):
        run(layout)
    assert not (layout.host_dir / "host_token").exists()


def test_no_temp_files_left(layout):
    run(layout)
    leftovers = [p for d in (layout.secrets_dir, layout.host_dir, layout.db_dir) for p in d.iterdir()
                 if p.name.startswith(".")]
    assert leftovers == []


def test_layout_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_SECRETS_DIR", str(tmp_path / "a"))
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_DB_HOST", "db.internal")
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_API_UID", "12345")
    result = layout_from_env()
    assert result.secrets_dir == tmp_path / "a"
    assert result.db_host == "db.internal"
    assert result.api_uid == 12345


def test_layout_from_env_rejects_bad_uid(monkeypatch):
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_API_UID", "root")
    with pytest.raises(BootstrapError):
        layout_from_env()


def test_main_exit_codes(monkeypatch, tmp_path, capsys):
    for name, sub in (("SECRETS", "s"), ("HOST", "h"), ("DB", "d")):
        monkeypatch.setenv(f"KNOWWORK_BOOTSTRAP_{name}_DIR", str(tmp_path / sub))
    assert main() == 0
    master = (tmp_path / "s" / "master.key").read_text()
    assert master not in capsys.readouterr().out
    os.chmod(tmp_path / "s" / "master.key", 0o600)
    (tmp_path / "s" / "master.key").write_text("x")
    assert main() == 1


def test_data_dirs_parsed_and_created(monkeypatch, tmp_path):
    target = tmp_path / "rustfs"
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_DATA_DIRS", f"{target}=10001")
    parsed = layout_from_env()
    assert parsed.data_dirs == ((target, 10001),)
    run(Layout(secrets_dir=tmp_path / "s", host_dir=tmp_path / "h", db_dir=tmp_path / "d", data_dirs=parsed.data_dirs))
    assert target.is_dir()


@pytest.mark.parametrize("raw", ["relative=1", "/x=abc", "/x"])
def test_data_dirs_rejects_bad_format(monkeypatch, raw):
    monkeypatch.setenv("KNOWWORK_BOOTSTRAP_DATA_DIRS", raw)
    with pytest.raises(BootstrapError):
        layout_from_env()
