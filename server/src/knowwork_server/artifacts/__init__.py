"""会话产物（技术方案 9.2 节，F-ART-1/2/3/5）：回合结束收沙箱 output、上限告知、公式转义、实时鉴权下载。

装配：
    artifacts = build_artifacts(sessionmaker, stores, sandbox)
    app.include_router(artifacts_router(artifacts.service, conversation_service, actor))
    eraser = artifacts.service.erase                                     # 交给 ConversationService(erasers=...)
    result = await artifacts.collector.collect(workspace_id, session_id)  # run.completed 时调用
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores
from .api import build_router
from .collector import DEFAULT_LIMITS, ArtifactCollector, ArtifactLimits, ArtifactView, CollectResult, Notice
from .models import ARTIFACT_RLS_TABLES, ARTIFACT_TABLES, Artifact
from .service import ArtifactNotFound, ArtifactService, ArtifactUnavailable


@dataclass(frozen=True)
class Artifacts:
    collector: ArtifactCollector
    service: ArtifactService


def build_artifacts(sessionmaker: async_sessionmaker, stores: SessionStores, sandbox: SessionSandbox,
                    limits: ArtifactLimits = DEFAULT_LIMITS) -> Artifacts:
    return Artifacts(collector=ArtifactCollector(sessionmaker, stores, sandbox, limits),
                     service=ArtifactService(sessionmaker, stores))


__all__ = [
    "ARTIFACT_RLS_TABLES",
    "ARTIFACT_TABLES",
    "Artifact",
    "ArtifactCollector",
    "ArtifactLimits",
    "ArtifactNotFound",
    "ArtifactService",
    "ArtifactUnavailable",
    "ArtifactView",
    "Artifacts",
    "CollectResult",
    "Notice",
    "build_artifacts",
    "build_router",
]
