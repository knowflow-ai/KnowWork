"""文档解析服务（MinerU 4.1+）的运行时配置：管理员在「模型 → 文档解析」里填，保存后几秒内生效，不用重启。

- 全平台共用一份：存在平台配置所在的空间（PLATFORM_WORKSPACE_ID）名下，系统管理员在「系统设置」里改，所有空间都用它；
- 读配置（current）：有配置行就以它为准（停用即「没配」），没有就用部署时的环境变量；缓存 5 秒，
  业务服务与 worker 每次解析前都来取，管理员改完不用重启；
- 令牌：MinerU 前面有网关要令牌时才填，加密存（同模型厂商的密钥），只回显末四位；
- 测试连接：取 MinerU 的 /openapi.json，核对有解析接口，分清「连不上」「令牌不对」「不是 MinerU」；
- 解析档位（tier）：auto 不强制，让 MinerU 按文档自动推荐；也可以强制 flash / basic / standard / advanced，
  附件与知识库都按它走。VLM 推理服务地址（sglang / vllm）与识别语言都不再是这里的设置：4.1 起它们由
  MinerU 侧部署配置，语言自动识别；
- 会话附件与扩展共用这一份。
"""

import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.members import PLATFORM_WORKSPACE_ID
from ..access.rls import set_workspace
from ..attachments.settings import AttachmentSettings
from ..models.crypto import SecretBox, SecretError, last4
from .models import DocumentParserRow

CACHE_SECONDS = 5.0
SECRET_SCOPE = "document-parser"  # 加密时的关联数据：解不开别处的密文
PROBE_TIMEOUT = 10.0
Tier = Literal["auto", "flash", "basic", "standard", "advanced"]  # attachments.mineru.MinerUOptions.tier
AUTO = "auto"


@dataclass(frozen=True)
class ParserConfig:
    """解析时用的配置。source：settings 是界面里配的，env 是部署时的环境变量。"""

    base_url: str
    api_key: str | None
    max_concurrency: int
    source: str
    tier: str | None = None  # 强制的解析档位；None 是自动（让 MinerU 推荐）


@dataclass(frozen=True)
class ParserView:
    """管理页看到的：现在用的是哪份配置，以及界面里存的值（令牌只给末四位）。"""

    source: str               # settings | env | none
    enabled: bool
    base_url: str
    tier: str
    api_key_set: bool
    api_key_last4: str | None
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
    tier: Tier = AUTO
    api_key: Annotated[str | None, Field(max_length=500)] = None
    max_concurrency: Annotated[int, Field(ge=1, le=64)] = 3

    _url = field_validator("base_url")(_clean_url)


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
        self._cache: tuple[float, ParserConfig | None] | None = None

    @asynccontextmanager
    async def _tx(self) -> AsyncIterator[AsyncSession]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, PLATFORM_WORKSPACE_ID)
            yield session

    async def _row(self, session: AsyncSession, *, lock: bool = False) -> DocumentParserRow | None:
        return await session.get(DocumentParserRow, PLATFORM_WORKSPACE_ID, with_for_update=lock)

    async def current(self, workspace_id: str) -> ParserConfig | None:
        """解析前取配置；None 是没配（或界面里停用了）。缓存几秒，改完很快生效。
        workspace_id 是调用者所在的空间：现在所有空间用同一份平台配置，参数留着给以后按空间区分用。"""
        cached = self._cache
        now = self._clock()
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
        async with self._tx() as session:
            row = await self._row(session)
            config = self._config(row) if row is not None else self._from_env()
        self._cache = (now, config)
        return config

    def _config(self, row: DocumentParserRow) -> ParserConfig | None:
        if not row.enabled or not row.base_url:
            return None
        return ParserConfig(base_url=row.base_url, api_key=self._secret(row), max_concurrency=row.max_concurrency,
                            source="settings", tier=None if row.tier == AUTO else row.tier)

    def _from_env(self) -> ParserConfig | None:
        env = self._env
        if not env.mineru_configured:
            return None
        return ParserConfig(base_url=env.mineru_base_url, api_key=None, max_concurrency=env.mineru_max_concurrency,
                            source="env", tier=None if env.mineru_tier == AUTO else env.mineru_tier)

    def _secret(self, row: DocumentParserRow) -> str | None:
        if not row.api_key_ciphertext:
            return None
        try:
            return self._box.decrypt(row.api_key_ciphertext, workspace_id=row.workspace_id, provider_id=SECRET_SCOPE)
        except SecretError:  # 换了主密钥：当作没有令牌，测试连接时会提示令牌不对
            return None

    async def view(self) -> ParserView:
        async with self._tx() as session:
            row = await self._row(session)
        if row is not None:
            return ParserView(source="settings", enabled=row.enabled, base_url=row.base_url,
                              tier=row.tier or AUTO, api_key_set=bool(row.api_key_ciphertext),
                              api_key_last4=row.api_key_last4, max_concurrency=row.max_concurrency,
                              updated_at=row.updated_at)
        env = self._env
        return ParserView(source="env" if env.mineru_configured else "none", enabled=env.mineru_configured,
                          base_url=env.mineru_base_url, tier=env.mineru_tier or AUTO, api_key_set=False,
                          api_key_last4=None, max_concurrency=env.mineru_max_concurrency, updated_at=None)

    async def save(self, actor: str, body: ParserSave) -> ParserView:
        async with self._tx() as session:
            row = await self._row(session, lock=True)
            if row is None:
                row = DocumentParserRow(workspace_id=PLATFORM_WORKSPACE_ID, kind="mineru")
                session.add(row)
            row.enabled, row.base_url, row.tier = body.enabled, body.base_url, body.tier
            row.max_concurrency, row.updated_by = body.max_concurrency, actor
            if body.api_key is not None:
                key = body.api_key.strip()
                row.api_key_ciphertext = self._box.encrypt(key, workspace_id=row.workspace_id,
                                                           provider_id=SECRET_SCOPE) if key else None
                row.api_key_last4 = last4(key) if key else None
        self._cache = None
        return await self.view()

    async def probe(self, body: ParserTest) -> ProbeView:
        """取 /openapi.json：连得上、令牌对、有 MinerU 的解析接口才算通。"""
        api_key = body.api_key.strip() if body.api_key else await self._saved_key()
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
        if not any(path in paths for path in ("/v1/tasks", "/v1/file_parse")):
            return ProbeView(False, "连上了，但没找到 MinerU 的解析接口（/v1/tasks 或 /v1/file_parse），确认是 MinerU 4.1+ 的 API 地址",
                             elapsed)
        return ProbeView(True, "连接正常", elapsed)

    async def _saved_key(self) -> str | None:
        async with self._tx() as session:
            row = await self._row(session)
        return self._secret(row) if row is not None else None

    def _elapsed(self, started: float) -> int:
        return max(0, int((self._clock() - started) * 1000))
