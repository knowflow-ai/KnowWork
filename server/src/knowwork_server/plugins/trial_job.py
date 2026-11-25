"""试装上传的插件（通用任务框架上的 plugin.trial）。

开了个人专属宿主（KNOWWORK_PLUGIN_HOSTS 不是 off）时，上传或升级的插件先记成「检查中」，同一事务里排这条任务；worker 起
一次性的宿主真的加载一遍（hosts/trial.py），结果写回登记表：通过的记下新增的工具、变成可用，没通过的变成「没通过」并写明
原因。检查中、没通过的都不生效（UploadService.effective 只取可用的）。

没开专属宿主时服务端没有地方跑没审过的代码，不排任务，插件直接可用、只在桌面端的本机对话里生效（页面上写明没在服务端试装）。

载荷里带着压缩包摘要：执行时登记表那一行已经换了包（又传了一次）或删了，这条任务就作废，结果不写。
试装环境本身起不来按服务问题重试；重试用完就记成「没通过」，原因写明是没能试装、可以重新上传再试。
"""

import logging
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..db.base import in_workspace
from ..hosts.launcher import HostPlugin
from ..hosts.trial import TrialResult, TrialUnavailable
from ..jobs import Claim, Done, JobContext, JobError, JobFailure, JobKind, JobRunner
from .archive import MAX_ARCHIVE_BYTES
from .models import STATUS_CHECKING, STATUS_FAILED, STATUS_READY, PluginInstallation

log = logging.getLogger(__name__)

KIND = JobKind("plugin.trial", pool="core", max_tries=3, timeout_seconds=600.0, label="试装上传的插件")


class Trial(Protocol):
    async def run(self, label: str, plugin: HostPlugin, modules: tuple[str, ...]) -> TrialResult: ...


class Blobs(Protocol):
    async def get(self, digest: str, *, max_bytes: int) -> bytes: ...


async def _settle(session: AsyncSession, workspace_id: str, plugin_id: str, digest: str, values: dict) -> None:
    """只改还在检查这份包的那一行：中途又传了一次（摘要变了）或删了，不写。"""
    await set_workspace(session, workspace_id)
    await session.execute(update(PluginInstallation).where(
        PluginInstallation.workspace_id == workspace_id, PluginInstallation.id == plugin_id,
        PluginInstallation.archive_digest == digest, PluginInstallation.status == STATUS_CHECKING).values(**values))


class TrialJob:
    def __init__(self, sessionmaker: async_sessionmaker, blobs: Blobs, trial: Trial) -> None:
        self._sessions = sessionmaker
        self._blobs = blobs
        self._trial = trial

    def register(self, runner: JobRunner) -> None:
        runner.register(KIND, self.handle, self.give_up)

    async def handle(self, ctx: JobContext) -> Done | None:
        digest = str(ctx.payload.get("digest") or "")
        with in_workspace(ctx.workspace_id):
            async with self._sessions() as session:
                row = await session.get(PluginInstallation, ctx.subject_id)
        if row is None or row.archive_digest != digest or row.status != STATUS_CHECKING:
            return None
        modules = tuple(dict.fromkeys(str(item.get("module") or "") for item in row.components or ()
                                      if isinstance(item, dict) and item.get("module")))
        blobs = self._blobs

        async def load() -> bytes:
            return await blobs.get(digest, max_bytes=MAX_ARCHIVE_BYTES)

        async with ctx.stage("trial") as stage:
            try:
                result = await self._trial.run(row.id.removeprefix("pi_")[:16], HostPlugin(row.plugin, digest, load),
                                               modules)
            except TrialUnavailable as error:
                raise JobError("trial_unavailable", "试装用的宿主起不来，稍后自动重试", detail=str(error)) from error
            stage.record(ok=result.ok, tools=len(result.tools))
        values = {"status": STATUS_READY if result.ok else STATUS_FAILED, "status_reason": result.reason,
                  "tool_names": list(result.tools)}
        workspace_id, plugin_id = ctx.workspace_id, ctx.subject_id

        async def apply(session: AsyncSession) -> None:
            await _settle(session, workspace_id, plugin_id, digest, values)

        return Done(apply=apply, note=None if result.ok else "plugin_rejected")

    async def give_up(self, session: AsyncSession, claim: Claim, failure: JobFailure) -> None:
        reason = f"没能试装（{failure.message}），可以重新上传再试；一直这样请联系系统管理员"
        await _settle(session, claim.workspace_id, claim.subject_id, str(claim.payload.get("digest") or ""),
                      {"status": STATUS_FAILED, "status_reason": reason[:480]})
