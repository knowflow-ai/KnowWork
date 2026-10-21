"""配置：主密钥可来自环境变量或文件（二选一），宿主令牌缺省时由主密钥派生。"""

import os

import pytest
from pydantic import ValidationError

from knowwork_server.config import Settings, derive_secret

MASTER = "m" * 48


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith("KNOWWORK_"):
            monkeypatch.delenv(name)


def test_master_key_from_env_derives_tokens(monkeypatch):
    monkeypatch.setenv("KNOWWORK_MASTER_KEY", MASTER)
    settings = Settings()
    assert settings.master_key.get_secret_value() == MASTER
    assert settings.host_token.get_secret_value() == derive_secret(MASTER, "host-token")
    assert len(settings.host_token.get_secret_value()) >= 32


def test_master_key_from_file(tmp_path, monkeypatch):
    key_file = tmp_path / "master.key"
    key_file.write_text(MASTER + "\n")
    monkeypatch.setenv("KNOWWORK_MASTER_KEY_FILE", str(key_file))
    settings = Settings()
    assert settings.master_key.get_secret_value() == MASTER


def test_env_and_file_together_rejected(tmp_path, monkeypatch):
    key_file = tmp_path / "master.key"
    key_file.write_text(MASTER)
    monkeypatch.setenv("KNOWWORK_MASTER_KEY", MASTER)
    monkeypatch.setenv("KNOWWORK_MASTER_KEY_FILE", str(key_file))
    with pytest.raises(ValidationError, match="二选一"):
        Settings()


def test_missing_master_key_rejected():
    with pytest.raises(ValidationError, match="主密钥"):
        Settings()


def test_unreadable_file_rejected_without_leaking_path_content(tmp_path, monkeypatch):
    monkeypatch.setenv("KNOWWORK_MASTER_KEY_FILE", str(tmp_path / "missing.key"))
    with pytest.raises(ValidationError, match="无法读取"):
        Settings()


def test_short_key_in_file_rejected(tmp_path, monkeypatch):
    key_file = tmp_path / "master.key"
    key_file.write_text("short-secret")
    monkeypatch.setenv("KNOWWORK_MASTER_KEY_FILE", str(key_file))
    with pytest.raises(ValidationError) as info:
        Settings()
    assert "short-secret" not in str(info.value)


def test_explicit_tokens_kept(monkeypatch):
    monkeypatch.setenv("KNOWWORK_MASTER_KEY", MASTER)
    monkeypatch.setenv("KNOWWORK_HOST_TOKEN", "h" * 40)
    settings = Settings()
    assert settings.host_token.get_secret_value() == "h" * 40


def test_derive_secret_is_purpose_bound():
    assert derive_secret(MASTER, "a") != derive_secret(MASTER, "b")
    assert derive_secret(MASTER, "a") == derive_secret(MASTER, "a")
