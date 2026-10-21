"""真实 Docker 上的沙箱合同（WeKnora 口径）：容器姿态与能力集、默认 root 且可按调用换账号、超时整组杀、容器内看门狗、
输出截断、产物收集与文件工具、空闲清扫删除容器后重建、销毁。

默认跳过；KNOWWORK_DOCKER_TESTS=1 且本机 Docker 可用时运行。镜像用 KNOWWORK_SANDBOX_TEST_IMAGE（默认本机已有的
alpine:3.20，不会为测试去拉大镜像）。daemon 地址与业务一样自动检测（DOCKER_HOST → docker context → 默认套接字）。
测试不访问外网：网络只检查路由表。跑完删掉建过的容器。
"""

import os
import time
import uuid

import docker
import pytest

from knowwork_server.sandbox import SandboxSettings, build_sandbox, scripts
from knowwork_server.sandbox.containers import SandboxKey
from knowwork_server.sandbox.contract import SandboxError, SandboxTooLarge

pytestmark = pytest.mark.anyio
IMAGE = os.environ.get("KNOWWORK_SANDBOX_TEST_IMAGE", "alpine:3.20")
# CHOWN DAC_OVERRIDE FOWNER FSETID SETGID SETUID KILL 的位图
WEKNORA_CAPS = sum(1 << bit for bit in (0, 1, 3, 4, 6, 7, 5))


def _docker_ready() -> bool:
    try:
        client = docker.DockerClient.from_env(timeout=3)
        try:
            return bool(client.ping()) and bool(client.images.list(name=IMAGE))
        finally:
            client.close()
    except (docker.errors.DockerException, OSError):
        return False


if os.environ.get("KNOWWORK_DOCKER_TESTS") != "1" or not _docker_ready():
    pytest.skip("未开启真实 Docker 测试（KNOWWORK_DOCKER_TESTS=1）、Docker 不可用或本机没有测试镜像",
                allow_module_level=True)


async def _module(**overrides):
    base = {"docker_enabled": True, "image": IMAGE, "memory_bytes": 256 * 1024 * 1024, "pids_limit": 64,
                "exec_default_timeout_seconds": 20, "exec_max_timeout_seconds": 30, "max_input_bytes": 1024}
    built = build_sandbox(SandboxSettings(**{**base, **overrides}))
    assert built is not None
    return built


@pytest.fixture
async def factory():
    built, created = [], []

    async def make(**overrides):
        module = await _module(**overrides)
        built.append(module)
        pair = ("ws-test", f"s-{uuid.uuid4().hex[:12]}")
        created.append((module, pair))
        return module, pair

    yield make
    for module, (workspace_id, session_id) in created:
        try:
            await module.sandbox.destroy(workspace_id, session_id)
        except SandboxError:
            pass
    for module in built:
        await module.aclose()


async def _sh(sandbox, ws, sid, command, timeout=15, **kwargs):
    return await sandbox.exec(ws, sid, ["sh", "-c", command], timeout_seconds=timeout, max_output_bytes=65536,
                              **kwargs)


async def test_posture_root_by_default_with_weknora_capabilities(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    await sandbox.put_input(ws, sid, "附件.txt", b"hello")
    who = await _sh(sandbox, ws, sid, "id -u; pwd; cat /workspace/input/附件.txt; echo; cat /proc/1/comm")
    assert who.stdout.split() == ["0", "/workspace", "hello", "docker-init"]
    caps = await _sh(sandbox, ws, sid, "grep CapEff /proc/self/status")
    assert int(caps.stdout.split()[-1], 16) == WEKNORA_CAPS
    nnp = await _sh(sandbox, ws, sid, "grep NoNewPrivs /proc/self/status")
    assert nnp.stdout.split()[-1] == "1"
    as_user = await _sh(sandbox, ws, sid, "id -u", user="1000:1000")
    assert as_user.stdout.strip() == "1000"
    marker = await _sh(sandbox, ws, sid, f"test -f {scripts.ACTIVITY_MARKER} && echo yes")
    assert marker.stdout.strip() == "yes"
    with pytest.raises(SandboxTooLarge):
        await sandbox.put_input(ws, sid, "big.bin", b"x" * 2048)
    info = await module.engine.inspect_container(SandboxKey.of(ws, sid).name)
    host = info["HostConfig"]
    assert host["Memory"] == host["MemorySwap"] == 256 * 1024 * 1024 and host["Init"] is True
    assert host["NetworkMode"] == "bridge" and info["Mounts"] == []


async def test_network_none_when_internet_not_allowed(factory):
    module, (ws, sid) = await factory(allow_internet=False)
    routes = await _sh(module.sandbox, ws, sid, "tail -n +2 /proc/net/route | wc -l")
    assert routes.stdout.strip() == "0"


async def test_timeout_kills_the_whole_process_group(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    started = time.monotonic()
    result = await _sh(sandbox, ws, sid, "(sleep 300 &) ; sleep 300 & echo started; wait", timeout=2)
    assert result.timed_out and "started" in result.stdout
    assert time.monotonic() - started < 10
    left = await _sh(sandbox, ws, sid, "ps -o args | grep -c '^sleep 300' || true")
    assert left.stdout.strip() == "0"  # 前台、后台、双重 fork 的子进程都被整组杀掉


async def test_in_container_watchdog_stops_runaway_commands(factory):
    module, (ws, sid) = await factory()
    started = time.monotonic()
    # 直接调用包装脚本、看门狗设 1 秒：不靠本进程的超时也能停下
    result = await module.sandbox.exec(ws, sid, ["sh", "-c", scripts.EXEC_WRAPPER, "kw", "manual", "1", "sleep", "60"],
                                       timeout_seconds=20, max_output_bytes=1000)
    assert not result.timed_out and result.exit_code == 137
    assert time.monotonic() - started < 10


async def test_output_cap_marks_truncation(factory):
    module, (ws, sid) = await factory()
    result = await module.sandbox.exec(ws, sid, ["sh", "-c", "head -c 100000 /dev/zero | tr '\\0' a; echo err >&2"],
                                       timeout_seconds=10, max_output_bytes=1000)
    assert result.truncated and len(result.stdout) == 1000 and result.stderr == "err\n" and result.exit_code == 0


async def test_read_delivered_regular_files_only(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    with pytest.raises(SandboxError):  # 没有沙箱时不为此创建容器
        await sandbox.read_delivered(ws, sid, "/workspace/a.txt", max_bytes=10)
    await _sh(sandbox, ws, sid, "cd /workspace && mkdir -p out && printf abc > out/a.txt "
                                "&& ln -s /etc/passwd link && printf 0123456789 > big.bin")
    assert await sandbox.read_delivered(ws, sid, "/workspace/out/a.txt", max_bytes=10) == b"abc"
    with pytest.raises(SandboxTooLarge):
        await sandbox.read_delivered(ws, sid, "/workspace/big.bin", max_bytes=5)
    with pytest.raises(SandboxError):
        await sandbox.read_delivered(ws, sid, "/workspace/link", max_bytes=1000)


async def test_idle_sweep_deletes_container_and_next_use_recreates(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    recreated = []

    async def hook(workspace_id, session_id):
        recreated.append(session_id)

    module.on_created(hook)
    await _sh(sandbox, ws, sid, "echo keep > /workspace/k")
    assert recreated == [sid]
    key = SandboxKey.of(ws, sid)
    module.sweeper._clock = lambda: time.time() + 4000  # 超过 1800 秒 TTL
    assert await module.sweeper.sweep() >= 1
    assert await module.engine.inspect_container(key.name) is None
    again = await _sh(sandbox, ws, sid, "cat /workspace/k 2>/dev/null || echo gone")
    assert again.stdout.strip() == "gone" and recreated == [sid, sid]  # 工作区不保留，钩子负责重放附件
    await sandbox.destroy(ws, sid)
    assert await module.engine.inspect_container(key.name) is None


async def test_stopped_container_keeps_files_and_is_restarted(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    await _sh(sandbox, ws, sid, "echo keep > /workspace/k")
    key = SandboxKey.of(ws, sid)
    client = docker.DockerClient.from_env(timeout=10)
    try:
        client.api.stop(key.name, timeout=1)
    finally:
        client.close()
    again = await _sh(sandbox, ws, sid, "cat /workspace/k")
    assert again.stdout == "keep\n"


async def test_session_root_links_to_the_workspace_for_host_paths(factory):
    """宿主的工作目录是 /sessions/<会话>，容器里它链接到 /workspace：命令里写这个绝对路径也能用。"""
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    result = await _sh(sandbox, ws, sid, f"echo hi > /sessions/{sid}/a.txt && cat /workspace/a.txt")
    assert result.exit_code == 0 and result.stdout == "hi\n", result.stderr


async def test_stat_path_follows_links_only_when_asked(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    root = await sandbox.stat_path(ws, sid, "/workspace", follow=True)
    assert root is not None and root.kind == "d" and root.version == "empty"  # 还没有容器时不为此新建
    await _sh(sandbox, ws, sid, "printf abc > /workspace/f && ln -s /workspace/f /workspace/l && mkdir /workspace/d")
    file = await sandbox.stat_path(ws, sid, "/workspace/l", follow=True)
    link = await sandbox.stat_path(ws, sid, "/workspace/l", follow=False)
    assert (file.kind, file.size) == ("f", 3) and link.kind == "l"
    assert (await sandbox.stat_path(ws, sid, "/workspace/d", follow=True)).kind == "d"
    assert await sandbox.stat_path(ws, sid, "/workspace/missing", follow=True) is None
    before = file.version
    await _sh(sandbox, ws, sid, "sleep 1.1; printf abcd > /workspace/f")
    assert (await sandbox.stat_path(ws, sid, "/workspace/f", follow=True)).version != before


async def test_exec_takes_workdir_and_environment(factory):
    module, (ws, sid) = await factory()
    sandbox = module.sandbox
    await _sh(sandbox, ws, sid, "mkdir -p /workspace/sub")
    result = await _sh(sandbox, ws, sid, 'pwd; echo "$KW_TEST_VALUE"', workdir="/workspace/sub",
                       env={"KW_TEST_VALUE": "中文 值"})
    assert result.stdout == "/workspace/sub\n中文 值\n", result.stderr
    with pytest.raises(SandboxError):
        await _sh(sandbox, ws, sid, "true", workdir="/etc")


@pytest.mark.skipif("knowwork-sandbox" not in IMAGE, reason="Office 转换需要带 LibreOffice Kit 的沙箱镜像")
async def test_convert_office_document_to_pdf(factory):
    # 原生 LibreOffice 转换的内存峰值约 190 MB，512 MiB 足够（官方 LibreOffice Kit 的 WASM 引擎要约 1.4 GB）
    module, (ws, sid) = await factory(exec_max_timeout_seconds=300, max_input_bytes=1 << 20, memory_bytes=512 << 20)
    sandbox = module.sandbox
    made = await _sh(sandbox, ws, sid, "python3 -c \"from docx import Document; d=Document(); "
                                       "d.add_heading('销售简报', 0); d.add_paragraph('华东 1200 万'); d.save('/workspace/a.docx')\"")
    assert made.exit_code == 0, made.stderr
    docx = await sandbox.read_delivered(ws, sid, "/workspace/a.docx", max_bytes=1 << 20)
    pdf = await sandbox.convert_to_pdf(ws, sid, "docx", docx, max_bytes=20 << 20, timeout_seconds=240)
    assert pdf.startswith(b"%PDF-") and len(pdf) > 1000
    leftovers = await _sh(sandbox, ws, sid, "ls /tmp | grep -c kw-convert || true")
    assert leftovers.stdout.strip() == "0", "转换的临时目录没删"
    # 原生 LibreOffice 认不出格式时按纯文本导入，照样出 PDF（预览成乱码文字），不报错
    with pytest.raises(SandboxError):
        await sandbox.convert_to_pdf(ws, sid, "exe", docx, max_bytes=20 << 20, timeout_seconds=240)


@pytest.mark.skipif("knowwork-sandbox" not in IMAGE, reason="Office 转换需要带 LibreOffice Kit 的沙箱镜像")
async def test_conversion_out_of_memory_is_explained(factory):
    module, (ws, sid) = await factory(exec_max_timeout_seconds=300, max_input_bytes=1 << 20, memory_bytes=64 << 20)
    sandbox = module.sandbox
    await _sh(sandbox, ws, sid, "python3 -c \"from docx import Document; Document().save('/workspace/a.docx')\"")
    docx = await sandbox.read_delivered(ws, sid, "/workspace/a.docx", max_bytes=1 << 20)
    with pytest.raises(SandboxError, match="内存不足"):
        await sandbox.convert_to_pdf(ws, sid, "docx", docx, max_bytes=20 << 20, timeout_seconds=240)


@pytest.mark.skipif("knowwork-sandbox" not in IMAGE, reason="Office 工具链只在 KnowWork 沙箱镜像里")
async def test_office_toolchain_used_by_the_bundled_skills(factory):
    """自带 Office 技能（照搬 KnowFlow）用到的工具都在镜像里、能跑通：docx-js 与 pptxgenjs 生成、修订改稿、
    公式重算、pandoc 读取、转 PDF 再出页面图片。"""
    module, (ws, sid) = await factory(exec_max_timeout_seconds=300, memory_bytes=1 << 30, pids_limit=256)
    sandbox = module.sandbox
    office = "/opt/knowwork/office"
    steps = {
        "docx-js": "cd /workspace/tmp && node -e \"const d=require('docx');const doc=new d.Document({sections:[{children:"
                   "[new d.Paragraph({children:[new d.TextRun('付款比例为 30%')]})]}]});"
                   "d.Packer.toBuffer(doc).then(b=>require('fs').writeFileSync('a.docx',b))\"",
        "pptxgenjs": "cd /workspace/tmp && node -e \"const P=require('pptxgenjs');const p=new P();"
                     "p.addSlide().addText('季度汇报',{x:1,y:1});p.writeFile({fileName:'a.pptx'})\"",
        "修订": f"cd /workspace/tmp && echo '[{{\"find\":\"30%\",\"replace\":\"20%\"}}]' > e.json "
                f"&& python3 {office}/docx_redline.py a.docx e.json b.docx && unzip -p b.docx word/document.xml | grep -c '<w:ins'",
        "pandoc": "cd /workspace/tmp && pandoc -t markdown --track-changes=all b.docx | head -5",
        "重算": f"cd /workspace/tmp && python3 -c \"from openpyxl import Workbook;w=Workbook();s=w.active;"
                f"s.append([1,2,'=A1+B1']);w.save('t.xlsx')\" && python3 {office}/recalc.py t.xlsx",
        "转 PDF 出图": f"cd /workspace/tmp && python3 {office}/soffice.py --headless --convert-to pdf --outdir . a.pptx "
                     "&& pdftoppm -jpeg -r 50 a.pdf slide && ls slide-*.jpg",
        "markitdown": "cd /workspace/tmp && markitdown a.pptx | head -3",
    }
    for name, command in steps.items():
        result = await _sh(sandbox, ws, sid, command, timeout=240)
        assert result.exit_code == 0, f"{name} 失败：{result.stdout[-500:]} {result.stderr[-800:]}"
        if name == "重算":
            assert '"success"' in result.stdout and '"total_errors": 0' in result.stdout, result.stdout
        if name == "修订":
            assert int(result.stdout.strip().splitlines()[-1]) >= 1, "修订没有生成 <w:ins>"
