"""知识源的增删改查、连通测试与知识库列表（技术方案 5.2、5.5 节，第 8 章开头，8.4 节）。

权限（2026-10-20 起知识源与知识库分开）：
- 知识源是连接，资源 knowledge_source:<id> 只由空间角色的默认规则给（管理员默认 owner），不单独授权：
  新建需要 workspace:edit；查看连接配置、修改、连通测试、列知识库需要 edit；删除需要 owner；
- 谁能查授在知识库上（knowledge_base:<知识源id>.<知识库id>，或 <知识源id>.* 表示整个来源）。
  有这个来源下任一知识库授权的成员看得到这个知识源的摘要。
判定出错一律拒绝；返回结构按权限分 SourceSummary / SourceDetail 两套，都不含密钥明文。
"""

import asyncio
import logging
from dataclasses import dataclass

from pydantic import SecretStr

from .bases import RESOURCE as BASE_RESOURCE
from .bases import Grants, base_id
from .connectors import SECRET_OF, AccessChecker, ConnectorFactory, EmailLookup, parse_config
from .errors import AccessDenied, InvalidInput, KnowledgeError, NotFound, SourceUnavailable
from .knowflow_mapping import AuthMode
from .models import Caller
from .schemas import (
    BaseView,
    ProbeResult,
    SecretStatus,
    SourceBases,
    SourceCreate,
    SourceDetail,
    SourcePatch,
    SourceSummary,
)
from .store import SourceChanges, SourceRecord, SourceStore

log = logging.getLogger(__name__)

RESOURCE = "knowledge_source"
WORKSPACE = "workspace"
_MISSING_SECRET = {"assertion": "签名断言方式必须配置服务密钥", "shared": "统一账号方式必须配置 KnowFlow API Key"}
_NOT_SET = SecretStatus(configured=False)


def resource_key(source_id: str) -> str:
    return f"{RESOURCE}:{source_id}"


@dataclass(frozen=True)
class QueryableSource:
    """本人可查的一个知识源及其知识库范围（知识源内的 id，"*" 表示本人在知识源里读得到的全部）。"""

    id: str
    name: str
    kind: str
    enabled: bool
    bases: tuple[str, ...]


def _secret_for(mode: AuthMode, service_key: SecretStr | None, account_key: SecretStr | None) -> str | None:
    """这种认证方式用的连接密钥；填了别的方式的密钥直接拒绝，免得存进去一个用不上的密钥。"""
    if mode == "assertion" and account_key is None:
        return service_key.get_secret_value() if service_key else None
    if mode == "shared" and service_key is None:
        return account_key.get_secret_value() if account_key else None
    raise InvalidInput("填写的密钥与认证方式不符：统一账号填 KnowFlow API Key，签名断言填服务密钥")


class SourceAdmin:
    def __init__(self, store: SourceStore, access: AccessChecker, connectors: ConnectorFactory,
                 email_of: EmailLookup) -> None:
        self._store = store
        self._access = access
        self._connectors = connectors
        self._email_of = email_of

    async def create(self, actor: str, workspace_id: str, body: SourceCreate) -> SourceDetail:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise AccessDenied()
        mode = body.config.auth_mode
        secret = _secret_for(mode, body.service_key, body.account_key)
        if not secret:
            raise InvalidInput(_MISSING_SECRET[mode])
        record = await self._store.create(workspace_id, name=body.name, kind=body.kind, base_url=body.base_url,
                                          config=body.config.model_dump(mode="json"), secret=secret,
                                          secret_kind=SECRET_OF[mode], created_by=actor)
        return self._detail(record)

    async def update(self, actor: str, workspace_id: str, source_id: str, body: SourcePatch) -> SourceDetail:
        current = await self._get(actor, workspace_id, source_id, "edit")
        old_mode = parse_config(current).auth_mode
        mode = (body.config or parse_config(current)).auth_mode
        secret = _secret_for(mode, body.service_key, body.account_key)
        # 换了认证方式，旧密钥的含义就变了（服务密钥不是 API Key），要重新填
        if not secret and (mode != old_mode or not current.secret_ciphertext):
            raise InvalidInput(_MISSING_SECRET[mode])
        changes = SourceChanges(name=body.name, base_url=body.base_url, enabled=body.enabled, secret=secret,
                                secret_kind=SECRET_OF[mode],
                                config=body.config.model_dump(mode="json") if body.config else None)
        return self._detail(await self._store.update(workspace_id, source_id, changes))

    async def delete(self, actor: str, workspace_id: str, source_id: str) -> None:
        """连同指向它的授权一起删（同一事务），包括它下面的 knowledge_base:<id>.* 与单个知识库的授权。"""
        await self._get(actor, workspace_id, source_id, "owner")
        change = await self._store.delete(workspace_id, source_id, also=lambda session: self._access.purge_grants(
            session, workspace_id, resources=(resource_key(source_id),), prefixes=(f"{BASE_RESOURCE}:{source_id}.",)))
        await self._access.settle(change)

    async def get(self, actor: str, workspace_id: str, source_id: str) -> SourceSummary | SourceDetail:
        record = await self._get(actor, workspace_id, source_id, "query")
        if self._access.check(actor, workspace_id, resource_key(source_id), "edit"):
            return self._detail(record)
        return self._summary(record)

    async def list(self, actor: str, workspace_id: str) -> list[SourceSummary | SourceDetail]:
        grants = self._grants(actor, workspace_id)
        editable = self._access.visible_resources(actor, workspace_id, RESOURCE, "edit")
        records = [r for r in await self._store.list(workspace_id) if editable.allows(r.id) or grants.any_in(r.id)]
        return [self._detail(r) if editable.allows(r.id) else self._summary(r)
                for r in records]

    # 下面两个返回 tuple：类里已有名为 list 的方法，注解里再写 list[...] 会取到它
    async def queryable(self, actor: str, workspace_id: str) -> tuple[QueryableSource, ...]:
        """本人可查的知识源与知识库范围，只用授权与开放范围、不问知识源（给运行配置快照，技术方案 5.3 节）。"""
        grants = self._grants(actor, workspace_id)
        found = []
        for record in await self._store.list(workspace_id):
            if not grants.any_in(record.id):
                continue
            try:
                bases = grants.within(record.id, parse_config(record).dataset_ids)
            except SourceUnavailable:
                continue  # 配置坏了的知识源不进快照，检索时会明确报错
            if bases:
                found.append(QueryableSource(id=record.id, name=record.name, kind=record.kind,
                                             enabled=record.enabled, bases=bases))
        return tuple(found)

    async def catalog(self, actor: str, workspace_id: str) -> tuple[SourceBases, ...]:
        """可编辑的知识源下各有哪些知识库（给授权页与「管理 → 知识库」）。以操作者本人身份去列，
        统一账号方式用那个账号；只列开放范围内的，开放范围里列不出来的也列上、名字用 id。"""
        editable = self._access.visible_resources(actor, workspace_id, RESOURCE, "edit")
        records = [r for r in await self._store.list(workspace_id) if editable.allows(r.id)]
        caller = Caller(workspace_id=workspace_id, user_id=actor, email=await self._email_of(actor))
        return tuple(await asyncio.gather(*(self._bases_of(caller, r) for r in records)))

    async def probe(self, actor: str, workspace_id: str, source_id: str) -> ProbeResult:
        """以操作者本人的身份发一个最小请求，验证地址、密钥与账号映射都通；统一账号方式另核对开放范围都读得到。"""
        record = await self._get(actor, workspace_id, source_id, "edit")
        caller = Caller(workspace_id=workspace_id, user_id=actor, email=await self._email_of(actor))
        try:
            await self._connectors.build(record).probe(caller)
        except KnowledgeError as error:
            return ProbeResult(ok=False, message=str(error))
        return ProbeResult(ok=True, message="连接正常")

    def _grants(self, actor: str, workspace_id: str) -> Grants:
        return Grants(self._access.visible_resources(actor, workspace_id, BASE_RESOURCE, "use"))

    async def _get(self, actor: str, workspace_id: str, source_id: str, level: str) -> SourceRecord:
        """level：query（有这个来源下的知识库授权，或能改连接）、edit、owner。"""
        queryable = self._grants(actor, workspace_id).any_in(source_id)
        editable = self._access.check(actor, workspace_id, resource_key(source_id), "edit")
        allowed = (queryable or editable) if level == "query" else \
            self._access.check(actor, workspace_id, resource_key(source_id), level)
        if not allowed:
            # 没有权限与不存在同响应，不泄露其他知识源是否存在
            if not (queryable or editable):
                raise NotFound()
            raise AccessDenied()
        return await self._store.get(workspace_id, source_id)

    async def _bases_of(self, caller: Caller, record: SourceRecord) -> SourceBases:
        config = parse_config(record)
        try:
            listed = await self._connectors.build(record).list_bases(caller)
            error = None
        except KnowledgeError as failure:
            listed, error = (), str(failure)
        except Exception:
            log.exception("listing bases of knowledge source %s failed", record.id)
            listed, error = (), f"知识源「{record.name}」暂时不可用"
        names = {base.id: base.name for base in listed}
        ordered = config.dataset_ids or tuple(names)
        return SourceBases(source_id=record.id, source_name=record.name, auth_mode=config.auth_mode,
                           enabled=record.enabled, error=error,
                           bases=[BaseView(id=base_id(record.id, kb), kb_id=kb, name=names.get(kb, kb))
                                  for kb in ordered])

    @staticmethod
    def _summary(record: SourceRecord) -> SourceSummary:
        return SourceSummary(id=record.id, name=record.name, kind=record.kind, enabled=record.enabled,
                             auth_mode=parse_config(record).auth_mode)

    @classmethod
    def _detail(cls, record: SourceRecord) -> SourceDetail:
        summary = cls._summary(record)
        kind = SECRET_OF[summary.auth_mode]
        stored = SecretStatus(configured=bool(record.secret_ciphertext), last4=record.secret_last4)
        return SourceDetail(**summary.model_dump(), base_url=record.base_url, config=parse_config(record),
                            service_key=stored if kind == "service_key" else _NOT_SET,
                            account_key=stored if kind == "account_key" else _NOT_SET,
                            created_by=record.created_by)
