"""本机开发环境：嵌入式 PostgreSQL + DSH 宿主 + 业务服务 + 后台任务 worker + 前端开发服务器，一条命令拉起。

用法（在仓库根目录）：
    cd server && uv run python ../scripts/dev_stack.py
然后浏览器打开 http://127.0.0.1:5173 。Ctrl+C 一起停掉。

数据、密钥与日志都在仓库根的 .dev/ 下（已在 .gitignore），重启后数据保留；删掉 .dev/ 即回到全新状态。
模型不在这里配：初始化后在「管理 → 模型」里添加厂商与模型，模型别名填 default（宿主请求的就是这个名字）。

改了代码不用重启整套（见 watched）：前端是 Vite 热更新；业务服务与 worker 在 server/src、扩展包源码里的 .py 改了
自动重启；宿主在 runtime-host/plugins 下的文件改了自动重启。runtime-host/profile/ 是启动时拷过去的，改了要重启整套。
"""

import os
import secrets
import shlex
import shutil
import signal
import subprocess
import sys
import time
import urllib.request
from importlib.metadata import entry_points
from importlib.util import find_spec
from pathlib import Path

import pgserver

ROOT = Path(__file__).resolve().parent.parent
DEV = ROOT / ".dev"
PINNED_NODE_BIN = Path.home() / ".nvm/versions/node/v24.18.0/bin"


def _node_bin() -> Path:
    """Node 所在目录：KNOWWORK_NODE_BIN 优先；本机有 nvm 装的固定版本就用它；否则取 PATH 里的 node（要 24.9 以上，DSH 0.2.2 起的要求）。"""
    configured = os.environ.get("KNOWWORK_NODE_BIN")
    if configured:
        return Path(configured)
    if (PINNED_NODE_BIN / "node").exists():
        return PINNED_NODE_BIN
    found = shutil.which("node")
    if found is None:
        sys.exit("找不到 node：请安装 Node.js 24.9 以上，或用 KNOWWORK_NODE_BIN 指定所在目录")
    return Path(found).parent


NODE_BIN = _node_bin()
PUBLIC_PORT, INTERNAL_PORT, HOST_PORT, WEB_PORT = 8000, 8001, 7300, 5173
APP_ROLE = "knowwork_app"  # 业务服务与 worker 连库用的普通账号（不是超级用户、没有 BYPASSRLS）


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


def watched(command: list[str], paths: list[Path], *, python_only: bool) -> list[str]:
    """改了代码自动重启这个进程：用 watchfiles（uvicorn[standard] 自带）包一层，看到改动先发 SIGINT 让它正常收尾，
    再起新的。起不来（例如改到一半有语法错）不会带倒整套，等下一次改动再起。

    - 业务服务重启时，进行中的回答接得上：事件泵起来后从库里最后一条接着读宿主的事件，页面的流断一下自己重连；
    - worker 正在跑的任务被打断，租约到期后重新领；
    - 宿主重启时正在进行的回答会中断，要重发（宿主的会话状态在它自己进程里）。"""
    return [sys.executable, "-m", "watchfiles", "--target-type", "command", "--filter",
            "python" if python_only else "default", "--sigint-timeout", "15", shlex.join(command), *map(str, paths)]


def server_sources() -> list[Path]:
    """业务服务与 worker 的源码：开源的 server/src，加上装了的扩展包（入口组 knowwork.extensions）的源码目录。"""
    found = [ROOT / "server/src"]
    for entry in entry_points(group="knowwork.extensions"):
        spec = find_spec(entry.module.split(".")[0])
        if spec is not None and spec.submodule_search_locations:
            found.extend(Path(p) for p in spec.submodule_search_locations)
    return list(dict.fromkeys(found))


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
    # 和部署包一样分两个账号：超级用户 postgres 只做迁移（表的 owner），业务服务与 worker 用普通账号 APP_ROLE 连库。
    # 原来本机一直用超级用户跑，超级用户绕过行级安全，一个空间能读到别的空间的数据，隔离问题在本机永远试不出来
    # （2026-10-27 走查多空间时撞上）。
    pg.psql(f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN "
            f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS; END IF; END $$;")
    migration_url = f"postgresql+asyncpg://postgres@/postgres?host={socket_dir}"
    database_url = f"postgresql+asyncpg://{APP_ROLE}@/postgres?host={socket_dir}"
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
    host = start("runtime-host", watched([str(ROOT / "runtime-host/node_modules/.bin/dsh"), "--profile", "knowwork-host"],
                                         [ROOT / "runtime-host/plugins"], python_only=False),
                 cwd=ROOT / "runtime-host", env={
                     **base_env, "DSH_HOME": str(home), "KNOWWORK_HOST_TOKEN": host_token,
                     "KNOWWORK_HOST_PORT": str(HOST_PORT), "KNOWWORK_MODEL": "default",
                     "KNOWWORK_MODEL_BASE_URL": f"{internal}/internal/model/v1",
                     "KNOWWORK_TOOLS_BASE_URL": f"{internal}/internal/extensions/tools",
                     "KNOWWORK_SKILLS_BASE_URL": f"{internal}/internal/extensions/skills"})
    # 业务服务与 worker 共用：同一个库、同一份对象存储、同一个沙箱（worker 把解析出的 Markdown 放进会话容器）。
    # MinerU 地址等其余 KNOWWORK_* 照外面的环境变量传进来。
    app_env = {
        **base_env, "KNOWWORK_DATABASE_URL": database_url, "KNOWWORK_MIGRATION_DATABASE_URL": migration_url,
        "KNOWWORK_APP_DB_ROLE": APP_ROLE, "KNOWWORK_MASTER_KEY": secret("master_key"),
        "KNOWWORK_HOST_TOKEN": host_token, "KNOWWORK_BLOB_DIR": str(DEV / "blobs"),
        # 会话沙箱：用本机 Docker（Docker Desktop），镜像先构建：
        #   docker build -f deploy/docker/sandbox.Dockerfile -t knowwork-sandbox:dev .
        # 流式执行（KNOWWORK_SANDBOX_SSH_ENABLED=true，照外面的环境变量传进来）要用这份 Dockerfile 新构建的镜像；
        # 宿主在本机用 macOS 自带的 ssh 与 nc。
        "KNOWWORK_SANDBOX_DOCKER_ENABLED": os.environ.get("KNOWWORK_SANDBOX_DOCKER_ENABLED", "true"),
        "KNOWWORK_SANDBOX_IMAGE": os.environ.get("KNOWWORK_SANDBOX_IMAGE", "knowwork-sandbox:dev")}
    sources = server_sources()
    server = start("server", watched([sys.executable, "-m", "knowwork_server"], sources, python_only=True),
                   cwd=ROOT / "server", env={
        **app_env, "KNOWWORK_HOST_BASE_URL": f"http://127.0.0.1:{HOST_PORT}",
        "KNOWWORK_PUBLIC_HOST": "127.0.0.1", "KNOWWORK_PUBLIC_PORT": str(PUBLIC_PORT),
        "KNOWWORK_INTERNAL_HOST": "127.0.0.1", "KNOWWORK_INTERNAL_PORT": str(INTERNAL_PORT),
        "KNOWWORK_PUBLIC_BASE_URL": f"http://127.0.0.1:{WEB_PORT}", "KNOWWORK_COOKIE_SECURE": "false"})
    if not wait_healthy(f"http://127.0.0.1:{PUBLIC_PORT}/health", server):
        print("业务服务没能起来，看 .dev/logs/server.log")
    worker = start("worker", watched([sys.executable, "-m", "knowwork_server.ops.worker"], sources, python_only=True),
                   cwd=ROOT / "server", env=app_env)
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
