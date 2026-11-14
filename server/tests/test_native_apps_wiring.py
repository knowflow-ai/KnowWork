"""原生应用（桌面端等）用到的公开入口：只在扩展登记了原生应用时才挂，开源部署不多一个路由（可选服务零影响）。"""

import secrets

import httpx
import pytest

from knowwork_server.app import build_runtime, create_public_app
from knowwork_server.config import Settings
from knowwork_server.extensions.license import NO_LICENSE
from knowwork_server.extensions.loader import Extensions
from knowwork_server.identity.oauth_clients import OAuthClient

pytestmark = pytest.mark.anyio

PUBLIC_MODEL = "/api/model/v1/chat/completions"
PUBLIC_TOOLS = "/api/extensions/tools/list"
PUBLIC_SKILLS = "/api/extensions/skills/catalog"


class NativeAppExtension:
    name = "native-app"
    license_module = None
    model_modules: tuple[str, ...] = ()
    rls_tables: tuple[str, ...] = ()
    migrations = None
    context = None

    def setup_api(self, ctx):
        self.context = ctx
        ctx.oauth_clients.register(OAuthClient(client_id="kw-app", name="应用", redirect_path="/callback"))
        return []

    def setup_worker(self, ctx):
        return None

    async def start(self):
        return None

    async def aclose(self):
        return None


def settings() -> Settings:
    return Settings(database_url="postgresql+asyncpg://x@127.0.0.1:1/x", master_key=secrets.token_hex(32),
                    host_token="h" * 32)


async def entry_status(runtime, path: str) -> int:
    """不带令牌调一次公开入口：挂了回 401（令牌不对），没挂回 404。"""
    transport = httpx.ASGITransport(app=create_public_app(runtime))
    async with httpx.AsyncClient(transport=transport, base_url="http://kw") as client:
        if path == PUBLIC_MODEL:
            response = await client.post(path, json={"messages": [{"role": "user", "content": "hi"}]})
        else:
            response = await client.get(path)
    return response.status_code


async def test_without_native_apps_nothing_extra_is_mounted():
    runtime = build_runtime(settings())
    assert await entry_status(runtime, PUBLIC_MODEL) == 404
    assert await entry_status(runtime, PUBLIC_TOOLS) == 404
    assert await entry_status(runtime, PUBLIC_SKILLS) == 404
    assert runtime.identity.oauth_exchangers.get("model") is None


async def test_a_registered_native_app_gets_the_public_entries_and_the_model_audience():
    extension = NativeAppExtension()
    runtime = build_runtime(settings(), extensions=Extensions(license=NO_LICENSE, installed=(extension,),
                                                              active=(extension,)))
    assert await entry_status(runtime, PUBLIC_MODEL) == 401
    assert await entry_status(runtime, PUBLIC_TOOLS) == 401
    assert await entry_status(runtime, PUBLIC_SKILLS) == 401
    assert runtime.identity.oauth_exchangers.get("model") is not None


async def test_extensions_can_add_audiences_and_issue_callback_tokens_for_device_sessions():
    """本机会话回调业务服务的令牌由扩展换（它管本机会话的记录与归属）：开源给登记受众的地方和按受众签令牌的函数，
    工具与技能是同一套。"""
    extension = NativeAppExtension()
    runtime = build_runtime(settings(), extensions=Extensions(license=NO_LICENSE, installed=(extension,),
                                                              active=(extension,)))
    assert extension.context.oauth_exchangers is runtime.identity.oauth_exchangers
    assert set(extension.context.device_tokens) == {"tools", "skills"}
    issued = await extension.context.device_tokens["skills"]("ws1", "c_1", "u1")
    assert runtime.skills.delivery._signer.verify(issued.token).session_id == "c_1"
    assert issued.expires_in == runtime.skills.delivery.token_ttl_seconds
    assert extension.context.device_conversations is not None
