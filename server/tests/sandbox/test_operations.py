"""合同操作与工作区文件：执行身份默认 root、附件经 archive 放入、产物清单与读取、exec 超时整组杀与截断、文件读写列目录。"""

import asyncio

import pytest

from knowwork_server.sandbox import scripts
from knowwork_server.sandbox.containers import SandboxKey
from knowwork_server.sandbox.contract import SandboxError, SandboxFile, SandboxTooLarge
from knowwork_server.sandbox.execution import WATCHDOG_GRACE_SECONDS

from .fake_docker import Behavior, FakeDocker, tar_members, tar_payload
from .helpers import SID, WS

pytestmark = pytest.mark.anyio


def script_responder(table, users=None):
    """按脚本常量分派：argv 形如 ["sh", "-c", 脚本, "kw", 参数…]。"""
    def respond(argv, user):
        if users is not None:
            users.append(user)
        if len(argv) > 2 and argv[2] in table:
            return table[argv[2]](argv[4:])
        return Behavior()
    return respond


def listing(*entries):
    return b"".join(f"{kind}\0{size}\0{name}\0".encode() for kind, size, name in entries)


async def _touch(module):
    await module.sandbox.exec(WS, SID, ["true"], timeout_seconds=1, max_output_bytes=10)


async def test_exec_runs_as_root_in_workspace_by_default(make):
    fake = FakeDocker(responder=lambda argv, user: Behavior(stdout="你好\n".encode() * 5, stderr=b"warn", exit_code=2))
    fake, module = make(fake)
    result = await module.sandbox.exec(WS, SID, ["python", "x.py"], timeout_seconds=5, max_output_bytes=1000)
    assert (result.exit_code, result.stdout, result.stderr) == (2, "你好\n" * 5, "warn")
    assert not result.timed_out and not result.truncated
    record = fake.exec_by_argv(["python", "x.py"])
    assert record.user == "root" and record.workdir == "/workspace"
    assert record.cmd[:3] == ["sh", "-c", scripts.EXEC_WRAPPER]
    assert record.cmd[5] == str(5 + WATCHDOG_GRACE_SECONDS)  # 容器内看门狗
    assert scripts.ACTIVITY_MARKER in scripts.EXEC_WRAPPER


async def test_exec_user_can_be_chosen_per_call_or_by_config(make):
    fake, module = make()
    await module.sandbox.exec(WS, SID, ["id"], timeout_seconds=1, max_output_bytes=10, user="1000:1000")
    assert fake.exec_by_argv(["id"]).user == "1000:1000"
    with pytest.raises(SandboxError, match="账号"):
        await module.sandbox.exec(WS, SID, ["id"], timeout_seconds=1, max_output_bytes=10, user=" ")
    fake2, module2 = make(exec_user="1000:1000")
    await module2.sandbox.exec(WS, SID, ["whoami"], timeout_seconds=1, max_output_bytes=10)
    assert fake2.exec_by_argv(["whoami"]).user == "1000:1000"


async def test_put_input_places_file_via_archive(make):
    fake, module = make(max_input_bytes=10)
    result = await module.sandbox.put_input(WS, SID, "报告.pdf", b"12345")
    assert result == SandboxFile(name="报告.pdf", size=5)
    _cid, path, data = fake.archives[-1]
    member = tar_members(data)[0]
    assert path == "/" and member.name == "workspace/input/报告.pdf"
    assert (member.uid, member.gid, oct(member.mode)) == (0, 0, "0o444")
    assert tar_payload(data, member.name) == b"12345"


async def test_put_input_owner_follows_exec_user(make):
    fake, module = make(exec_user="1000")
    await module.sandbox.put_input(WS, SID, "a", b"x")
    member = tar_members(fake.archives[-1][2])[0]
    assert (member.uid, member.gid) == (1000, 1000)


@pytest.mark.parametrize("name", ["", ".", "..", "a/b", "x\n", "a" * 300])
async def test_put_input_rejects_bad_names(make, name):
    _fake, module = make()
    with pytest.raises(SandboxError, match="文件名"):
        await module.sandbox.put_input(WS, SID, name, b"x")


async def test_put_input_rejects_oversize(make):
    _fake, module = make(max_input_bytes=3)
    with pytest.raises(SandboxTooLarge):
        await module.sandbox.put_input(WS, SID, "a", b"1234")


async def test_read_delivered_without_sandbox_does_not_create_one(make):
    fake, module = make()
    with pytest.raises(SandboxError, match="还没有沙箱"):
        await module.sandbox.read_delivered(WS, SID, "/workspace/a.txt", max_bytes=10)
    assert "create" not in fake.calls


async def test_read_delivered_limits_and_errors(make):
    table = {scripts.READ_DELIVERED: lambda args: Behavior(stdout=b"abc") if args[0] == "/workspace/ok"
             else Behavior(exit_code={"/workspace/big": 9, "/workspace/gone": 3}[args[0]])}
    _fake, module = make(FakeDocker(responder=script_responder(table)))
    await _touch(module)
    assert await module.sandbox.read_delivered(WS, SID, "/workspace/ok", max_bytes=10) == b"abc"
    with pytest.raises(SandboxTooLarge):
        await module.sandbox.read_delivered(WS, SID, "/workspace/big", max_bytes=10)
    with pytest.raises(SandboxError, match="不存在"):
        await module.sandbox.read_delivered(WS, SID, "/workspace/gone", max_bytes=10)
    with pytest.raises(SandboxTooLarge):  # 文件在检查后又变大：输出被截断
        await module.sandbox.read_delivered(WS, SID, "/workspace/ok", max_bytes=2)
    with pytest.raises(SandboxTooLarge):
        await module.sandbox.read_delivered(WS, SID, "/workspace/ok", max_bytes=0)
    with pytest.raises(SandboxError):
        await module.sandbox.read_delivered(WS, SID, "/etc/passwd", max_bytes=10)


async def test_exec_truncates_output_per_stream(make):
    _fake, module = make(FakeDocker(responder=lambda argv, user: Behavior(stdout=b"x" * 100, stderr=b"y" * 3)))
    result = await module.sandbox.exec(WS, SID, ["cat"], timeout_seconds=5, max_output_bytes=10)
    assert result.truncated and result.stdout == "x" * 10 and result.stderr == "yyy"


async def test_exec_timeout_kills_process_group(make):
    fake, module = make(FakeDocker(responder=lambda argv, user: Behavior(stdout=b"started", hang=True)))
    result = await module.sandbox.exec(WS, SID, ["sleep", "100"], timeout_seconds=0.3, max_output_bytes=100)
    assert result.timed_out and result.stdout == "started" and result.exit_code == 137
    assert fake.kills == [fake.exec_by_argv(["sleep", "100"]).token]


async def test_exec_stream_that_survives_the_kill_is_closed(make, monkeypatch):
    monkeypatch.setattr("knowwork_server.sandbox.execution.DRAIN_SECONDS", 0.1)
    monkeypatch.setattr("knowwork_server.sandbox.execution.SETTLE_SECONDS", 0.05)
    _fake, module = make(FakeDocker(responder=lambda argv, user: Behavior(hang=True, ignore_kill=True)))
    started = asyncio.get_running_loop().time()
    result = await module.sandbox.exec(WS, SID, ["sleep", "100"], timeout_seconds=0.2, max_output_bytes=100)
    assert result.timed_out and asyncio.get_running_loop().time() - started < 3


async def test_cancelled_exec_still_kills_process_group(make):
    fake, module = make(FakeDocker(responder=lambda argv, user: Behavior(hang=True)))
    task = asyncio.create_task(module.sandbox.exec(WS, SID, ["sleep", "9"], timeout_seconds=60,
                                                   max_output_bytes=10))
    for _ in range(100):
        await asyncio.sleep(0.01)
        if any(record.running for record in fake.execs.values()):
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(fake.kills) == 1


async def test_exec_stream_failure_maps_to_unavailable(make):
    fake, module = make()
    await _touch(module)
    fake.connect_error = True
    with pytest.raises(SandboxError, match="暂时不可用"):
        await _touch(module)


@pytest.mark.parametrize("command,timeout,cap", [([], 1, 10), (["a\x00"], 1, 10), (["a"], 0, 10),
                                                 (["a"], 601, 10), (["a"], 1, 0)])
async def test_exec_validates_arguments(make, command, timeout, cap):
    _fake, module = make()
    with pytest.raises(SandboxError):
        await module.sandbox.exec(WS, SID, command, timeout_seconds=timeout, max_output_bytes=cap)


async def test_read_file_parses_size_header(make):
    table = {scripts.READ_FILE: lambda args: Behavior(stdout=b"10\n" + b"0123456789"[int(args[1]):][:int(args[2])])}
    _fake, module = make(FakeDocker(responder=script_responder(table)))
    chunk = await module.sandbox.read_file(WS, SID, "notes/a.txt", offset=4, limit=3)
    assert (chunk.path, chunk.size, chunk.offset, chunk.data) == ("/workspace/notes/a.txt", 10, 4, b"456")


@pytest.mark.parametrize("code,message", [(3, "不存在"), (4, "目录"), (7, "权限"), (42, "失败")])
async def test_read_file_errors(make, code, message):
    table = {scripts.READ_FILE: lambda args: Behavior(exit_code=code)}
    _fake, module = make(FakeDocker(responder=script_responder(table)))
    with pytest.raises(SandboxError, match=message):
        await module.sandbox.read_file(WS, SID, "a", offset=0, limit=10)


async def test_read_file_garbage_header_and_bad_range(make):
    table = {scripts.READ_FILE: lambda args: Behavior(stdout=b"nope")}
    _fake, module = make(FakeDocker(responder=script_responder(table)))
    with pytest.raises(SandboxError):
        await module.sandbox.read_file(WS, SID, "a", offset=0, limit=10)
    with pytest.raises(SandboxError, match="范围"):
        await module.sandbox.read_file(WS, SID, "a", offset=-1, limit=10)


async def test_write_file_prepares_as_exec_user_then_extracts_into_parent(make):
    prepared, users = [], []
    table = {scripts.PREPARE_WRITE: lambda args: prepared.append(args) or Behavior()}
    fake, module = make(FakeDocker(responder=script_responder(table, users)))
    assert await module.sandbox.write_file(WS, SID, "out/r.md", "内容".encode()) == 6
    assert prepared == [["/workspace/out/r.md"]] and users[-1] == "root"
    _cid, path, data = fake.archives[-1]
    member = tar_members(data)[0]
    assert path == "/workspace/out" and member.name == "r.md" and (member.uid, member.mode) == (0, 0o644)
    assert tar_payload(data, "r.md") == "内容".encode()


async def test_write_file_failures(make):
    table = {scripts.PREPARE_WRITE: lambda args: Behavior(exit_code=6)}
    fake, module = make(FakeDocker(responder=script_responder(table)), max_write_bytes=4)
    with pytest.raises(SandboxError, match="权限"):
        await module.sandbox.write_file(WS, SID, "a", b"x")
    assert fake.archives == []
    with pytest.raises(SandboxTooLarge):
        await module.sandbox.write_file(WS, SID, "a", b"12345")
    for bad in ("/workspace/input/a", "/etc/passwd", "../x", "/workspace", "/workspace/output"):
        with pytest.raises(SandboxError):
            await module.sandbox.write_file(WS, SID, bad, b"x")


async def test_list_dir(make):
    table = {scripts.LIST_DIR: lambda args: Behavior(stdout=listing(("d", 0, "output"), ("d", 0, "input")))}
    _fake, module = make(FakeDocker(responder=script_responder(table)))
    assert [e.name for e in await module.sandbox.list_dir(WS, SID, "/workspace")] == ["input", "output"]
    table[scripts.LIST_DIR] = lambda args: Behavior(exit_code=3)
    with pytest.raises(SandboxError, match="目录"):
        await module.sandbox.list_dir(WS, SID, "nope")


@pytest.mark.parametrize("status", [500, 503])
async def test_docker_unavailable_maps_to_generic_message(make, status):
    fake, module = make()
    fake.fail_all = status
    with pytest.raises(SandboxError) as caught:
        await _touch(module)
    assert str(caught.value) == "沙箱服务暂时不可用，请稍后再试"
    assert SandboxKey.of(WS, SID).name not in str(caught.value)
