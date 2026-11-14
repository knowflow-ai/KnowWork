"""扩展合同：扩展模块以 Python 包的形式装进镜像，经入口组 knowwork.extensions 注册一个
Extension 对象。业务服务与 worker 启动时按授权加载；没有授权、校验失败或加载报错时跳过，开源功能照常。

扩展只拿这里的上下文，不拿业务服务内部的装配对象：内部重构不牵动扩展。上下文以后按需加字段。
"""

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..access.api import ProfileLookup
from ..access.service import AccessService
from ..attachments.mineru import MinerUProvider
from ..channels.targets import ChannelTargets
from ..config import Settings
from ..conversations.answer_modes import AnswerModes
from ..conversations.background import BackgroundTurns, LocalBackground
from ..conversations.device import DeviceConversations
from ..conversations.mention_kinds import MentionKinds
from ..conversations.quota import QuotaPolicies
from ..conversations.scopes import ConversationScopes
from ..conversations.summaries import Contributor
from ..conversations.turn_context import TurnContextHook
from ..identity.directory_accounts import DirectoryAccounts
from ..identity.im_identities import ImIdentities
from ..identity.methods import LoginMethods
from ..identity.oauth import DeviceLogin
from ..identity.oauth_clients import OAuthClients
from ..identity.oauth_exchange import Exchanged, TokenExchangers
from ..identity.password_logins import PasswordLogins
from ..identity.sso import RedirectLogins
from ..identity.verify_files import VerifyFiles
from ..jobs import JobRunner
from ..jobs.gate import Gate, LocalGate
from ..knowledge.links import LinkSigner
from ..knowledge.provided import ProvidedSources
from ..models.client import ModelClient
from ..models.recorder import UsageReader
from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores
from ..skills.platform import PlatformSkillAdmin
from ..tools.mcp.enterprise import EnterpriseMcpAdmin
from ..tools.provided import ProvidedTools
from ..tools.results import ResultSources

ENTRY_GROUP = "knowwork.extensions"
VERIFIER_GROUP = "knowwork.license"


async def no_embed(principal: Any, request: Any, conversation_id: str) -> None:
    """没接上网页渠道时的 embed_guard：嵌入身份一律不给用。"""
    if getattr(principal, "is_embedded", False):
        raise HTTPException(status_code=403, detail="网页嵌入里用不了这个功能")


# 给不在服务端跑的会话签一种回调令牌：(空间, 会话, 用户) → 短期令牌与有效期
DeviceTokenIssuer = Callable[[str, str, str], Awaitable[Exchanged]]

@dataclass(frozen=True)
class ApiContext:
    """业务服务进程给扩展的东西。"""

    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker          # 每个事务都声明当前空间（db.base.current_workspace）
    global_sessionmaker: async_sessionmaker   # 不声明工作空间，由调用方 set_workspace
    access: AccessService
    # 管理路由的鉴权依赖：principal(范围) 要求 API Key 带这个范围；principal(None) 不查范围，只拦跨空间的 Key
    principal: Callable[[str | None], Any]
    models: ModelClient  # 嵌入、重排、看图；检索时的重排用交互额度（background=False）
    # 按命名空间取对象存储（按摘要寻址，再按（工作空间, 二级标识）分开），例如 stores_for("library")
    stores_for: Callable[[str], SessionStores]
    # 登记扩展提供的知识源（knowledge_search、knowledge_read 与运行配置都会算上它）
    knowledge_sources: ProvidedSources
    # 登记扩展提供的工具（tools/provided.py）：同内置工具只要求是空间成员，给不给哪个会话由扩展判断
    tools: ProvidedTools = field(default_factory=ProvidedTools)
    # 登记每轮补上下文的提供者（conversations/turn_context.py，turn_context.add），例如记忆、问数的示例；按登记顺序拼
    turn_context: TurnContextHook = field(default_factory=TurnContextHook)
    # 登记回答方式（conversations/answer_modes.py，answer_modes.add）：输入框下拉里多一项，选了它这一轮按它收窄官方工具、
    # 注入指令；业务工具在不在这种方式里由工具自己的 answer_modes 声明
    answer_modes: AnswerModes = field(default_factory=AnswerModes)
    # 登记结果来源（tools/results.py，results.add）：查询类工具查出来的结果按编号交给 show_result 展示，来源自己的回答方式里
    # 也给 show_result（只收编号）
    results: ResultSources = field(default_factory=ResultSources)
    # 登记 @ 类型（conversations/mention_kinds.py，mentions.add）：输入框 @ 菜单里多一组（例如数据源），发消息时核对权限、
    # 这一轮开始前交给扩展
    mentions: MentionKinds = field(default_factory=MentionKinds)
    # 登记对话的范围（conversations/scopes.py，conversation_scopes.add）：给某段对话定顶部的名字、固定的回答方式，
    # 知识库、技能、MCP 收窄到哪些（和本人的权限取交集）；每一轮的说明经 turn_context 给。
    # 建对话时能挂的东西也登记在这里（conversation_scopes.add_claimer，例如专家）：不管在哪执行、从哪建都经它挂上
    conversation_scopes: ConversationScopes = field(default_factory=ConversationScopes)
    # 对外检索结果里的短期签名链接（图片等）：扩展的取文件接口用 links.verify 认签名，取时仍按签名里的用户判权
    links: LinkSigner | None = None
    # 按用户 id 批量取姓名、邮箱与账号类型（同成员列表），给扩展的页面显示人名、分真人与渠道账号；查不到的 id 不出现在结果里
    profiles: ProfileLookup | None = None
    # 登记回合结束时要做的事（conversations/summaries.py 的 contribute(名字, 函数)）：拿到这一轮的提问、回答、状态与提问人，
    # 例如记忆排后台提取；返回的字典写进运行记录的 facts[名字]，返回 None 就不写。没给时登记了也不会被调用
    on_turn_ended: Callable[[str, Contributor], None] = lambda name, contributor: None
    # 企业技能的管理（系统管理员）：上传、换版本、发布、启停、删除、发给哪些空间、从空间技能收为企业技能。
    # 存储与会话里的三段清单在开源，管理的接口与页面由扩展提供；没给时扩展不挂企业技能的管理视图
    platform_skills: PlatformSkillAdmin | None = None
    # 企业 MCP 的管理（系统管理员）：登记、改、同步、工具三档、发给哪些空间、删除。会话里的工具、空间管理员的停用与收窄、
    # 成员的连接都在开源，管理的接口与页面由扩展提供（同企业技能）
    enterprise_mcp: EnterpriseMcpAdmin | None = None
    # 目录同步的账号部分：按人员快照建号、对上已有账号、停用离职的、恢复重新在职的（组织同步用）
    accounts: DirectoryAccounts | None = None
    # 登录页的入口列表（登记一个来源，列表时现取）与账号密码类、跳转类登录的提供方（域账号、企业微信），
    # 扩展的登录方式登记到这里；平台验证域名用的根目录校验文件也由扩展按文件名给内容
    login_methods: LoginMethods | None = None
    password_logins: PasswordLogins | None = None
    redirect_logins: RedirectLogins | None = None
    verify_files: VerifyFiles | None = None
    # 原生应用登录（OAuth 授权码 + PKCE，identity/oauth.py）的客户端登记表：扩展登记自己的应用（例如桌面端），
    # 没有任何登记时授权与换令牌一律拒绝
    oauth_clients: OAuthClients | None = None
    # 原生应用的令牌交换（RFC 8693，identity/oauth_exchange.py）：扩展登记自己的受众，例如本机会话的工具令牌
    oauth_exchangers: TokenExchangers | None = None
    # 给不在服务端跑的会话（桌面端的本机会话）签回调业务服务的令牌：受众（tools 工具、skills 技能）→
    # (空间, 会话, 用户) → 短期令牌与有效期。和服务端会话同一种令牌、同一套计算（工具：权限、MCP、扩展的工具，只去掉要用服务端
    # 沙箱的；技能：此刻的会话技能清单），会话归谁、能不能换由扩展先判。本机宿主拿它从公开入口 /api/extensions/tools、
    # /api/extensions/skills 回调（登记了原生应用才挂）。以后多一种回调，在这里多一个受众，扩展不用改
    device_tokens: Mapping[str, DeviceTokenIssuer] | None = None
    # 在用户设备上执行的对话（conversations/device.py）：建对话不开宿主会话、收设备送来的会话事件、查某人名下的这种对话。
    # 谁能建、谁能送、设备是谁由扩展判断（例如只认桌面端登录）
    device_conversations: DeviceConversations | None = None
    # 原生应用那次登录报的设备号（identity/oauth.py 的 device_id）：登录编号（Principal.grant_id）→ 设备号，没报或已退出是 None
    oauth_device_id: Callable[[str], Awaitable[str | None]] | None = None
    # 这个人此刻登录着的原生应用设备（identity/oauth.py 的 list_devices：设备号、设备名、最近用的在前）：扩展据此说「在哪台设备上」
    oauth_devices: Callable[[str], Awaitable[Sequence[DeviceLogin]]] | None = None
    # IM 机器人认人用的身份来源：扩展把组织来源登记进来（id、平台、身份域、免登入口），渠道选一个来认人
    im_identities: ImIdentities = field(default_factory=ImIdentities)
    # 替用户建对话（conversations/background.py 的 LocalBackground，同 WorkerContext.turns 的判定，不走 HTTP）：
    # 可指定对话 id，扩展先拿它记下自己的东西再建
    background: LocalBackground | None = None
    # 扩展里网页嵌入也能用的接口（路由用 principal("conversations:use") 让嵌入身份进来），碰某段对话前调
    # embed_guard(调用者, 请求, 会话 id)：不是嵌入身份的直接过；嵌入身份要是自己从这个渠道开的会话，再按访客限次，
    # 不满足时抛 HTTPException（404、429、403），实现见 principals.EmbedGuard
    embed_guard: Callable[[Any, Any, str], Awaitable[None]] = no_embed
    # 网页渠道能挂的对象（channels/targets.py）：扩展登记一类（例如专家），渠道挂上就照它答；对象改了、删了调 changed
    channel_targets: ChannelTargets = field(default_factory=ChannelTargets)
    # 登记用量配额的判定（conversations/quota.py，quota.set）：各端开始一轮、定时任务到点激活之前调，超了抛 QuotaExceeded。
    # 只能登记一个；没登记时不限制
    quota: QuotaPolicies = field(default_factory=QuotaPolicies)
    # 只读的用量汇总（models/recorder.py 的 summary）：按人、按模型、按天，「哪一天」按 settings.usage_timezone 切
    usage: UsageReader | None = None


@dataclass(frozen=True)
class WorkerContext:
    """worker 进程给扩展的东西：注册后台任务用 runner。"""

    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker
    runner: JobRunner
    stores: SessionStores
    sandbox: SessionSandbox
    models: ModelClient  # 默认用后台额度
    stores_for: Callable[[str], SessionStores]
    # 下游并发闸门（jobs.gate）：MinerU 用名额池 mineru（GatedMinerU），扩展的其他下游可以自己起名
    gate: Gate = field(default_factory=LocalGate)
    # 文档解析服务（MinerU）：按空间取管理后台里当前的配置，已经过闸门；没给时扩展自己看环境变量
    mineru: MinerUProvider | None = None
    # 权限与企业组织的写入（组织同步写部门、成员规则）。worker 不判定：只写库，提交时通知业务服务重新加载
    access: AccessService | None = None
    # 目录同步的账号部分（同 ApiContext.accounts）
    accounts: DirectoryAccounts | None = None
    # 替用户发起后台对话（conversations/background.py）：建会话、按回答方式发一轮、看状态、取消；会话不进本人的「最近」
    turns: BackgroundTurns | None = None


class Extension(Protocol):
    name: str                         # 扩展名，日志与系统信息里显示
    # 授权里的模块名；授权里没有这个模块就不加载。None：闭源但不收费，装了就启用，不看授权（也不随授权到期停）
    license_module: str | None
    model_modules: tuple[str, ...]    # 建表与迁移要导入的模型模块
    rls_tables: tuple[str, ...]       # 要开行级安全的表（迁移里开，测试核对）
    migrations: Path | None           # Alembic 迁移目录，单独一条分支；装了包就升级，不看授权
    # 可选：requires: tuple[str, ...]  依赖的别的扩展名（例如问数要知识库）；依赖的没启用，它也不启用

    def setup_api(self, ctx: ApiContext) -> Sequence[APIRouter]:
        """业务服务启动时调用，返回要挂的路由。"""
        ...

    # 可选：def setup_internal(self, ctx: ApiContext) -> Sequence[APIRouter]
    # 返回挂在内部端口的路由（只给宿主、扩展自己部署的服务回调，网关不转发）。没有这个方法就不挂；
    # 和 setup_api 一起装配，任何一个报错整个扩展都去掉。

    def setup_worker(self, ctx: WorkerContext) -> None:
        """worker 启动时调用，在 ctx.runner 上注册任务类型。"""
        ...

    async def start(self) -> None:
        """业务服务开始服务之前调用（迁移已跑完、权限规则已加载），例如把要在内存里判定的数据加载好。
        报错时业务服务拒绝启动：扩展的权限判定没准备好就开门，会误判。"""
        ...

    async def aclose(self) -> None: ...
