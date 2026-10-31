"""部署编排的结构约束：容器数、网络隔离、密钥卷的挂载范围。

用 `docker compose config --format json` 解析出最终配置再断言；本机没有 docker compose 时跳过。
不构建、不拉取、不启动任何容器。
"""

import json
import re
import shutil
import subprocess
from functools import cache
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "deploy" / "docker-compose.yml"
SANDBOX = REPO / "deploy" / "docker-compose.sandbox.yml"
VERSION = (REPO / "VERSION").read_text().strip()
ENV = {"KNOWWORK_RUSTFS_ACCESS_KEY": "ci-access", "KNOWWORK_RUSTFS_SECRET_KEY": "ci-secret", "PATH": "/usr/bin:/bin"}

pytestmark = pytest.mark.skipif(shutil.which("docker") is None, reason="需要 docker compose")


@cache
def _config(*overlays: Path) -> dict:
    docker = shutil.which("docker")
    env = {**ENV, "PATH": f"{Path(docker).parent}:/usr/bin:/bin"}
    files = [arg for path in (COMPOSE, *overlays) for arg in ("-f", str(path))]
    result = subprocess.run([docker, "compose", *files, "config", "--format", "json"],
                            env=env, capture_output=True, text=True, timeout=60, check=False)
    if result.returncode != 0:
        pytest.skip(f"docker compose 不可用：{result.stderr[:200]}")
    return json.loads(result.stdout)


def _services() -> dict:
    return _config()["services"]


def _networks(name: str) -> set[str]:
    return set(_services()[name].get("networks", {}))


def _members(network: str) -> set[str]:
    return {name for name in _services() if network in _networks(name)}


def test_seven_long_running_containers():
    long_running = {name for name, svc in _services().items() if svc.get("restart") != "no"}
    assert long_running == {"gateway", "api", "worker", "runtime-host", "postgres", "redis", "rustfs"}


def test_one_shot_jobs():
    assert {name for name, svc in _services().items() if svc.get("restart") == "no"} == {"bootstrap"}
    assert _services()["bootstrap"]["network_mode"] == "none"


def _socket_mounts(services: dict) -> dict[str, list[str]]:
    return {name: [m["target"] for m in svc.get("volumes", []) if str(m.get("source", "")).endswith("docker.sock")]
            for name, svc in services.items()}


def test_main_compose_file_alone_keeps_sandbox_off_and_socket_unmounted():
    """不再有 Docker API 代理。主编排文件单独用时沙箱关闭、不挂 docker.sock（挂了等于宿主机 root）。"""
    assert "sandbox-proxy" not in _services() and "sandbox-control" not in _config()["networks"]
    api = _services()["api"]
    assert api["environment"]["KNOWWORK_SANDBOX_DOCKER_ENABLED"] == "false"
    assert api["environment"]["KNOWWORK_SANDBOX_IMAGE"] == (
        f"crpi-6q638zy09wmnj70w.cn-hangzhou.personal.cr.aliyuncs.com/knowflow-ai/knowwork-sandbox:{VERSION}")
    assert "DOCKER_HOST" not in api["environment"]
    assert not any(_socket_mounts(_services()).values())


def test_sandbox_overlay_turns_it_on_and_mounts_socket_only_for_api_and_worker():
    """worker 把解析出的附件 Markdown 放进会话容器，要和 api 连同一个 daemon、用同一份沙箱配置。"""
    services = _config(SANDBOX)["services"]
    for name in ("api", "worker"):
        assert services[name]["environment"]["KNOWWORK_SANDBOX_DOCKER_ENABLED"] == "true", name
        assert services[name]["environment"]["KNOWWORK_SANDBOX_IMAGE"] == (
            f"crpi-6q638zy09wmnj70w.cn-hangzhou.personal.cr.aliyuncs.com/knowflow-ai/knowwork-sandbox:{VERSION}"), name
    assert {name: targets for name, targets in _socket_mounts(services).items() if targets} == {
        "api": ["/var/run/docker.sock"], "worker": ["/var/run/docker.sock"]}


def test_api_and_worker_agree_on_streaming_execution():
    """流式执行会改容器姿态（多加 SYS_CHROOT、换入口），姿态摘要写在容器 label 上、对不上就重建。worker 也建、也用会话容器
    （放附件），两边开关不一致时会互相把对方建的容器当成「配置已变更」删掉重建，工作区被清空。"""
    services = _config(SANDBOX)["services"]
    assert services["api"]["environment"]["KNOWWORK_SANDBOX_SSH_ENABLED"] == "false"
    assert services["worker"]["environment"]["KNOWWORK_SANDBOX_SSH_ENABLED"] == "false"


def test_sandbox_is_on_by_default_through_compose_file_in_env_example():
    lines = (REPO / "deploy" / ".env.example").read_text().splitlines()
    assert "COMPOSE_FILE=docker-compose.yml:docker-compose.sandbox.yml" in lines
    # .env 里写死开关会盖过叠加文件的默认值，所以模板里只能是注释
    assert not any(line.startswith("KNOWWORK_SANDBOX_DOCKER_ENABLED=") for line in lines)


def test_images_come_from_one_registry_with_versioned_tags():
    for name, svc in _config(SANDBOX)["services"].items():
        if "image" in svc:
            assert svc["image"].startswith("crpi-6q638zy09wmnj70w.cn-hangzhou.personal.cr.aliyuncs.com/knowflow-ai/"), name
    for name in ("api", "worker", "runtime-host", "gateway"):
        assert _services()[name]["image"].endswith(f":{VERSION}"), name


def test_api_and_worker_entrypoints_drop_to_app_user_with_only_setuid_setgid():
    for name, command in (("api", ["python", "-m", "knowwork_server"]),
                          ("worker", ["python", "-m", "knowwork_server.ops.worker"])):
        service = _services()[name]
        assert service["user"] == "0:0" and sorted(service["cap_add"]) == ["SETGID", "SETUID"], name
        assert service["entrypoint"] == ["python", "-m", "knowwork_server.ops.entrypoint"], name
        assert service["command"] == command, name


def test_sandbox_image_is_build_only_behind_a_profile():
    assert "sandbox-image" not in _services()  # 不加 --profile 时不出现，不常驻
    text = COMPOSE.read_text()
    assert "deploy/docker/sandbox.Dockerfile" in text and "profiles: [sandbox]" in text
    dockerfile = (REPO / "deploy" / "docker" / "sandbox.Dockerfile").read_text()
    for needle in ("python:3.12-slim-trixie", "node:22", "jq", "pandas", "openpyxl", "python-docx", "matplotlib",
                   "useradd -u 1000", "/workspace",
                   # 自带 Office 技能的工具链（照搬 KnowFlow）
                   "libreoffice-writer-nogui", "libreoffice-calc-nogui", "libreoffice-impress-nogui", "poppler-utils",
                   "pandoc", "docx@", "pptxgenjs@", "markitdown", "/opt/knowwork/office"):
        assert needle in dockerfile, needle


def test_runtime_host_only_reachable_by_api():
    assert _networks("runtime-host") == {"host-link"}
    assert _config()["networks"]["host-link"]["internal"] is True
    assert _members("host-link") == {"api", "runtime-host"}


def test_api_internal_port_bound_to_host_link_only():
    api = _services()["api"]
    assert api["environment"]["KNOWWORK_INTERNAL_HOST"] == api["networks"]["host-link"]["ipv4_address"]


def test_data_stores_are_internal_only():
    assert _config()["networks"]["backend"]["internal"] is True
    for name in ("postgres", "redis", "rustfs"):
        assert _networks(name) == {"backend"}, name


def test_only_gateway_publishes_ports():
    published = {name for name, svc in _services().items() if svc.get("ports")}
    assert published == {"gateway"}
    assert _networks("gateway") == {"edge"}


def _mounts(volume: str) -> dict[str, bool]:
    result = {}
    for name, svc in _services().items():
        for mount in svc.get("volumes", []):
            if mount.get("type") == "volume" and mount.get("source") == volume:
                result[name] = bool(mount.get("read_only"))
    return result


@pytest.mark.parametrize(("volume", "allowed"), [
    ("secrets", {"bootstrap", "api", "worker"}),
    ("host-secrets", {"bootstrap", "runtime-host"}),
    ("db-secrets", {"bootstrap", "postgres"}),
])
def test_secret_volumes_scoped_and_read_only(volume, allowed):
    mounts = _mounts(volume)
    assert set(mounts) == allowed
    assert all(read_only for name, read_only in mounts.items() if name != "bootstrap")


def test_no_plaintext_secrets_in_environment():
    for name, svc in _services().items():
        for key, value in (svc.get("environment") or {}).items():
            if "PASSWORD" in key or key.endswith("_TOKEN"):
                assert key.endswith("_FILE"), f"{name}.{key}"
                assert value.startswith("/run/"), f"{name}.{key}"


def test_app_services_drop_capabilities():
    for name in ("api", "worker", "runtime-host", "gateway", "bootstrap"):
        svc = _services()[name]
        assert svc.get("cap_drop") == ["ALL"], name
        assert "no-new-privileges:true" in svc.get("security_opt", []), name


def test_api_migrates_as_owner_and_runs_as_plain_app_role():
    api = _services()["api"]["environment"]
    assert api["KNOWWORK_AUTO_MIGRATE"] == "true"
    assert api["KNOWWORK_DATABASE_URL"].startswith("postgresql+asyncpg://knowwork_app@postgres:5432/")
    assert api["KNOWWORK_MIGRATION_DATABASE_URL"].startswith("postgresql+asyncpg://knowwork_owner@postgres:5432/")
    assert api["KNOWWORK_APP_DB_ROLE"] == "knowwork_app"
    worker = _services()["worker"]["environment"]
    assert worker["KNOWWORK_AUTO_MIGRATE"] == "false"
    assert "KNOWWORK_MIGRATION_DATABASE_URL" not in worker, "worker 不迁移，不需要 owner 连接串"
    assert worker["KNOWWORK_DATABASE_URL"].startswith("postgresql+asyncpg://knowwork_app@")


def test_host_and_api_agree_on_callback_and_model_alias():
    host = _services()["runtime-host"]["environment"]
    api = _services()["api"]["environment"]
    assert host["KNOWWORK_TOOLS_BASE_URL"] == "http://api:8001/internal/extensions/tools"
    assert host["KNOWWORK_MODEL_BASE_URL"] == "http://api:8001/internal/model/v1"
    assert host["KNOWWORK_MODEL"] == api["KNOWWORK_HOST_MODEL_ALIAS"]


def test_every_callback_the_host_reads_is_configured():
    """宿主插件读的每个回调地址（KNOWWORK_*_BASE_URL）编排里都要配，指向 api 的内部端口。没配时插件退回
    127.0.0.1:8001，在容器里连的是宿主自己，那一类回调全部失败：v0.0.1、v0.0.2 漏了沙箱那个，
    写文件、执行命令都报「沙箱服务暂时不可用」，本机开发时进程都在一台机器上，默认地址恰好能用，没暴露出来。"""
    sources = (REPO / "runtime-host" / "plugins").rglob("*.mjs")
    read = {name for path in sources
            for name in re.findall(r"process\.env\.(KNOWWORK_[A-Z_]*BASE_URL)", path.read_text("utf-8"))}
    assert {"KNOWWORK_SANDBOX_BASE_URL", "KNOWWORK_TOOLS_BASE_URL"} <= read, "插件读地址的写法变了，这条测试跟着改"
    host = _services()["runtime-host"]["environment"]
    missing = sorted(name for name in read if not host.get(name, "").startswith("http://api:8001/internal/"))
    assert missing == [], f"宿主读这些回调地址，编排里没配或没指向 api 的内部端口：{missing}"
