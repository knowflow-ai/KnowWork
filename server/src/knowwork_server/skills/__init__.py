"""技能：ZIP 包安装校验、SKILL.md 解析、草稿与发布、按会话交付给宿主、运行事实。

主线整合：
- 建表：SKILL_TABLES（挂在 db.base.Base 上），行级安全：SKILL_RLS_TABLES；
- 公开应用：`include_router(build_router(skills, access_service, current_principal))`（技能、我能用的），
  `include_router(build_submissions_router(skills.submissions, access_service, current_principal))`（提交与审核），
  `include_router(build_layers_router(skills.layers, access_service, current_principal))`（本空间的企业技能与内置技能）；
- 移出成员时清理个人技能：`access_service.on_member_removed(skills.service.forget_member)`；
- 沙箱执行命令时带上技能的环境变量：`build_internal_router(..., skill_env=skills.environment.for_exec)`（sandbox）；
- 内部应用：`include_router(build_internal_router(skills.delivery))`；
- 管理端「最近运行」：`include_router(build_runs_router(skills.run_query, access_service, current_principal))`；
- 打开宿主会话时：`skills.delivery.issue_session_token(workspace_id, session_id, user_id)` 放进建会话请求的
  `skills.token`，过期前经宿主 `PUT /v1/sessions/{id}/skill-credential` 续期。
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from .api import build_router
from .available import SkillAvailability
from .blobstore import BlobStore, LocalBlobStore, S3BlobStore, make_s3_client
from .bundled import BundledFetch, BundledSkills
from .catalog import SkillCatalog
from .claims import DEFAULT_BASIC_TOOLS
from .env import EnvBox
from .environment import SkillEnvironment
from .internal_api import SkillDelivery, build_internal_router
from .layers import UpperSkills
from .layers_api import build_layers_router
from .models import SKILL_RLS_TABLES, SKILL_TABLES
from .platform import PlatformSkillAdmin
from .run_query import SkillRunQuery
from .runs import SkillRunStore
from .runs_api import build_runs_router
from .service import AccessChecker, SkillService
from .submissions import SubmissionService
from .submissions_api import build_submissions_router
from .tokens import SkillTokenSigner


@dataclass(frozen=True)
class Skills:
    service: SkillService
    submissions: SubmissionService
    available: SkillAvailability
    layers: UpperSkills  # 空间管理员看与停用本空间的企业技能、内置技能
    platform: PlatformSkillAdmin  # 系统管理员管企业技能（给扩展的企业技能管理视图用）
    environment: SkillEnvironment  # 会话里加载过的技能跑命令时带上的环境变量（沙箱执行接口调）
    delivery: SkillDelivery
    runs: SkillRunStore
    run_query: SkillRunQuery


def build_skills(master_key: str, sessionmaker: async_sessionmaker, access: AccessChecker, blobs: BlobStore, *,
                 basic_tools: frozenset[str] = DEFAULT_BASIC_TOOLS, token_ttl_seconds: int = 900,
                 bundled: BundledFetch | None = None) -> Skills:
    """bundled：问宿主要内置技能清单（内核合同的 bundled_skills）；不给就不列内置技能。"""
    catalog = SkillCatalog(sessionmaker, access, blobs)
    env_box = EnvBox.from_master_key(master_key)
    service = SkillService(sessionmaker, access, blobs, basic_tools=basic_tools, env_box=env_box)
    bundled_skills = BundledSkills(bundled)
    return Skills(
        service=service,
        submissions=SubmissionService(sessionmaker, access, blobs, limits=service.limits),
        available=SkillAvailability(catalog, bundled_skills),
        layers=UpperSkills(sessionmaker, access, bundled_skills),
        platform=PlatformSkillAdmin(sessionmaker, access, service),
        environment=SkillEnvironment(sessionmaker, catalog, env_box),
        delivery=SkillDelivery(catalog, SkillTokenSigner.from_master_key(master_key),
                               token_ttl_seconds=token_ttl_seconds),
        runs=SkillRunStore(sessionmaker),
        run_query=SkillRunQuery(sessionmaker),
    )


__all__ = ["SKILL_RLS_TABLES", "SKILL_TABLES", "BlobStore", "LocalBlobStore", "PlatformSkillAdmin", "S3BlobStore",
           "Skills", "build_internal_router", "build_layers_router", "build_router", "build_runs_router",
           "build_skills", "build_submissions_router", "make_s3_client"]
