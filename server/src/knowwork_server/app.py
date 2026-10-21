"""应用装配：公开应用（对浏览器与 SDK）与内部应用（只给宿主回调）共用一个运行时容器。

部署时两个应用监听不同端口；内部端口只在容器内网，网关不转发。生产入口见 __main__.py。
"""

from dataclasses import dataclass

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from .access.api import build_router as access_router
from .access.members import DEFAULT_WORKSPACE_ID
from .access.service import AccessService
from .access.vocab import WORKSPACE_RESOURCE, Action
from .approvals import SessionGrants
from .artifacts import Artifacts, build_artifacts
from .artifacts.api import build_router as artifacts_router
from .artifacts.observer import ArtifactObserver
from .attachments import Attachments, build_attachments
from .attachments.api import build_router as attachments_router
from .attachments.turns import TurnAttachments
from .audit import AUDIT_SCOPE, AuditModule, build_audit
from .audit import build_router as audit_router
from .config import Settings
from .conversations.api import build_router as conversation_router
from .conversations.listeners import Broadcaster
from .conversations.models import Conversation
from .conversations.observers import fan_out
from .conversations.pump import EventPump
from .conversations.replay import EventFeed
from .conversations.service import ConversationService
from .conversations.tool_credentials import ToolCredentials, require_membership
from .conversations.writer import BatchWriter
from .db.base import Base, make_engine, make_sessionmaker, make_workspace_sessionmaker
from .db.migrate import migrate_for_settings
from .db.registry import load_all_models
from .debugview import DebugService, RunRecords, build_admin_router, build_debugview, build_run_records
from .debugview import build_router as debug_router
from .documents import PreviewService
from .documents import build_router as documents_router
from .gateway.credential_rotation import CredentialRotator
from .gateway.model_gateway import MODEL_AUDIENCE, GatewayLimits
from .gateway.model_gateway import build_router as gateway_router
from .identity.api import build_router as identity_router
from .identity.config import IdentityConfig, OidcConfig
from .identity.container import Identity, build_identity
from .kernel.host_client import HostKernel
from .kernel.tokens import TokenSigner, derive_key
from .knowledge import KnowledgeModule, build_knowledge
from .knowledge.api import build_router as knowledge_router
from .knowledge.citation_api import build_router as citations_router
from .knowledge.citation_observer import CitationObserver
from .models import ModelsService, build_models_service
from .models.api import build_router as models_router
from .models.observer import UsageObserver
from .ops.health import build_router as health_router
from .ops.health import database_check, host_check, host_dsh_version
from .principals import (
    KNOWLEDGE_SCOPE,
    MODELS_SCOPE,
    TOOLS_SCOPE,
    WORKSPACE_SCOPE,
    bound_principal,
    conversation_actor,
    scoped_principal,
    workspace_admin,
    workspace_member,
)
from .profile import AuthorizedToolRegistry, ToolClearance, build_profiles
from .sandbox import EXECUTION_TOOLS, SandboxModule, SandboxSettings, build_sandbox
from .sandbox.internal_api import build_internal_router as sandbox_internal_router
from .sandbox.unavailable import UnavailableSandbox
from .sessionfiles.storage import local_session_stores, s3_session_stores
from .setup import build_setup_service
from .setup.api import build_router as setup_router
from .skills import Skills, build_skills
from .skills import build_runs_router as skill_runs_router
from .skills.api import build_router as skills_router
from .skills.blobstore import BlobStore, LocalBlobStore, S3BlobStore, make_s3_client
from .skills.claims import DEFAULT_BASIC_TOOLS
from .skills.internal_api import build_internal_router as skills_internal_router
from .skills.projector import LoggedTurns, SkillRunProjector
from .system import BrandService, SystemInfoService
from .system import build_router as system_router
from .tools import build_tool_service
from .tools.api import build_internal_router as tools_router
from .tools.mcp import McpModule, build_mcp
from .tools.mcp.api import build_router as mcp_router
from .tools.registry import KW_NOW, CompositeToolRegistry, StaticToolRegistry
from .tools.service import ToolService
from .workflows import STALE_AFTER, Workflows, build_workflows
from .workflows.api import build_router as workflows_router


@dataclass
class Runtime:
    settings: Settings
    kernel: HostKernel
    signer: TokenSigner
    writer: BatchWriter
    broadcaster: Broadcaster
    pump: EventPump
    service: ConversationService
    rotator: CredentialRotator
    tool_credentials: ToolCredentials
    skill_credentials: ToolCredentials
    skills: Skills
    blobs: BlobStore
    audit: AuditModule
    sandbox: SandboxModule | None  # 没打开 Docker 沙箱时为空：宿主的执行与文件工具进每个会话的 deny
    attachments: Attachments
    artifacts: Artifacts
    previews: PreviewService
    artifact_observer: ArtifactObserver
    citation_observer: CitationObserver
    skill_runs: SkillRunProjector
    workflows: Workflows
    grants: SessionGrants
    tools: ToolService
    identity: Identity
    access: AccessService
    models: ModelsService
    usage: UsageObserver
    knowledge: KnowledgeModule
    mcp: McpModule
    debug: DebugService
    runs: RunRecords
    # 不声明工作空间：只能读写全局表（账号、工作空间等）；读行级安全表一行都看不到，要用声明空间的工厂
    global_sessionmaker: async_sessionmaker
    engine: AsyncEngine
    upstream: httpx.AsyncClient
    # 按模型目录转发到各厂商、做连通测试：不带 base_url 与默认鉴权头，免得把固定上游的密钥带给别的厂商
    routed_http: httpx.AsyncClient
    host_http: httpx.AsyncClient  # 就绪探针查宿主 /health 用
    oidc_http: httpx.AsyncClient | None

    async def start(self, *, create_schema: bool = False) -> None:
        if create_schema or self.settings.create_schema:
            load_all_models()
            async with self.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        elif self.settings.auto_migrate:
            await migrate_for_settings(self.settings)
        ensure_bucket = getattr(self.blobs, "ensure_bucket", None)
        if ensure_bucket is not None:
            await ensure_bucket()
        await self.workflows.engine.reconcile(STALE_AFTER)  # 上次没跑完的运行标为中断，可续跑
        if not await self.access.reload():
            raise RuntimeError("权限规则加载失败，拒绝启动")
        self.writer.start()
        self.audit.start()
        if self.sandbox is not None:
            self.sandbox.start()
        await self.rotator.start()
        self.tool_credentials.start()
        self.skill_credentials.start()

    async def stop(self) -> None:
        await self.workflows.engine.aclose()
        await self.tool_credentials.stop()
        await self.skill_credentials.stop()
        await self.rotator.stop()
        await self.pump.close()
        await self.usage.drain()
        await self.artifact_observer.drain()
        await self.citation_observer.drain()
        await self.skill_runs.drain()  # 它等本轮的帧落库，要在关落库器之前
        self.broadcaster.close_all()
        await self.writer.close()
        await self.kernel.aclose()
        await self.upstream.aclose()
        await self.routed_http.aclose()
        await self.host_http.aclose()
        await self.knowledge.client.aclose()
        if self.oidc_http is not None:
            await self.oidc_http.aclose()
        await self.audit.close()
        if self.sandbox is not None:
            await self.sandbox.aclose()
        await self.attachments.aclose()
        await self.engine.dispose()


def identity_config(settings: Settings) -> IdentityConfig:
    oidc = None
    if settings.oidc_issuer:
        missing = [name for name, value in (("KNOWWORK_OIDC_CLIENT_ID", settings.oidc_client_id),
                                            ("KNOWWORK_OIDC_REDIRECT_URI", settings.oidc_redirect_uri))
                   if not value]
        if missing:
            raise ValueError(f"已配置 OIDC 签发方，但缺少 {'、'.join(missing)}")
        oidc = OidcConfig(issuer=settings.oidc_issuer, client_id=settings.oidc_client_id,
                          client_secret=settings.oidc_client_secret.get_secret_value(),
                          redirect_uri=settings.oidc_redirect_uri)
    return IdentityConfig(public_base_url=settings.public_base_url, cookie_secure=settings.cookie_secure, oidc=oidc)


def _session_credentials(kernel: HostKernel, tools: ToolService, skills: Skills, access: AccessService,
                         settings: Settings) -> tuple[ToolCredentials, ToolCredentials]:
    """工具桥与技能两张会话令牌，共用同一套续期与成员复核。

    建会话、恢复与每轮续期都复核成员身份：被移出工作空间的用户拿不到新令牌，正在跑的回合被取消。
    判定器不健康时 check 一律为假，那不是"权限被收回"：返回 None（未知），按签发失败处理，不取消回合。
    """

    def is_member(conversation: Conversation) -> bool | None:
        if not access.engine.healthy:
            return None
        return access.check(conversation.user_id, conversation.workspace_id, WORKSPACE_RESOURCE, Action.USE)

    async def issue_tools(conversation: Conversation) -> str:
        return await tools.issue_session_token(conversation.workspace_id, conversation.id,
                                               user_id=conversation.user_id)

    options = {"interval": max(settings.tool_token_ttl_seconds // 3, 20),
               "call_timeout": settings.tool_token_call_timeout_seconds,
               "concurrency": settings.tool_token_renew_concurrency}
    tool_credentials = ToolCredentials(kernel, require_membership(issue_tools, is_member), **options)
    skill_credentials = ToolCredentials(kernel, require_membership(skills.delivery.issuer(), is_member),
                                        rotate=kernel.rotate_skill_credential, label="skill", **options)
    return tool_credentials, skill_credentials


def _s3_client(settings: Settings):
    return make_s3_client(endpoint_url=settings.s3_endpoint_url,
                          access_key=settings.s3_access_key.get_secret_value(),
                          secret_key=settings.s3_secret_key.get_secret_value())


def _blob_store(settings: Settings) -> BlobStore:
    if settings.s3_endpoint_url:
        return S3BlobStore(_s3_client(settings), settings.s3_bucket)
    return LocalBlobStore(settings.blob_dir)


def _session_stores(settings: Settings):
    if settings.s3_endpoint_url:
        return s3_session_stores(_s3_client(settings), settings.s3_bucket)
    return local_session_stores(f"{settings.blob_dir.rstrip('/')}/session-files")


def _usage_observer(kernel: HostKernel, scoped: async_sessionmaker, models: ModelsService,
                    settings: Settings) -> UsageObserver:
    async def owner_of(conversation_id: str) -> str | None:
        async with scoped() as session:
            conversation = await session.get(Conversation, conversation_id)
        return conversation.user_id if conversation is not None else None

    async def cancel_turn(conversation_id: str) -> None:
        await kernel.cancel(conversation_id)

    return UsageObserver(models.recorder, owner_of, cancel_turn, default_model=settings.host_model_alias,
                         max_calls_per_turn=settings.model_max_calls_per_turn)


def build_runtime(settings: Settings) -> Runtime:
    config = identity_config(settings)  # 先校验配置，失败时还没建任何连接
    master_key = settings.master_key.get_secret_value()
    engine = make_engine(settings.database_url)
    global_sessionmaker = make_sessionmaker(engine)
    # 开源版只有默认空间：对话与工具回执的每个事务都声明它，行级安全据此放行
    scoped = make_workspace_sessionmaker(engine, DEFAULT_WORKSPACE_ID)
    kernel = HostKernel(settings.host_base_url, settings.host_token.get_secret_value())
    signer = TokenSigner(derive_key(master_key, "host-callback"))

    def issue_model_token() -> str:
        return signer.issue(audience=MODEL_AUDIENCE, workspace_id=DEFAULT_WORKSPACE_ID,
                            ttl_seconds=settings.model_token_ttl_seconds)

    rotator = CredentialRotator(kernel.rotate_model_credential, issue_model_token,
                                interval=max(settings.model_token_ttl_seconds // 3, 20))
    access = AccessService(global_sessionmaker)
    knowledge = build_knowledge(master_key, global_sessionmaker, access)
    sandbox = build_sandbox(SandboxSettings())
    # 内置工具（时间、知识检索、沙箱）只要求是空间成员；MCP 工具要 tool:<名字> 授权（默认只有 owner、admin）。
    # 执行时注册表按当前授权再判一次，权限收回立即生效。内置工具排在前面，外部工具不能顶替同名的内置工具。
    builtin_tools = (KW_NOW, *knowledge.tools)
    clearance = ToolClearance(access, builtin=frozenset(tool.spec.name for tool in builtin_tools))
    mcp = build_mcp(master_key, global_sessionmaker, access)
    registry = AuthorizedToolRegistry(CompositeToolRegistry(StaticToolRegistry.of(builtin_tools), mcp.registry),
                                      clearance)
    audit = build_audit(global_sessionmaker, default_workspace_id=DEFAULT_WORKSPACE_ID)
    tools = build_tool_service(master_key, scoped, registry=registry, token_ttl_seconds=settings.tool_token_ttl_seconds,
                               audit=audit.tool_hook)
    blobs = _blob_store(settings)
    skills = build_skills(master_key, global_sessionmaker, access, blobs,
                          basic_tools=DEFAULT_BASIC_TOOLS | {tool.spec.name for tool in knowledge.tools},
                          token_ttl_seconds=settings.tool_token_ttl_seconds)
    tool_credentials, skill_credentials = _session_credentials(kernel, tools, skills, access, settings)
    models = build_models_service(global_sessionmaker, access, master_key, timezone=settings.usage_timezone)
    usage = _usage_observer(kernel, scoped, models, settings)
    stores = _session_stores(settings)
    session_sandbox = sandbox.sandbox if sandbox else UnavailableSandbox()
    attachments = build_attachments(scoped, stores, session_sandbox)
    if sandbox is not None:
        # 容器被空闲清扫或配置变更删掉后工作区是空的；新建时把已就绪的附件重新放进去
        sandbox.on_created(attachments.service.restage)
    artifacts = build_artifacts(scoped, stores, session_sandbox)
    previews = PreviewService(artifacts.service, session_sandbox)
    artifact_observer = ArtifactObserver(artifacts.collector)
    citation_observer = CitationObserver(knowledge.citations)
    writer = BatchWriter(scoped)
    # 回合结束后在后台投影技能运行事实，不占事件泵的帧处理
    skill_runs = SkillRunProjector(LoggedTurns(scoped, models.recorder, artifacts.service), skills.runs,
                                   writer.pending, artifact_observer.settle)
    broadcaster = Broadcaster()
    pump = EventPump(kernel, scoped, writer, broadcaster, on_host_restart=rotator.rotate_now,
                     observer=fan_out(usage, artifact_observer, citation_observer, skill_runs))
    feed = EventFeed(scoped, writer, broadcaster, pump.produced_upto)
    async def erase_citations(conversation: Conversation) -> None:
        await knowledge.citations.discard(conversation.workspace_id, conversation.id)

    async def erase_receipts(conversation: Conversation) -> None:
        await tools.discard_session(conversation.id)

    workflows = build_workflows(global_sessionmaker, tools, kernel, skills.delivery.catalog)
    profiles = build_profiles(global_sessionmaker, registry, clearance, skills=skills.delivery.catalog,
                              models=models.catalog, knowledge=knowledge.admin,
                              host_denied=() if sandbox else EXECUTION_TOOLS)
    # 「本会话内同类操作不再询问」的授权按对话当前绑定的运行配置版本记与查
    grants = SessionGrants(global_sessionmaker, profiles.bound_version)
    service = ConversationService(kernel, scoped, pump, feed, on_host_restart=rotator.rotate_now,
                                  refresh_model_credential=rotator.ensure_fresh, profiles=profiles,
                                  grants=grants, attachments=TurnAttachments(attachments.service),
                                  tool_credentials=tool_credentials, skill_credentials=skill_credentials,
                                  erasers=(erase_citations, erase_receipts,
                                           attachments.service.erase, artifacts.service.erase,
                                           *((sandbox.eraser(),) if sandbox else ())))
    oidc_http = httpx.AsyncClient(timeout=10.0) if config.oidc is not None else None
    upstream = httpx.AsyncClient(
        base_url=settings.model_upstream_base_url,
        headers=({"authorization": f"Bearer {upstream_key}"} if (upstream_key := settings.model_upstream_api_key
                                                                   .get_secret_value()) else {}),
        timeout=httpx.Timeout(30.0, read=300.0),
    )
    routed_http = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=300.0))
    host_http = httpx.AsyncClient(base_url=settings.host_base_url, timeout=3.0)
    identity = build_identity(global_sessionmaker, config, oidc_http=oidc_http,
                              on_user_created=access.join_default_workspace)
    debug = build_debugview(scoped, models.recorder)
    return Runtime(settings=settings, kernel=kernel, signer=signer, writer=writer, broadcaster=broadcaster,
                   pump=pump, service=service, rotator=rotator, tool_credentials=tool_credentials,
                   skill_credentials=skill_credentials, skills=skills, blobs=blobs, audit=audit, sandbox=sandbox, attachments=attachments,
                   artifacts=artifacts, previews=previews, artifact_observer=artifact_observer,
                   citation_observer=citation_observer, skill_runs=skill_runs, workflows=workflows,
                   grants=grants, tools=tools,
                   identity=identity,
                   access=access, models=models, usage=usage, knowledge=knowledge, mcp=mcp,
                   debug=debug,
                   runs=build_run_records(scoped, models.recorder, debug, service, access, audit.sink,
                                          identity.users.get_many),
                   global_sessionmaker=global_sessionmaker,
                   engine=engine, upstream=upstream, routed_http=routed_http, host_http=host_http,
                   oidc_http=oidc_http)


def create_public_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="KnowWork", version=runtime.settings.version)
    identity, access = runtime.identity, runtime.access
    app.include_router(identity_router(identity, is_admin=workspace_admin(access),
                                       can_bind_workspace=workspace_member(access)))
    app.include_router(access_router(access, scoped_principal(identity, WORKSPACE_SCOPE),
                                     profiles=identity.users.get_many))
    app.include_router(models_router(runtime.models.catalog, runtime.models.recorder, access,
                                     scoped_principal(identity, MODELS_SCOPE), runtime.routed_http,
                                     probe_timeout=runtime.settings.model_probe_timeout_seconds))
    app.include_router(workflows_router(runtime.workflows.service, access, bound_principal(identity)))
    app.include_router(skills_router(runtime.skills.service, access, bound_principal(identity)))
    app.include_router(skill_runs_router(runtime.skills.run_query, access, bound_principal(identity)))
    app.include_router(knowledge_router(runtime.knowledge.admin, scoped_principal(identity, KNOWLEDGE_SCOPE)))
    app.include_router(mcp_router(runtime.mcp.admin, scoped_principal(identity, TOOLS_SCOPE)))
    actor = conversation_actor(identity, access)
    app.include_router(conversation_router(runtime.service, actor))
    app.include_router(citations_router(runtime.knowledge.citations, runtime.service, actor))
    app.include_router(debug_router(runtime.debug, runtime.service, actor))
    app.include_router(build_admin_router(runtime.runs, scoped_principal(identity, WORKSPACE_SCOPE)))
    app.include_router(attachments_router(runtime.attachments.service, runtime.service, actor))
    app.include_router(artifacts_router(runtime.artifacts.service, runtime.service, actor,
                                        settle=runtime.artifact_observer.settle))
    app.include_router(documents_router(runtime.previews, runtime.service, actor))
    setup = build_setup_service(runtime.global_sessionmaker, access)
    app.include_router(setup_router(setup))
    brand = BrandService(runtime.global_sessionmaker, setup.is_system_admin)
    info = SystemInfoService(runtime.settings.version, runtime.global_sessionmaker, host_dsh_version(runtime.host_http))
    app.include_router(system_router(brand, info, bound_principal(identity)))
    app.include_router(audit_router(runtime.audit.query, access, scoped_principal(identity, AUDIT_SCOPE)))
    app.include_router(health_router((database_check(runtime.engine), host_check(runtime.host_http))))
    runtime.audit.install(app)  # 最后挂：记全部写接口
    return app


def create_internal_app(runtime: Runtime) -> FastAPI:
    app = FastAPI(title="KnowWork internal", docs_url=None, redoc_url=None, openapi_url=None)
    settings = runtime.settings
    # 没配固定上游密钥时不回退：模型全部来自模型目录
    fallback = settings.model_upstream_model if settings.model_upstream_api_key.get_secret_value() else ""
    limits = GatewayLimits(total_timeout_seconds=settings.model_call_timeout_seconds,
                           idle_timeout_seconds=settings.model_idle_timeout_seconds)
    app.include_router(gateway_router(runtime.signer, runtime.upstream, fallback, resolver=runtime.models.catalog,
                                      http=runtime.routed_http, limits=limits))
    app.include_router(tools_router(runtime.tools, grants=runtime.grants.covers))
    app.include_router(skills_internal_router(runtime.skills.delivery))
    if runtime.sandbox is not None:
        membership = ToolClearance(runtime.access, builtin=frozenset())
        app.include_router(sandbox_internal_router(runtime.sandbox.sandbox, runtime.tools, membership.member,
                                                   max_timeout_seconds=runtime.sandbox.max_timeout_seconds))
    return app
