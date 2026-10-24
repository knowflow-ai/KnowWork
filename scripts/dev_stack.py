"""本机开发环境：嵌入式 PostgreSQL + DSH 宿主 + 业务服务 + 后台任务 worker + 前端开发服务器，一条命令拉起。

用法（在仓库根目录）：
    cd server && uv run python ../scripts/dev_stack.py
然后浏览器打开 http://127.0.0.1:5173 。Ctrl+C 一起停掉。

数据、密钥与日志都在仓库根的 .dev/ 下（已在 .gitignore），重启后数据保留；删掉 .dev/ 即回到全新状态。
模型不在这里配：初始化后在「管理 → 模型」里添加厂商与模型，模型别名填 default（宿主请求的就是这个名字）。
"""

import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / ".dev"
PINNED_NODE_BIN = Path.home() / ".nvm/versions/node/v24.18.0/bin"


def _node_bin() -> Path:
    """Node 所在目录：KNOWWORK_NODE_BIN 优先；本机有 nvm 装的固定版本就用它；否则取 PATH 里的 node（要 22.19 以上）。"""
    configured = os.environ.get("KNOWWORK_NODE_BIN")
    if configured:
        return Path(configured)
    if (PINNED_NODE_BIN / "node").exists():
        return PINNED_NODE_BIN
    found = shutil.which("node")
    if found is None:
        sys.exit("找不到 node：请安装 Node.js 22.19 以上，或用 KNOWWORK_NODE_BIN 指定所在目录")
    return Path(found).parent


NODE_BIN = _node_bin()
PUBLIC_PORT, INTERNAL_PORT, HOST_PORT, WEB_PORT = 8000, 8001, 7300, 5173


def secret(name: str) -> str:
    path = DEV / "secrets" / name
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(32))
        path.chmod(0o600)
    return path.read_text().strip()


def install_profile(home: Path) -> None:
    profile = home / "profiles" / "knowwork-host"
    shutil.rmtree(profile, ignore_errors=True)
    profile.mkdir(parents=True)
    for name in ("package.json", "cordis.yml", "cordis.patch.yml", "pnpm-workspace.yaml"):
        shutil.copy(ROOT / "runtime-host/profile" / name, profile / name)
    modules = profile / "node_modules" / "@knowwork"
    modules.mkdir(parents=True)
    (modules / "dsh-host").symlink_to(ROOT / "runtime-host/plugins/dsh-host")
    (modules / "dsh-host-bundle").symlink_to(ROOT / "runtime-host/plugins/host-bundle")


def start(name: str, args: list[str], *, cwd: Path, env: dict) -> subprocess.Popen:
    print(f"启动 {name}，日志 .dev/logs/{name}.log")
    with open(DEV / "logs" / f"{name}.log", "ab") as log:  # 子进程继承文件描述符，父进程这边可以关
        return subprocess.Popen(args, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT,
                                start_new_session=True)


def wait_healthy(url: str, proc: subprocess.Popen, timeout: float = 90.0) -> bool:
    """等业务服务起来（启动时会跑数据库迁移），worker 再起，免得它领任务时表还没建好、刷一屏错误日志。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 不读系统代理
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and proc.poll() is None:
        try:
            with opener.open(url, timeout=2) as response:
                if response.status == 200:
                    return True
        except OSError:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    (DEV / "logs").mkdir(parents=True, exist_ok=True)
    pg = pgserver.get_server(str(DEV / "pg"), cleanup_mode="stop")
    socket_dir = pg.get_uri().split("host=")[1]
    database_url = f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}"
    host_token = secret("host_token")
    internal = f"http://127.0.0.1:{INTERNAL_PORT}"
    base_env = {**os.environ, "PATH": f"{NODE_BIN}:{os.environ['PATH']}"}
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        base_env.pop(proxy, None)
    # 一律不走代理：本机各服务之间、以及局域网里的模型服务都要直连。只去掉代理变量不够——
    # macOS 上 Python 还会读系统代理设置，局域网地址被送进代理就成了 502。
    base_env.update({"NO_PROXY": "*", "no_proxy": "*"})

    home = DEV / "dsh"
    install_profile(home)
    host = start("runtime-host", [str(ROOT / "runtime-host/node_modules/.bin/dsh"), "--profile", "knowwork-host"],
                 cwd=ROOT / "runtime-host", env={
                     **base_env, "DSH_HOME": str(home), "KNOWWORK_HOST_TOKEN": host_token,
                     "KNOWWORK_HOST_PORT": str(HOST_PORT), "KNOWWORK_MODEL": "default",
                     "KNOWWORK_MODEL_BASE_URL": f"{internal}/internal/model/v1",
                     "KNOWWORK_TOOLS_BASE_URL": f"{internal}/internal/extensions/tools",
                     "KNOWWORK_SKILLS_BASE_URL": f"{internal}/internal/extensions/skills"})
    # 业务服务与 worker 共用：同一个库、同一份对象存储、同一个沙箱（worker 把解析出的 Markdown 放进会话容器）。
    # MinerU 地址等其余 KNOWWORK_* 照外面的环境变量传进来。
    app_env = {
        **base_env, "KNOWWORK_DATABASE_URL": database_url, "KNOWWORK_MASTER_KEY": secret("master_key"),
        "KNOWWORK_HOST_TOKEN": host_token, "KNOWWORK_BLOB_DIR": str(DEV / "blobs"),
        # 会话沙箱：用本机 Docker（Docker Desktop），镜像先构建：
        #   docker build -f deploy/docker/sandbox.Dockerfile -t knowwork-sandbox:dev .
        "KNOWWORK_SANDBOX_DOCKER_ENABLED": os.environ.get("KNOWWORK_SANDBOX_DOCKER_ENABLED", "true"),
        "KNOWWORK_SANDBOX_IMAGE": os.environ.get("KNOWWORK_SANDBOX_IMAGE", "knowwork-sandbox:dev")}
    server = start("server", [sys.executable, "-m", "knowwork_server"], cwd=ROOT / "server", env={
        **app_env, "KNOWWORK_HOST_BASE_URL": f"http://127.0.0.1:{HOST_PORT}",
        "KNOWWORK_PUBLIC_HOST": "127.0.0.1", "KNOWWORK_PUBLIC_PORT": str(PUBLIC_PORT),
        "KNOWWORK_INTERNAL_HOST": "127.0.0.1", "KNOWWORK_INTERNAL_PORT": str(INTERNAL_PORT),
        "KNOWWORK_PUBLIC_BASE_URL": f"http://127.0.0.1:{WEB_PORT}", "KNOWWORK_COOKIE_SECURE": "false"})
    if not wait_healthy(f"http://127.0.0.1:{PUBLIC_PORT}/health", server):
        print("业务服务没能起来，看 .dev/logs/server.log")
    worker = start("worker", [sys.executable, "-m", "knowwork_server.ops.worker"], cwd=ROOT / "server", env=app_env)
    web = start("web", [str(ROOT / "web/node_modules/.bin/vite"), "--host", "127.0.0.1", "--port", str(WEB_PORT),
                        "--strictPort"], cwd=ROOT / "web", env=base_env)
    processes = {"runtime-host": host, "server": server, "worker": worker, "web": web}
    print(f"\n前端：http://127.0.0.1:{WEB_PORT}  （首次打开进入初始化向导，创建管理员）\n")

    stop = False

    def on_signal(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    try:
        while not stop:
            for name, proc in processes.items():
                if proc.poll() is not None:
                    print(f"{name} 已退出（代码 {proc.returncode}），看 .dev/logs/{name}.log")
                    stop = True
            time.sleep(1)
    finally:
        for proc in processes.values():
            if proc.poll() is None:
                os.killpg(proc.pid, signal.SIGTERM)
        for proc in processes.values():
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
        pg.cleanup()
    return 0


if __name__ == "__main__":
    sys.exit(main())
