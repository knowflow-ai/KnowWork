"""技能提供方的内部路由，只挂在内部端口（宿主回调用）。

- `GET /internal/extensions/skills/catalog`：本会话可用技能清单（已发布版本）与每个技能的
  allowed-tools、completion-tools、unlocks；bundledOff 是本空间停用了的内置技能，宿主不给这个会话注册它们；
- `GET /internal/extensions/skills/content?name=&digest=`：一个技能的 SKILL.md 正文。

都要 `Authorization: Bearer <受众 skills 的短期令牌>`；清单与内容都按此刻的授权重新编译。
错误统一为 `{"error": {"code", "message"}}`，message 不含内部细节。

登记了原生应用（桌面端）时，同一套接口在公开端口的 `/api/extensions/skills` 下再挂一份，给用户电脑上的本机宿主回调
（同工具的公开入口）：令牌是同一种，经令牌交换拿到（identity/oauth_exchange.py），没有令牌的请求一律 401。
这一份（挂的时候给了 device_environment）多两处，都是因为本机命令跑在用户电脑上、技能的环境变量要由本机宿主来取：
- `POST .../env`：本会话加载过的技能要带的环境变量，只给个人技能的（environment.for_device）；
- 清单里企业技能、空间技能声明了环境变量的，描述后面注明本机会话用不了（值不下发），模型据此请用户改到云端对话里用。
"""

import logging
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..db.base import enter_workspace
from ..kernel.tokens import TokenError
from .catalog import SkillCatalog
from .environment import PERSONAL, SkillEnvironment
from .errors import SkillError
from .tokens import SkillTokenClaims, SkillTokenSigner

log = logging.getLogger(__name__)

PREFIX = "/internal/extensions/skills"
PUBLIC_PREFIX = "/api/extensions/skills"
NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"
DEVICE_NOTE = "（这个技能要用管理员配置的密钥，本机会话里用不了，请到云端对话里用）"


class EnvRequest(BaseModel):
    skills: list[Annotated[str, Field(pattern=NAME_PATTERN, max_length=64)]] = Field(max_length=64)


def _device_entry(entry) -> dict[str, Any]:
    """本机会话的清单条目：企业、空间技能声明了环境变量的，描述后面注明用不了。"""
    host = entry.to_host()
    if not entry.env or entry.scope == PERSONAL:
        return host
    return {**host, "description": f"{host['description']}{DEVICE_NOTE}"}


class SkillDelivery:
    """把技能交给宿主：签发会话令牌、按令牌出清单与内容。"""

    def __init__(self, catalog: SkillCatalog, signer: SkillTokenSigner, *, token_ttl_seconds: int = 900) -> None:
        self._catalog = catalog
        self._signer = signer
        self._ttl = token_ttl_seconds

    def issue_session_token(self, workspace_id: str, session_id: str, user_id: str) -> str:
        """打开宿主会话（或续期）时签发，放进建会话请求的 `skills.token`。"""
        return self._signer.issue(workspace_id=workspace_id, session_id=session_id, user_id=user_id,
                                  ttl_seconds=self._ttl)

    @property
    def token_ttl_seconds(self) -> int:
        return self._ttl

    def issuer(self):
        """给 conversations.tool_credentials.ToolCredentials 用的签发函数：按会话的空间、id 与发起人签发。"""

        async def issue(conversation) -> str:
            return self.issue_session_token(conversation.workspace_id, conversation.id, conversation.user_id)

        return issue

    def verify(self, authorization: str | None) -> SkillTokenClaims:
        if authorization is None or not authorization.startswith("Bearer "):
            raise _Unauthorized()
        try:
            claims = self._signer.verify(authorization[7:])
        except TokenError as error:
            raise _Unauthorized() from error
        enter_workspace(claims.workspace_id)  # 这次回调以令牌里的空间读写
        return claims

    @property
    def catalog(self) -> SkillCatalog:
        return self._catalog


class _Unauthorized(Exception):
    pass


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def build_internal_router(delivery: SkillDelivery, *, path_prefix: str = PREFIX,
                          device_environment: SkillEnvironment | None = None) -> APIRouter:
    """device_environment 给了就是给本机宿主的那一份（见模块说明）。"""
    router = APIRouter(include_in_schema=False)
    to_host = (lambda entry: entry.to_host()) if device_environment is None else _device_entry

    @router.get(f"{path_prefix}/catalog")
    async def catalog(authorization: str | None = Header(default=None)):
        try:
            claims = delivery.verify(authorization)
            entries = await delivery.catalog.for_session(claims.workspace_id, claims.user_id)
            bundled_off = await delivery.catalog.bundled_off(claims.workspace_id, claims.user_id)
        except _Unauthorized:
            return _error(401, "unauthorized", "令牌无效")
        except Exception:
            log.exception("skill catalog compile failed")
            return _error(500, "internal", "技能清单暂时不可用")
        return {"skills": [to_host(entry) for entry in entries], "bundledOff": list(bundled_off)}

    @router.get(f"{path_prefix}/content")
    async def content(name: str = Query(pattern=NAME_PATTERN, max_length=64),
                      digest: str = Query(pattern=DIGEST_PATTERN),
                      authorization: str | None = Header(default=None)):
        try:
            claims = delivery.verify(authorization)
            loaded = await delivery.catalog.load(claims.workspace_id, claims.user_id, name, digest)
        except _Unauthorized:
            return _error(401, "unauthorized", "令牌无效")
        except SkillError as error:
            return _error(error.status, error.code, error.message)
        return loaded.to_json()

    if device_environment is not None:
        @router.post(f"{path_prefix}/env")
        async def env(body: EnvRequest, authorization: str | None = Header(default=None)):
            try:
                claims = delivery.verify(authorization)
                values = await device_environment.for_device(claims.workspace_id, claims.user_id, body.skills)
            except _Unauthorized:
                return _error(401, "unauthorized", "令牌无效")
            except Exception:
                log.exception("skill env for device failed")
                return _error(500, "internal", "技能的环境变量暂时取不到")
            return {"env": values}

    return router
