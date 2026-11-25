"""试装上传的插件：在一次性的宿主里真的加载一遍，看它的组件激活了没有、给对话新增了哪些工具（插件方案 4.4 节第 2、3 项）。

- 用专属宿主同一套启动器起（容器或进程），不挂任何人的持久目录、不开会话，读完就停；
- 组件激活了没有，读宿主的插件清单（宿主照官方 dsh-host-plugin-inventory 的读法读加载器）：插件包配置补丁插入的那几行，
  启用着的都要是 active。激活失败（failed）的带着官方给的原因；模块都没导入进来的（没有 fiber）原因只在宿主日志里，
  从日志里找提到这个模块的几行；
- 新增了哪些工具：官方工具登记处不记是谁登记的，拿一个不带插件的宿主当基线（一个进程只起一次），比出多出来的名字。
  只看全局登记的工具；插件在开会话时才按会话登记的工具这里看不到；
- 宿主带着插件根本起不来：算插件的问题，原因取宿主最后的输出。基线宿主都起不来、或宿主没回清单：是试装环境的问题，
  抛 TrialUnavailable，由任务按服务问题重试。
"""

import asyncio
import contextlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ..kernel.host_client import HostKernel, PluginInventory
from .launcher import HostKey, HostLauncher, HostPlugin, LaunchFailed

TRIAL_WORKSPACE = "trial"
MAX_REASON = 480  # 登记表的原因列 500 字
MAX_LOG_LINES = 3
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class TrialUnavailable(Exception):
    """试装环境本身有问题（不是插件的错）。"""


@dataclass(frozen=True)
class TrialResult:
    ok: bool
    reason: str = ""
    tools: tuple[str, ...] = ()


def _clip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_REASON else text[:MAX_REASON - 1] + "…"


def _log_lines(logs: str, needle: str) -> str:
    """日志里提到 needle 的几行（去掉终端颜色）。"""
    lines = [_ANSI.sub("", line).strip() for line in logs.splitlines() if needle in line]
    return "；".join(dict.fromkeys(line for line in lines if line))[:MAX_REASON] if lines else ""


def judge(inventory: PluginInventory, logs: str, modules: Sequence[str], baseline: frozenset[str]) -> TrialResult:
    """modules 是插件包配置补丁插入的那几行的模块名（上传时静态读出的组件清单）。"""
    wanted = set(modules)
    rows = [entry for entry in inventory.entries if entry.module in wanted]
    tools = tuple(sorted(set(inventory.tools) - baseline))
    if wanted and not rows:
        return TrialResult(False, "宿主里找不到这个插件的组件：插件包的 dsh.bundle.patch 没有被加载", tools)
    problems = []
    for entry in (entry for entry in rows if entry.enabled and entry.phase != "active"):
        if entry.phase == "failed":
            problems.append(f"{entry.module} 激活失败：{entry.diagnostic or '没有给出原因'}")
        elif entry.phase is None:
            found = _log_lines(logs, entry.module)
            problems.append(f"{entry.module} 没有加载起来" + (f"：{found}" if found else "（模块导入失败）"))
        else:
            problems.append(f"{entry.module} 一直没有就绪（{entry.phase}）")
    return TrialResult(not problems, _clip("；".join(problems)), tools)


class PluginTrial:
    def __init__(self, launcher: HostLauncher) -> None:
        self._launcher = launcher
        self._baseline: frozenset[str] | None = None
        self._lock = asyncio.Lock()

    async def _read(self, key: HostKey, plugins: Sequence[HostPlugin]) -> tuple[PluginInventory, str]:
        launched = await self._launcher.launch(key, plugins, ephemeral=True)
        kernel = HostKernel(launched.base_url, launched.token)
        try:
            inventory = await kernel.plugin_inventory()
            logs = await launched.logs()
        except Exception as error:
            raise TrialUnavailable("宿主没有回插件清单") from error
        finally:
            await kernel.aclose()
            with contextlib.suppress(Exception):
                await launched.stop()
        return inventory, logs

    async def baseline(self) -> frozenset[str]:
        """不带插件的宿主登记了哪些工具（一个进程只起一次）。"""
        async with self._lock:
            if self._baseline is None:
                try:
                    inventory, _ = await self._read(HostKey(TRIAL_WORKSPACE, "baseline"), ())
                except LaunchFailed as error:
                    raise TrialUnavailable("试装用的宿主起不来") from error
                self._baseline = frozenset(inventory.tools)
            return self._baseline

    async def run(self, label: str, plugin: HostPlugin, modules: Sequence[str]) -> TrialResult:
        """label 区分同时在试的几个（容器名、临时目录用它），用登记表那一行的编号。"""
        baseline = await self.baseline()
        try:
            inventory, logs = await self._read(HostKey(TRIAL_WORKSPACE, label), (plugin,))
        except LaunchFailed as error:
            found = _log_lines(error.detail, "Error") or _ANSI.sub("", error.detail.strip().splitlines()[-1]
                                                                 if error.detail.strip() else "")
            return TrialResult(False, _clip("带着这个插件，宿主没有起来" + (f"：{found}" if found else "")))
        return judge(inventory, logs, modules, baseline)
