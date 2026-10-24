"""文档解析服务（MinerU）的运行时配置：管理员在「模型 → 文档解析」里填，保存后几秒内生效，不用重启。

- 读配置（current）：这个空间有配置行就以它为准（停用即「没配」），没有就用部署时的环境变量；按空间缓存 5 秒，
  业务服务与 worker 每次解析前都来取，管理员改完不用重启；
- 令牌：MinerU 前面有网关要令牌时才填，加密存（同模型厂商的密钥），只回显末四位；
- 测试连接：取 MinerU 的 /openapi.json，核对有解析接口，分清「连不上」「令牌不对」「不是 MinerU」；
- VLM 推理服务地址（server_url，常见 30000 端口）是给 MinerU 用的：MinerU 的 vlm、hybrid 后端去访问它，KnowWork 不直连，
  所以测试连接不测它；没配时知识库要它的 hybrid 后端退回 pipeline；
- 解析模式（backend）：auto 不强制，按用途定（知识库 hybrid-http-client、附件 pipeline）；也可以强制 vlm-http-client、
  hybrid-http-client 或 pipeline，附件与知识库都按它走。hybrid 自动判断版面有时效果不好，整页交给 VLM 更稳。
  选了要 VLM 服务的模式就必须填地址，保存时拦住，不让它悄悄退回 pipeline；
- 会话附件与扩展共用这一份（档位、图片解析增强各自按用途定，不在这里配）。
"""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..attachments.mineru import HTTP_CLIENT_BACKENDS
from ..attachments.settings import AttachmentSettings
from ..models.crypto import SecretBox, SecretError, last4
from .models import DocumentParserRow

CACHE_SECONDS = 5.0
SECRET_SCOPE = "document-parser"  # 加密时的关联数据：解不开别处的密文
PROBE_TIMEOUT = 10.0
Lang = Literal["ch", "en", "japan", "korean", "chinese_cht", "latin", "arabic", "cyrillic", "devanagari"]
Backend = Literal["auto", "vlm-http-client", "hybrid-http-client", "pipeline"]  # attachments.mineru.CHOOSABLE_BACKENDS
AUTO = "auto"


@dataclass(frozen=True)
class ParserConfig:
    """解析时用的配置。source：settings 是界面里配的，env 是部署时的环境变量。"""

    base_url: str
    api_key: str | None
    lang: str
    max_concurrency: int
    source: str
    server_url: str | None = None  # VLM 推理服务；不配时要它的后端退回 pipeline
    backend: str | None = None     # 强制的解析模式；None 是自动（按调用方的默认）


@dataclass(frozen=True)
class ParserView:
    """管理页看到的：现在用的是哪份配置，以及界面里存的值（令牌只给末四位）。"""

    source: str               # settings | env | none
    enabled: bool
    base_url: str
    server_url: str
    backend: str
    api_key_set: bool
    api_key_last4: str | None
    lang: str
    max_concurrency: int
    updated_at: datetime | None


@dataclass(frozen=True)
class ProbeView:
    ok: bool
    message: str
    latency_ms: int


def _clean_url(value: str) -> str:
    value = value.strip().rstrip("/")
    if not value.startswith(("http://", "https://")) or " " in value:
        raise ValueError("地址要以 http:// 或 https:// 开头，例如 http://mineru:8000")
    return value


class ParserSave(BaseModel):
    """保存界面里的配置。api_key：不传（None）保持原样，空字符串清掉，其余是新令牌。"""

    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    base_url: Annotated[str, Field(max_length=500)]
    server_url: Annotated[str, Field(max_length=500)] = ""  # 空字符串是不用 VLM 服务
    backend: Backend = AUTO
    api_key: Annotated[str | None, Field(max_length=500)] = None
    lang: Lang = "ch"
    max_concurrency: Annotated[int, Field(ge=1, le=64)] = 3

    _url = field_validator("base_url")(_clean_url)

    @field_validator("server_url")
    @classmethod
    def _vlm(cls, value: str) -> str:
        return _clean_url(value) if value.strip() else ""

    @model_validator(mode="after")
    def _vlm_needed(self) -> "ParserSave":
        if self.enabled and self.backend in HTTP_CLIENT_BACKENDS and not self.server_url:
            raise ValueError("VLM、hybrid 模式要填 VLM 服务地址；没有 VLM 服务时选 pipeline 或自动")
        return self


class ParserTest(BaseModel):
    """测试连接：用表单里还没保存的值测；令牌不填时用已存的。"""

    model_config = ConfigDict(extra="forbid")
    base_url: Annotated[str, Field(max_length=500)]
    api_key: Annotated[str | None, Field(max_length=500)] = None

    _url = field_validator("base_url")(_clean_url)


class DocumentParsers:
    def __init__(self, sessionmaker: async_sessionmaker, master_key: str, env: AttachmentSettings | None = None, *,
                 http: httpx.AsyncClient | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self._sessionmaker = sessionmaker
        self._box = SecretBox.from_master_key(master_key)
        self._env = env or AttachmentSettings()
        self._http = http
        self._clock = clock
        self._cache: dict[str, tuple[float, ParserConfig | None]] = {}

    @asynccontextmanager
    async def _tx(self, workspace_id: str) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            yield session

    async def current(self, workspace_id: str) -> ParserConfig | None:
        """解析前取配置；None 是没配（或界面里停用了）。按空间缓存几秒，改完很快生效。"""
        cached = self._cache.get(workspace_id)
        now = self._clock()
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        async with self._tx(workspace_id) as session:
            row = await session.get(DocumentParserRow, workspace_id)
            config = self._config(row) if row is not None else self._from_env()
        self._cache = {**self._cache, workspace_id: (now, config)}
        return config

    def _config(self, row: DocumentParserRow) -> ParserConfig | None:
        if not row.enabled or not row.base_url:
            return None
        return ParserConfig(base_url=row.base_url, api_key=self._secret(row), lang=row.lang,
                            max_concurrency=row.max_concurrency, source="settings", server_url=row.server_url or None,
                            backend=None if row.backend == AUTO else row.backend)

    def _from_env(self) -> ParserConfig | None:
        env = self._env
        if not env.mineru_configured:
            return None
        return ParserConfig(base_url=env.mineru_base_url, api_key=None, lang=env.mineru_lang,
                            max_concurrency=env.mineru_max_concurrency, source="env",
                            server_url=env.mineru_server_url or None)

    def _secret(self, row: DocumentParserRow) -> str | None:
        if not row.api_key_ciphertext:
            return None
        try:
            return self._box.decrypt(row.api_key_ciphertext, workspace_id=row.workspace_id, provider_id=SECRET_SCOPE)
        except SecretError:  # 换了主密钥：当作没有令牌，测试连接时会提示令牌不对
            return None

    async def view(self, workspace_id: str) -> ParserView:
        async with self._tx(workspace_id) as session:
            row = await session.get(DocumentParserRow, workspace_id)
        if row is not None:
            return ParserView(source="settings", enabled=row.enabled, base_url=row.base_url,
                              server_url=row.server_url or "", backend=row.backend or AUTO,
                              api_key_set=bool(row.api_key_ciphertext), api_key_last4=row.api_key_last4,
                              lang=row.lang, max_concurrency=row.max_concurrency, updated_at=row.updated_at)
        env = self._env
        return ParserView(source="env" if env.mineru_configured else "none", enabled=env.mineru_configured,
                          base_url=env.mineru_base_url, server_url=env.mineru_server_url, backend=AUTO,
                          api_key_set=False, api_key_last4=None, lang=env.mineru_lang,
                          max_concurrency=env.mineru_max_concurrency, updated_at=None)

    async def save(self, actor: str, workspace_id: str, body: ParserSave) -> ParserView:
        async with self._tx(workspace_id) as session:
            row = await session.get(DocumentParserRow, workspace_id, with_for_update=True)
            if row is None:
                row = DocumentParserRow(workspace_id=workspace_id, kind="mineru")
                session.add(row)
            row.enabled, row.base_url, row.lang = body.enabled, body.base_url, body.lang
            row.server_url, row.backend = body.server_url or None, body.backend
            row.max_concurrency, row.updated_by = body.max_concurrency, actor
            if body.api_key is not None:
                key = body.api_key.strip()
                row.api_key_ciphertext = self._box.encrypt(key, workspace_id=workspace_id,
                                                           provider_id=SECRET_SCOPE) if key else None
                row.api_key_last4 = last4(key) if key else None
        self._cache = {k: v for k, v in self._cache.items() if k != workspace_id}
        return await self.view(workspace_id)

    async def probe(self, workspace_id: str, body: ParserTest) -> ProbeView:
        """取 /openapi.json：连得上、令牌对、有 MinerU 的解析接口才算通。"""
        api_key = body.api_key.strip() if body.api_key else await self._saved_key(workspace_id)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        started = self._clock()
        try:
            client = self._http or httpx.AsyncClient(timeout=PROBE_TIMEOUT)
            try:
                response = await client.get(f"{body.base_url}/openapi.json", headers=headers, timeout=PROBE_TIMEOUT)
            finally:
                if self._http is None:
                    await client.aclose()
        except httpx.HTTPError:
            return ProbeView(False, "连不上这个地址：检查地址、端口，以及业务服务和 worker 所在机器能否访问到它",
                             self._elapsed(started))
        elapsed = self._elapsed(started)
        if response.status_code in (401, 403):
            return ProbeView(False, "服务要求令牌，或令牌不对", elapsed)
        if response.status_code != 200:
            return ProbeView(False, f"服务返回了 HTTP {response.status_code}，看起来不是 MinerU 的接口地址", elapsed)
        try:
            paths = response.json().get("paths", {})
        except ValueError:
            paths = {}
        if not any(path in paths for path in ("/tasks", "/file_parse")):
            return ProbeView(False, "连上了，但没找到 MinerU 的解析接口（/tasks 或 /file_parse），确认是 MinerU 3.x 的 API 地址",
                             elapsed)
        return ProbeView(True, "连接正常", elapsed)

    async def _saved_key(self, workspace_id: str) -> str | None:
        async with self._tx(workspace_id) as session:
            row = await session.get(DocumentParserRow, workspace_id)
        return self._secret(row) if row is not None else None

    def _elapsed(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))
