"""技能：ZIP 包安装校验、SKILL.md 解析、草稿与发布、按会话交付给宿主、运行事实。

主线整合：
- 建表：SKILL_TABLES（挂在 db.base.Base 上），行级安全：SKILL_RLS_TABLES；
- 公开应用：`include_router(build_router(skills.service, access_service, current_principal))`；
- 内部应用：`include_router(build_internal_router(skills.delivery))`；
- 管理端「最近运行」：`include_router(build_runs_router(skills.run_query, access_service, current_principal))`；
- 打开宿主会话时：`skills.delivery.issue_session_token(workspace_id, session_id, user_id)` 放进建会话请求的
  `skills.token`，过期前经宿主 `PUT /v1/sessions/{id}/skill-credential` 续期。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from .api import build_router
from .blobstore import BlobStore, LocalBlobStore, S3BlobStore, make_s3_client
from .catalog import SkillCatalog
from .claims import DEFAULT_BASIC_TOOLS
from .internal_api import SkillDelivery, build_internal_router
from .models import SKILL_RLS_TABLES, SKILL_TABLES
from .run_query import SkillRunQuery
from .runs import SkillRunStore
from .runs_api import build_runs_router
from .service import AccessChecker, SkillService
from .tokens import SkillTokenSigner


@dataclass(frozen=True)
class Skills:
    service: SkillService
    delivery: SkillDelivery
    runs: SkillRunStore
    run_query: SkillRunQuery


def build_skills(master_key: str, sessionmaker: async_sessionmaker, access: AccessChecker, blobs: BlobStore, *,
                 basic_tools: frozenset[str] = DEFAULT_BASIC_TOOLS, token_ttl_seconds: int = 900) -> Skills:
    catalog = SkillCatalog(sessionmaker, access, blobs)
    return Skills(
        service=SkillService(sessionmaker, access, blobs, basic_tools=basic_tools),
        delivery=SkillDelivery(catalog, SkillTokenSigner.from_master_key(master_key),
                               token_ttl_seconds=token_ttl_seconds),
        runs=SkillRunStore(sessionmaker),
        run_query=SkillRunQuery(sessionmaker),
    )


__all__ = ["SKILL_RLS_TABLES", "SKILL_TABLES", "BlobStore", "LocalBlobStore", "S3BlobStore", "Skills",
           "build_internal_router", "build_router", "build_runs_router", "build_skills", "make_s3_client"]
