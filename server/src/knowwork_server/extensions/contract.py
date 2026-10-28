"""扩展合同：扩展模块以 Python 包的形式装进镜像，经入口组 knowwork.extensions 注册一个
Extension 对象。业务服务与 worker 启动时按授权加载；没有授权、校验失败或加载报错时跳过，开源功能照常。

扩展只拿这里的上下文，不拿业务服务内部的装配对象：内部重构不牵动扩展。上下文以后按需加字段。
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from fastapi import APIRouter
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from ..access.api import ProfileLookup
from ..access.service import AccessService
from ..attachments.mineru import MinerUProvider
from ..config import Settings
from ..jobs import JobRunner
from ..jobs.gate import Gate, LocalGate
from ..knowledge.links import LinkSigner
from ..knowledge.provided import ProvidedSources
from ..models.client import ModelClient
from ..sandbox.contract import SessionSandbox
from ..sessionfiles.storage import SessionStores

ENTRY_GROUP = "knowwork.extensions"
VERIFIER_GROUP = "knowwork.license"


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
    # 对外检索结果里的短期签名链接（图片等）：扩展的取文件接口用 links.verify 认签名，取时仍按签名里的用户判权
    links: LinkSigner | None = None
    # 按用户 id 批量取姓名与邮箱（同成员列表），给扩展的页面显示人名；查不到的 id 不出现在结果里
    profiles: ProfileLookup | None = None


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


class Extension(Protocol):
    name: str                         # 扩展名，日志与系统信息里显示
    license_module: str               # 授权里的模块名；授权里没有这个模块就不加载
    model_modules: tuple[str, ...]    # 建表与迁移要导入的模型模块
    rls_tables: tuple[str, ...]       # 要开行级安全的表（迁移里开，测试核对）
    migrations: Path | None           # Alembic 迁移目录，单独一条分支；装了包就升级，不看授权

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
