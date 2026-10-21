"""deploy/install.sh：用假的 docker 命令驱动，验证版本检查、.env 准备、拉取重试上限与就绪轮询。

不调用真实 Docker。脚本与编排文件拷到临时目录里跑，不污染仓库里的 deploy/。
"""

import http.server
import os
import shutil
import socket
import subprocess
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]

FAKE_DOCKER = r"""#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "version --format") echo "${FAKE_DOCKER_VERSION:-27.3.1}"; exit 0 ;;
esac
if [ "$1" = "compose" ]; then
  for arg in "$@"; do
    case "$arg" in
      version) echo "${FAKE_COMPOSE_VERSION:-2.30.0}"; exit 0 ;;
      pull)
        n=$(cat "$FAKE_STATE" 2>/dev/null || echo 0); n=$((n + 1)); echo "$n" > "$FAKE_STATE"
        [ "$n" -gt "${FAKE_PULL_FAILURES:-0}" ] && exit 0 || exit 1 ;;
      config|up|ps) exit 0 ;;
    esac
  done
fi
exit 0
"""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _Ready(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200 if self.path == "/ready" else 404)
        self.end_headers()

    def log_message(self, *args):
        pass


@pytest.fixture
def sandbox(tmp_path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    for name in ("install.sh", "docker-compose.yml", "docker-compose.sandbox.yml", ".env.example"):
        shutil.copy(REPO / "deploy" / name, deploy / name)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(FAKE_DOCKER)
    (bin_dir / "docker").chmod(0o755)
    return deploy, bin_dir, tmp_path


def _run(sandbox, *args, port: int, **env):
    deploy, bin_dir, tmp = sandbox
    (deploy / ".env.example").write_text((deploy / ".env.example").read_text().replace(
        "KNOWWORK_PORT=8080", f"KNOWWORK_PORT={port}"))
    full_env = {"PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(tmp / "calls.log"),
                "FAKE_STATE": str(tmp / "pulls"), "KNOWWORK_PULL_RETRY_DELAY": "0", **env}
    result = subprocess.run(["sh", str(deploy / "install.sh"), *args], env=full_env, capture_output=True,
                            text=True, timeout=60, check=False)
    calls = (tmp / "calls.log").read_text() if (tmp / "calls.log").exists() else ""
    return result, calls


@pytest.fixture
def ready_server():
    port = _free_port()
    server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Ready)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()


def _env(deploy: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in (deploy / ".env").read_text().splitlines()
                if "=" in line and not line.startswith("#"))


def test_happy_path_prepares_env_and_waits_for_ready(sandbox, ready_server):
    result, calls = _run(sandbox, port=ready_server)
    assert result.returncode == 0, result.stderr
    assert "已就绪" in result.stdout
    env_file = sandbox[0] / ".env"
    env = _env(sandbox[0])
    assert len(env["KNOWWORK_RUSTFS_ACCESS_KEY"]) == 48
    assert oct(env_file.stat().st_mode & 0o777) == "0o600"
    # 首次运行把访问地址换成本机地址加端口，http 访问配非 Secure 的 Cookie，登录才不会失败
    assert env["KNOWWORK_PUBLIC_BASE_URL"].startswith("http://") and env["KNOWWORK_PUBLIC_BASE_URL"].endswith(
        f":{ready_server}")
    assert env["KNOWWORK_COOKIE_SECURE"] == "false"
    assert env["KNOWWORK_PUBLIC_BASE_URL"] in result.stdout
    # 默认带上沙箱叠加文件，并拉沙箱镜像
    assert "docker-compose.sandbox.yml" in calls and "--profile sandbox pull --quiet sandbox-image" in calls
    assert "up -d" in calls


def test_no_sandbox_drops_overlay_and_sandbox_image(sandbox, ready_server):
    result, calls = _run(sandbox, "--no-sandbox", port=ready_server)
    assert result.returncode == 0, result.stderr
    assert _env(sandbox[0])["COMPOSE_FILE"] == "docker-compose.yml"
    assert "docker-compose.sandbox.yml" not in calls and "sandbox-image" not in calls


def test_url_option_sets_public_address(sandbox, ready_server):
    result, _ = _run(sandbox, "--url", "http://117.50.180.59:8080/", port=ready_server)
    assert result.returncode == 0, result.stderr
    # 去掉末尾的斜杠；已有 .env 时再给 --url 也照样改
    assert _env(sandbox[0])["KNOWWORK_PUBLIC_BASE_URL"] == "http://117.50.180.59:8080"
    _run(sandbox, "--url", "https://kw.example.com", port=ready_server)
    assert _env(sandbox[0])["KNOWWORK_PUBLIC_BASE_URL"] == "https://kw.example.com"
    bad, _ = _run(sandbox, "--url", "kw.example.com", port=ready_server)
    assert bad.returncode != 0 and "http://" in bad.stderr


def test_private_address_gets_a_hint(sandbox, ready_server):
    deploy = sandbox[0]
    _run(sandbox, port=ready_server)
    (deploy / ".env").write_text("\n".join(
        "KNOWWORK_PUBLIC_BASE_URL=http://10.60.201.108:8080" if line.startswith("KNOWWORK_PUBLIC_BASE_URL=") else line
        for line in (deploy / ".env").read_text().splitlines()) + "\n")
    result, _ = _run(sandbox, port=ready_server)
    assert "内网地址" in result.stdout and "--url" in result.stdout


def test_warns_when_http_meets_secure_cookie(sandbox, ready_server):
    deploy = sandbox[0]
    _run(sandbox, port=ready_server)
    (deploy / ".env").write_text((deploy / ".env").read_text().replace(
        "KNOWWORK_COOKIE_SECURE=false", "KNOWWORK_COOKIE_SECURE=true"))
    result, _ = _run(sandbox, port=ready_server)
    assert "浏览器不会保存登录 Cookie" in result.stdout


def test_existing_env_values_are_kept(sandbox, ready_server):
    deploy = sandbox[0]
    _run(sandbox, port=ready_server)
    first = (deploy / ".env").read_text()
    _run(sandbox, port=ready_server)
    assert (deploy / ".env").read_text() == first


def test_pull_retried_then_succeeds(sandbox, ready_server):
    result, calls = _run(sandbox, port=ready_server, FAKE_PULL_FAILURES="2")
    assert result.returncode == 0, result.stderr
    # 前两次主镜像拉取失败，第三次主镜像与沙箱镜像都成功
    assert calls.count(" pull --quiet\n") == 3 and calls.count("pull --quiet sandbox-image") == 1


def test_pull_gives_up_after_three_attempts(sandbox, ready_server):
    result, calls = _run(sandbox, port=ready_server, FAKE_PULL_FAILURES="9")
    assert result.returncode != 0
    assert calls.count(" pull --quiet\n") == 3 and "sandbox-image" not in calls
    assert "up -d" not in calls


def test_no_pull_flag(sandbox, ready_server):
    result, calls = _run(sandbox, "--no-pull", port=ready_server)
    assert result.returncode == 0
    assert " pull" not in calls


def test_old_docker_rejected(sandbox, ready_server):
    result, calls = _run(sandbox, port=ready_server, FAKE_DOCKER_VERSION="20.10.7")
    assert result.returncode != 0
    assert "过低" in result.stderr
    assert "up -d" not in calls


def test_old_compose_rejected(sandbox, ready_server):
    result, _ = _run(sandbox, port=ready_server, FAKE_COMPOSE_VERSION="v2.10.0")
    assert result.returncode != 0
    assert "Compose" in result.stderr


def test_ready_timeout(sandbox):
    result, _ = _run(sandbox, "--no-pull", "--timeout", "1", port=_free_port())
    assert result.returncode != 0
    assert "仍未就绪" in result.stderr


def test_rejects_bad_arguments(sandbox):
    result, _ = _run(sandbox, "--timeout", "abc", port=_free_port())
    assert result.returncode != 0
    result, _ = _run(sandbox, "--bogus", port=_free_port())
    assert result.returncode != 0
