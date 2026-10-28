"""技能提供方的内部路由，只挂在内部端口（宿主回调用）。

- `GET /internal/extensions/skills/catalog`：本会话可用技能清单（已发布版本）与每个技能的
  allowed-tools、completion-tools、unlocks；
- `GET /internal/extensions/skills/content?name=&digest=`：一个技能的 SKILL.md 正文。

都要 `Authorization: Bearer <受众 skills 的短期令牌>`；清单与内容都按此刻的授权重新编译。
错误统一为 `{"error": {"code", "message"}}`，message 不含内部细节。
"""

import logging

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse

from ..db.base import enter_workspace
from ..kernel.tokens import TokenError
from .catalog import SkillCatalog
from .errors import SkillError
from .tokens import SkillTokenClaims, SkillTokenSigner

log = logging.getLogger(__name__)

PREFIX = "/internal/extensions/skills"
NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
DIGEST_PATTERN = r"^[0-9a-f]{64}$"


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


def build_internal_router(delivery: SkillDelivery) -> APIRouter:
    router = APIRouter()

    @router.get(f"{PREFIX}/catalog")
    async def catalog(authorization: str | None = Header(default=None)):
        try:
            claims = delivery.verify(authorization)
            entries = await delivery.catalog.for_session(claims.workspace_id, claims.user_id)
        except _Unauthorized:
            return _error(401, "unauthorized", "令牌无效")
        except Exception:
            log.exception("skill catalog compile failed")
            return _error(500, "internal", "技能清单暂时不可用")
        return {"skills": [entry.to_host() for entry in entries]}

    @router.get(f"{PREFIX}/content")
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

    return router
