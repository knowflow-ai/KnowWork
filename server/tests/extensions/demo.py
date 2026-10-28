"""测试用的扩展与授权校验器：一个公开路由、一个内部端口路由、一种后台任务、一条自己的迁移分支。"""

from pathlib import Path

from fastapi import APIRouter

from knowwork_server.extensions import LicenseState
from knowwork_server.jobs import JobKind

PING_KIND = JobKind("demo.ping")


class Demo:
    name = "demo"
    license_module = "demo"
    model_modules: tuple[str, ...] = ()
    rls_tables = ("demo_note",)
    migrations = Path(__file__).with_name("demo_migrations")

    def __init__(self) -> None:
        self.contexts: list = []
        self.started = False
        self.closed = False

    def setup_api(self, ctx):
        self.contexts.append(ctx)
        router = APIRouter(prefix="/api/demo")

        @router.get("/ping")
        async def ping():
            return {"pong": True}

        return [router]

    def setup_internal(self, ctx):
        router = APIRouter(prefix="/internal/demo")

        @router.get("/ping")
        async def ping():
            return {"internal": True}

        return [router]

    def setup_worker(self, ctx):
        async def handle(job_ctx):
            return None

        ctx.runner.register(PING_KIND, handle)

    async def start(self) -> None:
        self.started = True

    async def aclose(self) -> None:
        self.closed = True


class Broken(Demo):
    name = "broken"

    def setup_api(self, ctx):
        raise RuntimeError("装配失败")


class BrokenInside(Demo):
    """公开路由装得上、内部路由装配报错：整个扩展去掉，公开路由也不留。"""

    name = "broken-inside"

    def setup_internal(self, ctx):
        raise RuntimeError("内部路由装配失败")


class PublicOnly:
    """老扩展没有 setup_internal：照常加载，只挂公开路由。"""

    name = "public-only"
    license_module = "demo"
    model_modules: tuple[str, ...] = ()
    rls_tables: tuple[str, ...] = ()
    migrations = None

    def setup_api(self, ctx):
        router = APIRouter(prefix="/api/public-only")

        @router.get("/ping")
        async def ping():
            return {"pong": True}

        return [router]

    def setup_worker(self, ctx):
        return None

    async def start(self) -> None:
        return None

    async def aclose(self) -> None:
        return None


EXTENSION = Demo()
BROKEN = Broken()
BROKEN_INSIDE = BrokenInside()
PUBLIC_ONLY = PublicOnly()
NOT_AN_EXTENSION = object()


class Granting:
    def __init__(self, *modules: str) -> None:
        self.modules = frozenset(modules)

    def verify(self) -> LicenseState:
        return LicenseState(status="valid", message="授权有效", modules=self.modules, customer="测试客户")


def granting_demo() -> Granting:
    return Granting("demo")


def granting_other() -> Granting:
    return Granting("other")


class Exploding:
    def verify(self) -> LicenseState:
        raise ValueError("签名对不上")


class Expired:
    def verify(self) -> LicenseState:
        return LicenseState(status="expired", message="授权已于 2026-01-01 过期")
