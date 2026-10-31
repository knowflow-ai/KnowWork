"""应用装配：公开应用（对浏览器与 SDK）与内部应用（只给宿主回调）共用一个运行时容器。

部署时两个应用监听不同端口；内部端口只在容器内网，网关不转发。生产入口见 __main__.py。
"""

import logging
from dataclasses import dataclass
from functools import partial

import httpx
from fastapi import APIRouter, Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker
from starlette.routing import Route

from .access.api import build_router as access_router
from .access.members import PLATFORM_WORKSPACE_ID
from .access.service import AccessService
from .access.vocab import WORKSPACE_RESOURCE, Action
from .apidocs import install as install_api_docs
from .approvals import SessionGrants
from .artifacts import Artifacts, build_artifacts
from .artifacts.api import build_router as artifacts_router
from .artifacts.observer import ArtifactObserver
from .attachments import Attachments, build_attachments
from .attachments.api import build_router as attachments_router
from .attachments.turns import TurnAttachments
from .audit import AUDIT_SCOPE, AuditModule, build_audit
from .audit import build_router as audit_router
from .channels import Channels, build_channels
from .channels import build_router as channels_router
from .config import Settings
from .conversations.api import build_router as conversation_router
from .conversations.feedback import FeedbackStore
from .conversations.listeners import Broadcaster
from .conversations.mention_api import MentionSources
from .conversations.mention_api import build_router as mention_router
from .conversations.models import Conversation
from .conversations.observers import fan_out
from .conversations.pump import EventPump
from .conversations.replay import EventFeed
from .conversations.service import ConversationService
from .conversations.summaries import RunSummaries
from .conversations.tool_credentials import ToolCredentials, require_membership
from .conversations.turn_context import TurnContextHook
from .conversations.writer import BatchWriter
from .db.base import Base, make_engine, make_sessionmaker, make_workspace_sessionmaker
from .db.migrate import migrate_for_settings
from .db.registry import load_all_models
from .debugview import DebugService, RunRecords, build_admin_router, build_debugview, build_run_records
from .debugview import build_router as debug_router
from .docparse import DocumentParsers
from .docparse import build_router as docparse_router
from .documents import PreviewService
from .documents import build_router as documents_router
from .extensions import ApiContext, Extensions, installed_extensions
from .gateway.credential_rotation import CredentialRotator
from .gateway.model_gateway import MODEL_AUDIENCE, GatewayLimits
from .gateway.model_gateway import build_router as gateway_router
from .identity.api import build_router as identity_router
from .identity.config import IdentityConfig, OidcConfig
from .identity.container import Identity, build_identity
from .insights import InsightsService, build_insights
from .insights import build_router as insights_router
from .jobs.admin import JobsAdmin
from .jobs.api import build_router as jobs_router
from .kernel.contract import KnowledgeCatalog
from .kernel.host_client import HostKernel
from .kernel.tokens import TokenSigner, derive_key
from .knowledge import KnowledgeModule, Pinned, TurnScope, build_knowledge
from .knowledge.api import build_router as knowledge_router
from .knowledge.catalog import turn_catalog
from .knowledge.citation_api import build_router as citations_router
from .knowledge.citation_observer import CitationObserver
from .knowledge.cited import CitationNotifier
from .knowledge.links import LinkSigner
from .knowledge.mcp_server import PATH as MCP_PATH
from .knowledge.mcp_server import KnowledgeMcp
from .knowledge.mentionables import mentionable_bases, mentionable_documents
from .knowledge.reader import KnowledgeReader
from .knowledge.reader_api import build_router as reader_router
from .knowledge.service import KnowledgeService
from .models import ModelClient, ModelsService, build_models_service
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
    knowledge_reader,
    path_workspace,
    place_new_user,
    scoped_principal,
    workspace_admin,
    workspace_member,
)
from .profile import AuthorizedToolRegistry, ProfileBindings, ToolClearance, build_profiles
from .profile.snapshot import RuntimeProfile
from .sandbox import EXECUTION_TOOLS, SandboxModule, SandboxSettings, build_sandbox
from .sandbox.internal_api import build_internal_router as sandbox_internal_router
from .sandbox.unavailable import UnavailableSandbox
from .setup import build_setup_service
from .setup.api import build_router as setup_router
from .setup.service import current_system_admin
from .skills import Skills, build_skills
from .skills import build_layers_router as skill_layers_router
from .skills import build_runs_router as skill_runs_router
from .skills import build_submissions_router as skill_submissions_router
from .skills.api import build_router as skills_router
from .skills.blobstore import BlobStore
from .skills.claims import DEFAULT_BASIC_TOOLS
from .skills.internal_api import build_internal_router as skills_internal_router
from .skills.projector import LoggedTurns, SkillRunProjector
from .storage import blob_store, namespaced_stores, session_stores
from .system import BrandService, SystemInfoService
from .system import build_router as system_router
from .tools import build_tool_service
from .tools.api import build_internal_router as tools_router
from .tools.mcp import McpModule, build_mcp
from .tools.mcp.api import build_enterprise_here_router as mcp_here_router
from .tools.mcp.api import build_me_router as mcp_me_router
from .tools.mcp.api import build_oauth_router as mcp_oauth_router
from .tools.mcp.api import build_router as mcp_router
from .tools.provided import ProvidedTools
from .tools.registry import KW_NOW, CompositeToolRegistry, StaticToolRegistry, ToolContext
from .tools.service import ToolService
from .workflows import STALE_AFTER, Workflows, build_workflows
from .workflows.api import build_router as workflows_router

log = logging.getLogger(__name__)


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
    summaries: RunSummaries  # 每轮汇总（运行记录的列表读它）
    skill_runs: SkillRunProjector
    workflows: Workflows
    grants: SessionGrants
    tools: ToolService
    identity: Identity
    access: AccessService
    models: ModelsService
    usage: UsageObserver
    knowledge: KnowledgeModule
    knowledge_reader: KnowledgeReader  # 对外检索（REST 与 MCP 共用）
    mention_sources: MentionSources  # 输入框 @ 菜单的候选
    feedback: FeedbackStore  # 对每轮回答的赞、踩
    links: LinkSigner  # 站内链接签名：知识库图片在回答、引用里显示要带签名
    knowledge_mcp: KnowledgeMcp
    mcp: McpModule
    debug: DebugService
    runs: RunRecords
    insights: InsightsService  # 问答大盘（空间所有者、管理员）
    channels: Channels  # 渠道：企业微信等机器人
    jobs: JobsAdmin  # 运行面板
    parsers: DocumentParsers  # 文档解析服务的配置
    # 不声明工作空间：只能读写全局表（账号、工作空间等）；读行级安全表一行都看不到，要用声明空间的工厂
    global_sessionmaker: async_sessionmaker
    engine: AsyncEngine
    upstream: httpx.AsyncClient
    # 按模型目录转发到各厂商、做连通测试：不带 base_url 与默认鉴权头，免得把固定上游的密钥带给别的厂商
    routed_http: httpx.AsyncClient
    host_http: httpx.AsyncClient  # 就绪探针查宿主 /health 用
    oidc_http: httpx.AsyncClient | None
    # 按授权启用的扩展与它们的路由（装配失败的已从启用里去掉、记下原因）；内部端口的单独一份
    extensions: Extensions
    extension_routers: tuple[APIRouter, ...]
    extension_internal_routers: tuple[APIRouter, ...] = ()

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
        # 系统管理员能进任何空间：从初始化记录读回来交给权限模块（还没初始化时为空）
        self.access.set_system_admin(await current_system_admin(self.global_sessionmaker))
        for extension in self.extensions.active:
            await extension.start()
        await self.knowledge_mcp.start()
        self.writer.start()
        self.audit.start()
        if self.sandbox is not None:
            self.sandbox.start()
        await self.rotator.start()
        self.tool_credentials.start()
        self.skill_credentials.start()
        await self.channels.supervisor.start()  # 机器人长连接放最后：会话要用到的都已就绪

    async def stop(self) -> None:
        await self.channels.supervisor.stop()
        await self.knowledge_mcp.stop()
        await self.workflows.engine.aclose()
        await self.tool_credentials.stop()
        await self.skill_credentials.stop()
        await self.rotator.stop()
        await self.pump.close()
        await self.usage.drain()
        await self.artifact_observer.drain()
        await self.citation_observer.drain()
        await self.summaries.drain()
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
        for extension in self.extensions.active:
            try:
                await extension.aclose()
            except Exception:  # 一个扩展关不干净不影响别的资源释放
                log.exception("closing extension %s failed", extension.name)
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


async def _base_names(service: KnowledgeService, workspace_id: str, user_id: str) -> dict[str, str]:
    """问答大盘里知识库的名字：按查看者能查的列（全局 id → 名字）。"""
    listing = await service.bases(await service.caller(workspace_id, user_id))
    return {base.id: base.info.name or base.id for base in listing.bases}


def _owner_lookup(scoped: async_sessionmaker):
    async def owner_of(conversation_id: str) -> str | None:
        async with scoped() as session:
            conversation = await session.get(Conversation, conversation_id)
        return conversation.user_id if conversation is not None else None

    return owner_of


def _usage_observer(kernel: HostKernel, scoped: async_sessionmaker, models: ModelsService,
                    settings: Settings) -> UsageObserver:
    owner_of = _owner_lookup(scoped)

    async def cancel_turn(conversation_id: str) -> None:
        await kernel.cancel(conversation_id)

    return UsageObserver(models.recorder, owner_of, cancel_turn, default_model=settings.host_model_alias,
                         max_calls_per_turn=settings.model_max_calls_per_turn)


def build_runtime(settings: Settings, *, extensions: Extensions | None = None) -> Runtime:
    """extensions 不给时取本进程已安装、按授权启用的扩展（测试里传入假的）。"""
    config = identity_config(settings)  # 先校验配置，失败时还没建任何连接
    master_key = settings.master_key.get_secret_value()
    engine = make_engine(settings.database_url)
    global_sessionmaker = make_sessionmaker(engine)
    # 对话、工具回执、产物、附件等的每个事务都声明当前空间（请求路径、回调令牌、会话所在的空间），行级安全据此放行
    scoped = make_workspace_sessionmaker(engine)
    kernel = HostKernel(settings.host_base_url, settings.host_token.get_secret_value())
    signer = TokenSigner(derive_key(master_key, "host-callback"))

    def issue_model_token() -> str:
        # 宿主只有一份模型凭据，所有空间的会话共用；模型是平台级的，令牌就记平台配置所在的空间
        return signer.issue(audience=MODEL_AUDIENCE, workspace_id=PLATFORM_WORKSPACE_ID,
                            ttl_seconds=settings.model_token_ttl_seconds)

    rotator = CredentialRotator(kernel.rotate_model_credential, issue_model_token,
                                interval=max(settings.model_token_ttl_seconds // 3, 20))
    access = AccessService(global_sessionmaker)
    # 检索工具按对话本轮绑定的配置收窄：只查选了的知识库、去掉用户撤掉的条件、点名的库只查点名的文件与标签。
    # 会话配置用同一份绑定
    bindings = ProfileBindings(global_sessionmaker)

    async def turn_scope(ctx: ToolContext) -> TurnScope:
        profile = await bindings.for_tool(ctx)
        if profile is None:
            return TurnScope()
        return TurnScope(bases=profile.bound_knowledge, dropped=frozenset(profile.dropped_conditions),
                         pins=tuple(Pinned(base=p.base, documents=p.documents, tags=p.tags) for p in profile.pins))

    # 每轮汇总：回合开始、结束时各写一次；检索记录从它知道这一轮是第几轮，回合结束时把知识这一项事实交回给它
    summaries = RunSummaries(scoped, _owner_lookup(scoped))
    sandbox = build_sandbox(SandboxSettings())
    # 知识工具要会话沙箱放取来的图与文件（knowledge_fetch）；没开 Docker 沙箱时不提供这个工具
    knowledge = build_knowledge(master_key, global_sessionmaker, access, turn_scope=turn_scope,
                                current_turn=summaries.current_turn,
                                sandbox=sandbox.sandbox if sandbox is not None else None)
    summaries.contribute("knowledge", knowledge.search_log.close_turn)
    # 回答引用了扩展知识源的哪几份文档、是谁问的，告诉声明了 cited 的知识源（例如按人记常用资料）
    summaries.contribute("knowledge_cited", CitationNotifier(global_sessionmaker, knowledge.provided).turn_ended)
    # 内置工具（时间、知识检索、沙箱）只要求是空间成员；MCP 工具要 tool:<名字> 授权（默认只有 owner、admin）。
    # 执行时注册表按当前授权再判一次，权限收回立即生效。内置工具排在前面，外部工具不能顶替同名的内置工具。
    builtin_tools = (KW_NOW, *knowledge.tools)
    # 扩展提供的工具（例如记忆写入）在扩展装配时登记，排在内置工具之后、MCP 工具之前，同内置工具只要求是空间成员
    provided_tools = ProvidedTools()
    clearance = ToolClearance(access, builtin=frozenset(tool.spec.name for tool in builtin_tools),
                              provided=provided_tools)
    mcp = build_mcp(master_key, global_sessionmaker, access, public_base_url=settings.public_base_url,
                    permits=clearance.permits)
    access.on_member_removed(mcp.connections.forget_member)  # 人走了，他在这个空间的 MCP 连接一起删
    registry = AuthorizedToolRegistry(CompositeToolRegistry(StaticToolRegistry.of(builtin_tools), provided_tools,
                                                            mcp.registry), clearance)
    # 路径里没有空间的写操作（登录、建 Key、接受邀请）记在平台配置所在的空间
    audit = build_audit(global_sessionmaker, default_workspace_id=PLATFORM_WORKSPACE_ID)
    tools = build_tool_service(master_key, scoped, registry=registry, token_ttl_seconds=settings.tool_token_ttl_seconds,
                               audit=audit.tool_hook, notes=mcp.me.note)  # 告诉模型哪些服务用户还没连接
    blobs = blob_store(settings)
    skills = build_skills(master_key, global_sessionmaker, access, blobs,
                          basic_tools=DEFAULT_BASIC_TOOLS | {tool.spec.name for tool in knowledge.tools},
                          token_ttl_seconds=settings.tool_token_ttl_seconds, bundled=kernel.bundled_skills)
    access.on_member_removed(skills.service.forget_member)  # 人走了，他在这个空间的个人技能一起删
    tool_credentials, skill_credentials = _session_credentials(kernel, tools, skills, access, settings)
    models = build_models_service(global_sessionmaker, access, master_key, timezone=settings.usage_timezone)
    usage = _usage_observer(kernel, scoped, models, settings)
    stores = session_stores(settings)
    session_sandbox = sandbox.sandbox if sandbox else UnavailableSandbox()
    parsers = DocumentParsers(global_sessionmaker, master_key)  # 文档解析服务的运行时配置（模型 → 文档解析）
    attachments = build_attachments(scoped, stores, session_sandbox, parsers=parsers)
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
                     observer=fan_out(usage, artifact_observer, citation_observer, skill_runs,
                                      knowledge.turns, summaries))
    feed = EventFeed(scoped, writer, broadcaster, pump.produced_upto, live_snapshot=pump.live_snapshot)
    async def erase_knowledge(conversation: Conversation) -> None:
        await knowledge.citations.discard(conversation.workspace_id, conversation.id)
        await knowledge.search_log.discard(conversation.workspace_id, conversation.id)

    async def erase_receipts(conversation: Conversation) -> None:
        await tools.discard_session(conversation.id)

    workflows = build_workflows(global_sessionmaker, tools, kernel, skills.delivery.catalog)
    profiles = build_profiles(global_sessionmaker, registry, clearance, skills=skills.delivery.catalog,
                              models=models.catalog, knowledge=knowledge.admin,
                              host_denied=() if sandbox else EXECUTION_TOOLS, bindings=bindings)
    # 「本会话内同类操作不再询问」的授权按对话当前绑定的运行配置版本记与查
    # 「本会话内不再询问」按宿主相关版本记：工具、权限、技能变了才重新问，只是本轮 @ 变了不重新问
    grants = SessionGrants(global_sessionmaker, profiles.bound_host_version)
    async def mention_skills(workspace_id: str, user_id: str) -> list[dict]:
        found = await skills.available.for_user(workspace_id, user_id)  # 含宿主内置的技能
        return [{"name": s.name, "description": s.description} for s in found if s.user_invocable]

    mention_sources = MentionSources(bases=partial(mentionable_bases, knowledge.service), skills=mention_skills,
                                     documents=partial(mentionable_documents, knowledge.service),
                                     services=lambda workspace_id, user_id: mcp.me.mentionable(user_id, workspace_id))

    async def knowledge_catalog(conversation: Conversation, profile: RuntimeProfile) -> KnowledgeCatalog:
        if not profile.knowledge:
            return KnowledgeCatalog()
        caller = await knowledge.service.caller(conversation.workspace_id, conversation.user_id)
        return turn_catalog(await knowledge.service.bases(caller, bases=profile.bound_knowledge))

    # 扩展给每轮补的上下文（例如记忆）：扩展装配时登记提供者，没登记时这一轮什么都不带
    turn_context = TurnContextHook()
    service = ConversationService(kernel, scoped, pump, feed, on_host_restart=rotator.rotate_now,
                                  refresh_model_credential=rotator.ensure_fresh, profiles=profiles,
                                  grants=grants, attachments=TurnAttachments(attachments.service),
                                  knowledge_catalog=knowledge_catalog, turn_context=turn_context,
                                  tool_credentials=tool_credentials, skill_credentials=skill_credentials,
                                  erasers=(erase_knowledge, erase_receipts,
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
                              on_user_created=place_new_user(access))
    debug = build_debugview(scoped, models.recorder, knowledge.search_log)
    links = LinkSigner(derive_key(master_key, "knowledge-links"))
    knowledge_reader_ = KnowledgeReader(knowledge.service, links)
    knowledge_mcp = KnowledgeMcp(knowledge_reader_, knowledge_reader(identity, access))
    context = ApiContext(settings=settings, engine=engine, sessionmaker=scoped,
                         global_sessionmaker=global_sessionmaker, access=access,
                         models=ModelClient(models.catalog, routed_http),
                         stores_for=lambda namespace: namespaced_stores(settings, namespace),
                         knowledge_sources=knowledge.provided, tools=provided_tools, turn_context=turn_context,
                         links=links, profiles=identity.users.get_many, on_turn_ended=summaries.contribute,
                         platform_skills=skills.platform, enterprise_mcp=mcp.enterprise,
                         principal=lambda scope: scoped_principal(identity, scope) if scope else bound_principal(identity))
    extensions, extension_routers, extension_internal = _setup_extensions(extensions or installed_extensions(),
                                                                          context)
    return Runtime(settings=settings, kernel=kernel, signer=signer, writer=writer, broadcaster=broadcaster,
                   pump=pump, service=service, rotator=rotator, tool_credentials=tool_credentials,
                   skill_credentials=skill_credentials, skills=skills, blobs=blobs, audit=audit, sandbox=sandbox, attachments=attachments,
                   artifacts=artifacts, previews=previews, artifact_observer=artifact_observer,
                   citation_observer=citation_observer, summaries=summaries, skill_runs=skill_runs,
                   workflows=workflows,
                   grants=grants, tools=tools,
                   identity=identity,
                   access=access, models=models, usage=usage, knowledge=knowledge, mcp=mcp,
                   knowledge_reader=knowledge_reader_, links=links, knowledge_mcp=knowledge_mcp,
                   mention_sources=mention_sources, feedback=FeedbackStore(scoped),
                   debug=debug,
                   runs=build_run_records(scoped, models.recorder, debug, service, access, audit.sink,
                                          identity.users.get_many),
                   insights=build_insights(scoped, access, timezone=settings.usage_timezone,
                                           base_names=partial(_base_names, knowledge.service)),
                   channels=build_channels(master_key, scoped, global_sessionmaker, engine, access, service,
                                           accounts=identity.users, catalogs=knowledge.citations, sign=links.sign),
                   jobs=JobsAdmin(global_sessionmaker), parsers=parsers,
                   global_sessionmaker=global_sessionmaker,
                   engine=engine, upstream=upstream, routed_http=routed_http, host_http=host_http,
                   oidc_http=oidc_http, extensions=extensions, extension_routers=extension_routers,
                   extension_internal_routers=extension_internal)


Routers = tuple[APIRouter, ...]


def _setup_extensions(extensions: Extensions, context: ApiContext) -> tuple[Extensions, Routers, Routers]:
    """逐个装配启用的扩展：公开路由，以及可选的内部端口路由（setup_internal）。某个扩展装配报错只去掉它并记下
    原因（两种路由都不挂，不留半截），开源功能与别的扩展照常。"""
    public: list[APIRouter] = []
    internal: list[APIRouter] = []
    for extension in extensions.active:
        try:
            mine = tuple(extension.setup_api(context))
            setup_internal = getattr(extension, "setup_internal", None)
            hidden = tuple(setup_internal(context)) if setup_internal is not None else ()
        except Exception:
            log.exception("setting up extension %s failed", extension.name)
            extensions = extensions.without(extension, "扩展装配失败，详情见业务服务日志")
            continue
        public.extend(mine)
        internal.extend(hidden)
    return extensions, tuple(public), tuple(internal)


def create_public_app(runtime: Runtime) -> FastAPI:
    # 接口文档挂在 /api/docs、/api/openapi.json（apidocs），不用 FastAPI 默认的 /docs：网关只转发 /api
    app = FastAPI(title="KnowWork", version=runtime.settings.version, dependencies=[Depends(path_workspace)],
                  docs_url=None, redoc_url=None, openapi_url=None)
    identity, access = runtime.identity, runtime.access
    app.include_router(identity_router(identity, is_admin=workspace_admin(access),
                                       can_bind_workspace=workspace_member(access)))
    app.include_router(access_router(access, scoped_principal(identity, WORKSPACE_SCOPE),
                                     profiles=identity.users.get_many))
    app.include_router(models_router(runtime.models.catalog, runtime.models.recorder, access,
                                     scoped_principal(identity, MODELS_SCOPE), runtime.routed_http,
                                     probe_timeout=runtime.settings.model_probe_timeout_seconds))
    app.include_router(docparse_router(runtime.parsers, access, scoped_principal(identity, MODELS_SCOPE)))
    app.include_router(workflows_router(runtime.workflows.service, access, bound_principal(identity)))
    app.include_router(skills_router(runtime.skills, access, bound_principal(identity)))
    app.include_router(skill_submissions_router(runtime.skills.submissions, access, bound_principal(identity)))
    app.include_router(skill_layers_router(runtime.skills.layers, access, bound_principal(identity)))
    app.include_router(skill_runs_router(runtime.skills.run_query, access, bound_principal(identity)))
    app.include_router(knowledge_router(runtime.knowledge.admin, scoped_principal(identity, KNOWLEDGE_SCOPE)))
    app.include_router(reader_router(runtime.knowledge_reader, knowledge_reader(identity, access)))
    # MCP 是一个 ASGI 处理器（官方 SDK 的无状态流式 HTTP），外层自己判身份，挂成固定路径
    app.router.routes.append(Route(MCP_PATH, endpoint=runtime.knowledge_mcp, methods=["GET", "POST", "DELETE"]))
    app.include_router(mcp_router(runtime.mcp.admin, scoped_principal(identity, TOOLS_SCOPE)))
    app.include_router(mcp_here_router(runtime.mcp.here, scoped_principal(identity, TOOLS_SCOPE)))
    app.include_router(mcp_me_router(runtime.mcp.me, bound_principal(identity)))
    app.include_router(mcp_oauth_router(runtime.mcp.login, bound_principal(identity)))
    actor = conversation_actor(identity, access)
    app.include_router(mention_router(runtime.mention_sources, actor))  # 排在会话路由前，见 mention_api
    app.include_router(conversation_router(runtime.service, actor, runtime.feedback))
    app.include_router(citations_router(runtime.knowledge.citations, runtime.service, actor, runtime.links))
    app.include_router(debug_router(runtime.debug, runtime.service, actor))
    app.include_router(build_admin_router(runtime.runs, scoped_principal(identity, WORKSPACE_SCOPE)))
    app.include_router(insights_router(runtime.insights, scoped_principal(identity, WORKSPACE_SCOPE)))
    app.include_router(channels_router(runtime.channels.admin, scoped_principal(identity, WORKSPACE_SCOPE)))
    app.include_router(attachments_router(runtime.attachments.service, runtime.service, actor))
    app.include_router(artifacts_router(runtime.artifacts.service, runtime.service, actor,
                                        settle=runtime.artifact_observer.settle))
    app.include_router(documents_router(runtime.previews, runtime.service, actor))
    setup = build_setup_service(runtime.global_sessionmaker, access)
    app.include_router(setup_router(setup))
    brand = BrandService(runtime.global_sessionmaker, setup.is_system_admin)
    info = SystemInfoService(runtime.settings.version, runtime.global_sessionmaker, host_dsh_version(runtime.host_http),
                             runtime.extensions)
    app.include_router(system_router(brand, info, bound_principal(identity)))
    app.include_router(jobs_router(runtime.jobs, bound_principal(identity), setup.is_system_admin))
    app.include_router(audit_router(runtime.audit.query, access, scoped_principal(identity, AUDIT_SCOPE)))
    app.include_router(health_router((database_check(runtime.engine), host_check(runtime.host_http))))
    for router in runtime.extension_routers:
        app.include_router(router)
    install_api_docs(app)  # 路由都挂好以后：文档里要有扩展的接口
    runtime.audit.install(app)  # 最后挂：记全部写接口
    runtime.channels.bind_app(app)  # 机器人回答里的图在进程内经这些接口取
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
                                                   max_timeout_seconds=runtime.sandbox.max_timeout_seconds,
                                                   skill_env=runtime.skills.environment.for_exec,
                                                   ssh=runtime.sandbox.ssh))
    for router in runtime.extension_internal_routers:
        app.include_router(router)
    return app
