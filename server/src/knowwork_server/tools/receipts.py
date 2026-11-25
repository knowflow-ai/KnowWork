"""动作回执的读写：占位、结算、重放。依赖 PostgreSQL 的 INSERT ... ON CONFLICT DO NOTHING。"""

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import async_sessionmaker

from .models import ActionReceipt, ActionResultBlob

MAX_INLINE_BYTES = 64 * 1024
PREVIEW_BYTES = 16 * 1024


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class ActionCall:
    workspace_id: str
    session_id: str
    agent_session_id: str
    idempotency_key: str
    tool_name: str
    arguments_digest: str


@dataclass(frozen=True)
class ActionResult:
    action_id: str
    status: Literal["succeeded", "failed"]
    content: str
    truncated: bool
    result_ref: str | None
    replayed: bool
    presentation: dict[str, Any] | None = None  # 只给界面的卡片（tools/presentation.py），重放时一并交回


@dataclass(frozen=True)
class Claim:
    """占位结果。kind 为 new 时由调用方执行并结算；replay 直接返回 result；其余拒绝。"""

    kind: Literal["new", "replay", "in_progress", "conflict"]
    action_id: str | None = None
    result: ActionResult | None = None


def _capped(content: str, limit: int) -> tuple[str, bool]:
    """回给宿主的全文：超过上限截到上限（按字节，不切坏字），并标明截过。"""
    raw = content.encode()
    if len(raw) <= limit:
        return content, False
    return raw[:limit].decode(errors="ignore"), True


def _preview(content: str) -> str:
    return content.encode()[:PREVIEW_BYTES].decode(errors="ignore")


class ReceiptStore:
    """result_limit：回给宿主的结果最多多少字节。
    - 不给（宿主那边没有地方放大结果，例如没开会话沙箱）：同以前，超过 64KB 只回 16KB 预览；
    - 给了：回全文，超过上限才截。模型看多少、全文怎么放，由宿主的官方大输出溢出策略决定（dsh-spill-policy，完整内容写进
      会话工作区），业务服务先截掉的部分它就找不回来了。上限取会话沙箱单个文件的写入上限，全文写得进去。
    回执照旧：超过 64KB 存预览加全文引用（审计、调试视图用）。"""

    def __init__(self, sessionmaker: async_sessionmaker, *, result_limit: int | None = None) -> None:
        self._sessionmaker = sessionmaker
        self._result_limit = result_limit

    def _result_of(self, receipt: ActionReceipt, full: str | None, *, replayed: bool) -> ActionResult:
        """回执 → 回给宿主的结果。full 是全文（回执里只有预览时从全文存档取来），给了上限才用它。"""
        content, truncated = receipt.result_inline or "", receipt.truncated
        if self._result_limit is not None:
            content, truncated = _capped(full if full is not None else content, self._result_limit)
        return ActionResult(action_id=receipt.action_id, status=receipt.status, content=content, truncated=truncated,
                            result_ref=receipt.result_ref, replayed=replayed, presentation=receipt.presentation)

    async def discard_session(self, session_id: str) -> None:
        """删除会话时清掉它的回执与完整结果（都可能含业务内容）。"""
        async with self._sessionmaker() as session, session.begin():
            refs = select(ActionReceipt.result_ref).where(ActionReceipt.session_id == session_id,
                                                          ActionReceipt.result_ref.is_not(None))
            await session.execute(delete(ActionResultBlob).where(ActionResultBlob.id.in_(refs)))
            await session.execute(delete(ActionReceipt).where(ActionReceipt.session_id == session_id))

    async def claim(self, call: ActionCall) -> Claim:
        action_id = uuid.uuid4().hex
        statement = insert(ActionReceipt).values(
            id=uuid.uuid4().hex, action_id=action_id, idempotency_key=call.idempotency_key,
            workspace_id=call.workspace_id, session_id=call.session_id, agent_session_id=call.agent_session_id,
            tool_name=call.tool_name, arguments_digest=call.arguments_digest, status="running", truncated=False,
        ).on_conflict_do_nothing(index_elements=["idempotency_key"]).returning(ActionReceipt.action_id)
        async with self._sessionmaker() as session, session.begin():
            inserted = (await session.execute(statement)).scalar_one_or_none()
            if inserted is not None:
                return Claim(kind="new", action_id=inserted)
            existing = (await session.execute(
                select(ActionReceipt).where(ActionReceipt.idempotency_key == call.idempotency_key))).scalar_one()
            full = None
            if self._result_limit is not None and existing.result_ref is not None:
                blob = await session.get(ActionResultBlob, existing.result_ref)
                full = blob.content if blob is not None else None
        if existing.tool_name != call.tool_name or existing.arguments_digest != call.arguments_digest:
            return Claim(kind="conflict", action_id=existing.action_id)
        if existing.status == "running":
            return Claim(kind="in_progress", action_id=existing.action_id)
        return Claim(kind="replay", action_id=existing.action_id, result=self._result_of(existing, full, replayed=True))

    async def settle(self, call: ActionCall, action_id: str, status: Literal["succeeded", "failed"],
                     content: str, presentation: dict[str, Any] | None = None) -> ActionResult:
        truncated = len(content.encode()) > MAX_INLINE_BYTES
        inline = _preview(content) if truncated else content
        result_ref = uuid.uuid4().hex if truncated else None
        async with self._sessionmaker() as session, session.begin():
            if result_ref is not None:
                session.add(ActionResultBlob(id=result_ref, workspace_id=call.workspace_id, content=content))
            await session.execute(update(ActionReceipt).where(ActionReceipt.action_id == action_id).values(
                status=status, result_digest=digest(content), result_inline=inline, result_ref=result_ref,
                truncated=truncated, presentation=presentation))
        returned, cut = (inline, truncated) if self._result_limit is None else _capped(content, self._result_limit)
        return ActionResult(action_id=action_id, status=status, content=returned, truncated=cut,
                            result_ref=result_ref, replayed=False, presentation=presentation)
