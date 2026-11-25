"""内容安全的服务：策略读写（管理员）、给宿主插件的策略摘要与检查（宿主回调）、命中写审计。

- 词表只在业务服务：宿主每次把要查的文字送来，这里匹配、答复命中与否。词表里常有项目代号、客户名，本身就是机密，
  不下发到任何宿主，桌面端尤其不能；
- 查的词是勾上的几种词表合起来：自定义词表（custom）与扩展登记的来源（sources.py）。来源的词随扩展的包发、进程内不变；
- 编好的自动机按（空间，版本）缓存：每隔 CACHE_SECONDS 才回库看一眼版本号，变了才重新读词、重新编。本进程保存时
  马上作废；别的进程（多开的业务服务）最多晚 CACHE_SECONDS 看到新版本；
- 命中记审计（content_safety.blocked）：谁、哪段对话、哪个方向、命中了哪些词（最多 MAX_RECORDED_WORDS 个，另记总数）、
  策略第几版，**不记原文**。同一段对话里同一段文字只记一次（宿主重试、同一条消息在后面几步又送来时不重复记）。
"""

import asyncio
import hashlib
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from ..audit.events import AuditEvent
from ..audit.sink import AuditSink, safe_record
from ..db.base import in_workspace
from .matcher import Matcher
from .models import ContentSafetyPolicyRow
from .policy import MAX_RECORDED_WORDS, Policy, PolicyDraft, direction_enabled, holdback_of, split_words, unset_policy
from .sources import CUSTOM_SOURCE, WordSources

log = logging.getLogger(__name__)

WORKSPACE = "workspace"
CACHE_SECONDS = 2.0
MAX_REMEMBERED_HITS = 10_000  # 去重用的「已记过审计」的条数上限，满了丢最早的
BLOCKED_ACTION = "content_safety.blocked"


class AccessChecker(Protocol):
    def check(self, user_id: str, workspace_id: str, resource: str, action: str) -> bool: ...


class Forbidden(Exception):
    pass


class Conflict(Exception):
    """保存时版本对不上：别的管理员先改过。带着最新的一份，页面载入它、保留本页的修改。"""

    def __init__(self, latest: Policy) -> None:
        super().__init__("配置已被其他管理员修改")
        self.latest = latest


@dataclass(frozen=True)
class Caller:
    """宿主回调时，令牌认出来的这段会话归谁。模型网关来查时没有会话（session_id 为空），只知道是谁的专属宿主。"""

    workspace_id: str
    user_id: str
    session_id: str


@dataclass(frozen=True)
class CheckResult:
    blocked: bool
    message: str = ""
    words: tuple[str, ...] = ()


@dataclass(frozen=True)
class Summary:
    """给宿主插件的策略摘要：开没开、哪几个方向、流式输出要留几个字（合起来的词表里最长词长减一）、第几版。"""

    enabled: bool
    input: bool
    output: bool
    holdback: int
    version: int


@dataclass(frozen=True)
class _Compiled:
    policy: Policy
    matcher: Matcher | None  # 没开、或勾的词表里一个词都没有时为空
    holdback: int
    checked_at: float


def _policy_of(workspace_id: str, row: ContentSafetyPolicyRow | None) -> Policy:
    if row is None:
        return unset_policy(workspace_id)
    sources = tuple(str(item) for item in row.word_sources) if isinstance(row.word_sources, list) else (CUSTOM_SOURCE,)
    return Policy(workspace_id=workspace_id, version=row.version, input_enabled=row.input_enabled,
                  output_enabled=row.output_enabled, word_sources=sources, words=split_words(row.words),
                  block_message=row.block_message, updated_by=row.updated_by, updated_at=row.updated_at)


def _fingerprint(caller: Caller, direction: str, tool: str | None, segments: Sequence[str]) -> str:
    digest = hashlib.sha256()
    for part in (caller.workspace_id, caller.session_id, direction, tool or "", *segments):
        digest.update(part.encode())
        digest.update(b"\0")
    return digest.hexdigest()


class ContentSafetyService:
    def __init__(self, sessionmaker: async_sessionmaker, access: AccessChecker, audit: AuditSink | None, *,
                 sources: WordSources | None = None, cache_seconds: float = CACHE_SECONDS,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._sessions = sessionmaker
        self._access = access
        self._audit = audit
        self.sources = sources or WordSources()
        self._cache_seconds = cache_seconds
        self._clock = clock
        self._compiled: dict[str, _Compiled] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._recorded: OrderedDict[str, None] = OrderedDict()

    # ---- 管理员 ----

    def _require(self, actor: str, workspace_id: str) -> None:
        if not self._access.check(actor, workspace_id, WORKSPACE, "edit"):
            raise Forbidden()

    async def view(self, actor: str, workspace_id: str) -> Policy:
        self._require(actor, workspace_id)
        return await self._load(workspace_id)

    async def save(self, actor: str, workspace_id: str, *, version: int, draft: PolicyDraft) -> Policy:
        """version 是打开页面时的版本；对不上抛 Conflict。条件写入（按版本更新、没有才插入），两个管理员同时保存
        只有一个成功。"""
        self._require(actor, workspace_id)
        values = {"input_enabled": draft.input_enabled, "output_enabled": draft.output_enabled,
                  "word_sources": list(draft.word_sources), "words": "\n".join(draft.words),
                  "block_message": draft.block_message, "updated_by": actor}
        with in_workspace(workspace_id):
            async with self._sessions.begin() as session:
                if version == 0:
                    written = await session.execute(
                        insert(ContentSafetyPolicyRow).values(workspace_id=workspace_id, version=1, **values)
                        .on_conflict_do_nothing(index_elements=["workspace_id"]))
                else:
                    written = await session.execute(
                        update(ContentSafetyPolicyRow)
                        .where(ContentSafetyPolicyRow.workspace_id == workspace_id,
                               ContentSafetyPolicyRow.version == version)
                        .values(version=version + 1, **values))
                changed = written.rowcount == 1
        self._compiled.pop(workspace_id, None)
        latest = await self._load(workspace_id)
        if not changed:
            raise Conflict(latest)
        return latest

    async def _load(self, workspace_id: str) -> Policy:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                row = await session.get(ContentSafetyPolicyRow, workspace_id)
        return _policy_of(workspace_id, row)

    def source_words(self, actor: str, workspace_id: str, source_id: str) -> tuple[str, ...] | None:
        """管理员下载查看扩展登记的某份词表；没有这份返回 None。"""
        self._require(actor, workspace_id)
        source = self.sources.get(source_id)
        return None if source is None else source.words()

    def _words_of(self, policy: Policy) -> tuple[str, ...]:
        """勾上的几种词表合起来（去重）；扩展没装了的来源跳过。"""
        merged: dict[str, None] = {}
        for source_id in policy.word_sources:
            if source_id == CUSTOM_SOURCE:
                words = policy.words
            else:
                source = self.sources.get(source_id)
                words = source.words() if source is not None else ()
            merged.update(dict.fromkeys(words))
        return tuple(merged)

    # ---- 宿主回调 ----

    async def summary(self, workspace_id: str) -> Summary:
        compiled = await self._compile(workspace_id)
        policy, on = compiled.policy, compiled.matcher is not None
        return Summary(enabled=on, input=on and policy.input_enabled, output=on and policy.output_enabled,
                       holdback=compiled.holdback, version=policy.version)

    async def check(self, caller: Caller, *, direction: str, tool: str | None,
                    segments: Sequence[str]) -> CheckResult:
        compiled = await self._compile(caller.workspace_id)
        policy = compiled.policy
        if compiled.matcher is None or not direction_enabled(policy, direction):
            return CheckResult(blocked=False)
        # 词里不会有换行（换行是分隔符），用换行接起来不会凭空拼出跨段的词
        hits = compiled.matcher.find("\n".join(segments), limit=MAX_RECORDED_WORDS)
        if hits.total == 0:
            return CheckResult(blocked=False)
        await self._record(caller, policy, direction=direction, tool=tool, segments=segments,
                           words=hits.words, total=hits.total)
        return CheckResult(blocked=True, message=policy.block_message, words=hits.words)

    async def _compile(self, workspace_id: str) -> _Compiled:
        now = self._clock()
        cached = self._compiled.get(workspace_id)
        if cached is not None and now - cached.checked_at < self._cache_seconds:
            return cached
        lock = self._locks.setdefault(workspace_id, asyncio.Lock())
        async with lock:
            cached = self._compiled.get(workspace_id)
            if cached is not None and self._clock() - cached.checked_at < self._cache_seconds:
                return cached
            version = await self._version(workspace_id)
            if cached is not None and cached.policy.version == version:
                fresh = _Compiled(policy=cached.policy, matcher=cached.matcher, holdback=cached.holdback,
                                  checked_at=self._clock())
            else:
                policy = await self._load(workspace_id)
                words = self._words_of(policy) if policy.enabled else ()
                # 3 万个词编一次要几百毫秒，放到线程里，不卡住别的请求
                matcher = await asyncio.to_thread(Matcher, words) if words else None
                fresh = _Compiled(policy=policy, matcher=matcher, holdback=holdback_of(words) if words else 0,
                                  checked_at=self._clock())
            self._compiled[workspace_id] = fresh
            return fresh

    async def _version(self, workspace_id: str) -> int:
        with in_workspace(workspace_id):
            async with self._sessions() as session:
                found = await session.scalar(select(ContentSafetyPolicyRow.version)
                                             .where(ContentSafetyPolicyRow.workspace_id == workspace_id))
        return found or 0

    async def _record(self, caller: Caller, policy: Policy, *, direction: str, tool: str | None,
                      segments: Sequence[str], words: tuple[str, ...], total: int) -> None:
        key = _fingerprint(caller, direction, tool, segments)
        if key in self._recorded:
            return
        self._recorded[key] = None
        while len(self._recorded) > MAX_REMEMBERED_HITS:
            self._recorded.popitem(last=False)
        detail = {"direction": direction, "words": list(words), "word_count": total,
                  "policy_version": policy.version, **({"tool": tool} if tool else {}),
                  # 模型网关拦下的：专属宿主侧的检查没拦住（被插件绕开了，或查的是宿主侧不查的系统提示、工具定义）
                  **({} if caller.session_id else {"via": "model_gateway"})}
        session = caller.session_id or None
        await safe_record(self._audit, AuditEvent(
            workspace_id=caller.workspace_id, actor_type="human", actor_id=caller.user_id, action=BLOCKED_ACTION,
            result="denied", resource_type="conversation" if session else None, resource_id=session,
            session_id=session, detail=detail))
