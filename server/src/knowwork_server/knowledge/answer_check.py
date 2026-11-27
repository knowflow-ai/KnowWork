"""回合结束后判断「知识库答上了没有」（通用任务框架上的 knowledge.answer_check）。

原来让作答的智能体中途调 knowledge_gap 自己报缺口，可那只是它当时的判断：第一次没查到就报了、接着换个查法找到了，
答上的问题也被算成没答上。答上没有是对最终回答的判断，行业里的做法是答完以后另外用模型
评判（事后、批量、异步），这里照着做：

- 入队（queue_check，在 close_turn 的事务里）：检索过知识库、正常答完的一轮记一条待判断，排一条任务，主体是空间、
  延后一小时。同一空间排队中的同类任务合并、执行时间取早的，所以每个空间一小时一批：一轮一判太费模型调用。
- 执行：取这个空间最早的 PER_RUN 轮，每次调用判 PER_CALL 轮。给模型看问题、最终回答，和这一轮检索到的知识库片段
  （判的是「知识库里的内容答上了没有」，智能体拿自己的常识答的不算）。回答标了编号的片段给到 CITED_CHARS 字、带上
  图的描述，按名次补进来的只给 SNIPPET_CHARS 字：原来一律截 400 字，回答出自片段后半段的内容判断看不到，答对了的
  被判成只答了一部分（2026-10-29：《招惹》的主演、播放量在片段第 400 字之后）。
- 分级加预设原因，不打分（不用定阈值）：answered 答上了、partial 只答了一部分、unanswered 没答上；
  没答全时写一句缺什么，原因五选一（REASONS：不该知识库答、片段里没有、含糊矛盾或过时、没用上、模型自己的知识）。
  不看回答引用了几条：智能体答不上来时常常还会引用沾边的背景，引用条数说明不了答没答上。
- 还给判官看前几轮问了什么（追问只看这一句判不了）和用户紧接着发的下一条（纠正、换说法都在里面），判出用户的反应
  （follow：纠正了摘原话、换说法记新说法）和相关片段出自哪个库（about）。这几样是扩展据此给配置建议（同义词、
  版本过期……）的信号。刚答完、下一条还没来的等 wait_seconds 再判，等着的稍后再排一次。
- 结论并进每轮汇总的知识事实（VERDICT_KEYS），判完删掉待判断那一行。用默认模型。
- 一批的返回看不懂、或者缺了某几轮，这几轮留着下次再判，判 MAX_CHECK_TRIES 次都没结论就放弃（这一轮不算没答上）；
  模型调不通按服务问题重试（不算这一轮的次数）。
- 管理端可以「立即检查」（check_now）：排队中的任务提到现在，不等整点。
- 点踩（2026-11-26）：用户给这一轮点了踩的，判断时一起给模型看（选的分类、写的说明），点了踩就是用户的反应，不再等
  下一条消息；结论里记下点了踩和说明（disliked、dislike_note，数据照抄、不经模型），配置建议拿它当证据。点赞不给：
  建议都是先试过才出的，用不上。已经判过的轮次后来被点踩、改了说明、撤回或改成赞（requeue_rated，评分存下时调），
  检索过知识库、在 RERATE_DAYS 天内的，从会话记录里取回最后一条助手回答，重新排进待判断。原来点踩只进运行记录和
  问答大盘，用户点了踩、下一条又没说哪里不对，这个信号就丢了。
"""

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.events_v1 import assistant_text
from ..conversations.models import AnswerFeedback, RunEvent, RunSummary
from ..conversations.summaries import TurnEnded
from ..jobs import Done, JobContext, JobError, JobKind, JobRunner, enqueue
from ..models.catalog import DEFAULT_ALIAS
from ..models.client import ModelCallError, ModelClient
from ..profile.facts import VISION_TOOLS
from .citations import marked_numbers
from .fetch_tool import FETCH_NAME
from .tables import Citation, KnowledgeAnswerCheck, KnowledgeSearchHit, KnowledgeSearchLog

log = logging.getLogger(__name__)

# 放离线分析池：一次判 50 轮要调十次模型，原来放富化池，和新上传文档的图片描述、自动打标签抢槽；单独一池，
# 不影响文档解析与富化
KIND = JobKind("knowledge.answer_check", pool="analysis", max_tries=5, priority=-10, label="判断回答答上了没有")
BATCH_DELAY = 3600.0
PER_CALL = 5
PER_RUN = 50
# 一轮判几次都拿不到结论（返回格式坏了、内容太长）就放弃：不然它一直排在最前面，攒够 PER_RUN 条就把后面新的回合
# 永远挡住，还每小时白调一次模型
MAX_CHECK_TRIES = 3
MAX_SNIPPETS = 10
CITED_CHARS = 1500  # 回答标了编号的片段：回答依据的原文要给全，判断才核对得了
SNIPPET_CHARS = 400  # 按名次补进来的：只帮判断看出知识库里有没有，截短
SNIPPETS_BUDGET = 6000  # 一轮所有片段合计，一次判 PER_CALL 轮，别把上下文撑爆
CAPTION_CHARS = 200
ANSWER_CHARS = 4000
VERDICTS = frozenset({"answered", "partial", "unanswered"})
VERDICT_KEYS = frozenset({"verdict", "missing", "reason", "about", "follow", "correction", "rephrased", "disliked",
                          "dislike_note"})  # 判断写进知识事实的几项
# not_for_kb 不该知识库答（个人数据、写作任务、闲聊、外部最新信息）；content_missing 片段里没有（待核实：库里可能有、
# 这一轮没取到）；content_unclear 有但含糊、矛盾或过时；answer_off 有但回答没用上；own_knowledge 用的是模型自己的知识
REASONS = frozenset({"not_for_kb", "content_missing", "content_unclear", "answer_off", "own_knowledge"})
DEFAULT_REASON = "content_missing"
FOLLOWS = frozenset({"corrected", "rephrased", "satisfied", "none"})
KEPT_FOLLOWS = frozenset({"corrected", "rephrased"})  # 满意了、看不出来的不记
CONTEXT_TURNS = 2  # 给判官看前几轮的问题
RERATE_DAYS = 30  # 判过以后才点的踩，这么多天内的轮次重新判（和配置建议看的时间窗一样）
# 点踩的分类（DSH 官方 dsh-command-feedback 的固定分类）给判断看的中文名，和前端 api/feedback.ts 一致
CATEGORY_LABELS = {"task-result": "任务结果", "instruction-following": "指令理解与遵循",
                   "product-interaction": "产品功能与交互", "service-stability": "稳定性和速度",
                   "resource-cost": "资源使用与费用", "security-privacy-permission": "隐私与权限", "other": "其他"}
NEXT_WAIT = 600.0  # 答完不到这么久、用户还没发下一条的先不判（纠正、换说法都在下一条里）
MAX_MISSING = 200
MAX_QUOTE = 200  # 纠正、换说法摘的原话

PROMPT = """你在给企业知识库做质检。下面每一项是用户的一个问题、智能体的最终回答，以及这一轮给到的知识库片段（ref 是片段编号，
回答里的 [n] 指编号为 n 的片段，[IMG:n:k] 是把片段 n 的第 k 张图放进了回答，k 从 0 数）。context 是这段对话前几轮用户
问的（帮你看懂追问，比如「他」「那」指什么）；next_message 是用户紧接着发的下一条，没有这一项就是还没发或对话结束了；
feedback 是用户给这一轮回答点了踩：category 是他选的问题类型，note 是他写的说明（都可能没有）。

一、判断知识库里的内容答上这个问题了没有（verdict）：
- answered：用户问的都答上了，回答的主体内容在片段里有依据；问题本来就不需要知识库（写作、计算、闲聊）的也算。
- partial：用户问的有一部分没答上，或者回答的主体内容有一部分在片段里找不到依据。
- unanswered：没答上：说找不到、答非所问，或者答案不是出自片段。
- 用户在 next_message 里说回答不对、给出了不同的事实或口径，这一轮不算 answered。
- 用户点了踩（有 feedback）的，不算 answered；只有能看出点踩和回答的内容无关时（category 是稳定性和速度、资源使用与
  费用、隐私与权限、产品功能与交互，或者 note 说的是慢、格式、界面这类）才按内容照常判。

怎么判：
- 只按用户实际问的判断，不要替用户把问题扩大。问「有哪些」，回答列出了知识库里有的那些、并说明库里只有这些，算答上；
  用户没问的细节（主创、评分、日期之类）回答没写，不算缺。
- 回答里顺带的背景、建议、提示缺依据，不影响结论；只看回答问题的那部分。
- 片段结尾是「…」的截短了，回答依据的内容可能在截掉的部分，回答引用了这个片段、内容又和片段前文对得上的，不算没依据。
- images 是片段里图的描述，按图的顺序。图本身你看不到。用户只是要找这张图，回答把图放出来了，算答上；用户问图里画了
  什么，回答要说出画面才算答上，说出的画面和图的描述对得上，或者这一项带 looked_at_images（智能体这一轮把知识库里的图
  取出来看过了），都算有依据，不是模型自己的知识；回答说描述不了画面的，不算答上。

二、不是 answered 时，写原因（reason，五选一）和缺什么（missing）：
- not_for_kb：本来就不该由知识库答：问的是本人的个人数据（我的年假还剩几天）、要写作或生成文件（写一篇稿子、做个
  PPT）、闲聊、要外部最新信息（今天的新闻、最新的市场数据）；
- content_missing：片段里没有回答这个问题的内容；
- content_unclear：片段里有相关内容，但写得含糊、互相矛盾，或者已经过时（比如用户纠正说现在不是这样了）；
- answer_off：片段里有能答上的内容，回答没用上或答偏了；
- own_knowledge：回答的主体是片段之外的内容（模型自己的知识）。
missing 一句话写知识库该补的资料是哪一类（如「会员续费的退款规则」「差旅住宿标准」），不超过 60 字，写资料的名称，
不要抄回答或片段里的原句；not_for_kb 不用写。

三、refs：片段里和这个问题相关的编号（不管答没答上，相关就列），没有就给空数组。

四、用户接下来的反应（follow，看 next_message，四选一）：
- corrected：说回答不对、给了不同的事实或口径；correction 摘用户原话里给出的事实（不超过 100 字，原话，不要改写）；
  feedback 的 note 里给了不同的事实或口径的也算，correction 摘 note 的原话；
- rephrased：换了个说法重问同一件事；rephrased 写新的说法（原话）；
- satisfied：接着问别的、道谢，或者没有下一条；
- none：看不出来。

只输出 JSON，不要别的文字：
{"results": [{"id": "项的 id", "verdict": "answered|partial|unanswered", "reason": "...", "missing": "...", "refs": [1],
  "follow": "corrected|rephrased|satisfied|none", "correction": "...", "rephrased": "..."}]}"""


@dataclass(frozen=True)
class Pending:
    id: str
    conversation_id: str
    turn: int
    question: str
    answer: str
    snippets: tuple[dict[str, Any], ...]  # {"ref": 编号, "title", "text"}
    # 这一轮把知识库里的图取进沙箱、用 read_image 看过：回答里描述画面的内容出自它看到的图，判断看不到这一步，
    # 不告诉它就会当成模型自己的知识（2026-10-29：看过《东栏雪》海报、描述对了，被判成没答上）
    looked_at_images: bool = False
    context: tuple[str, ...] = ()  # 前几轮用户问的，按先后
    next_message: str | None = None  # 用户紧接着发的下一条
    kb_of: dict[int, str] = field(default_factory=dict)  # 片段编号 → 它出自的库（知识源.库，同知识事实里的 bases）
    disliked: dict[str, str] | None = None  # 用户点了踩：{category（中文）, note}，都可能没有；没点踩为 None

    def rating_facts(self) -> dict[str, Any]:
        """点了踩：结论里记下（数据照抄，不经模型），说明截到 MAX_QUOTE 字。"""
        if self.disliked is None:
            return {}
        note = " ".join((self.disliked.get("note") or "").split())[:MAX_QUOTE]
        return {"disliked": True, **({"dislike_note": note} if note else {})}


@dataclass(frozen=True)
class Verdict:
    verdict: str
    missing: str | None
    reason: str | None
    refs: tuple[int, ...] = ()
    follow: str = "none"
    correction: str | None = None
    rephrased: str | None = None

    def facts(self, kb_of: dict[int, str]) -> dict[str, Any]:
        """写进知识事实的几项：没答上的带缺什么与原因；相关片段对得上库的记 about；纠正、换说法记下原话。"""
        out: dict[str, Any] = {"verdict": self.verdict}
        if self.verdict != "answered":
            out = {**out, "missing": self.missing or "", "reason": self.reason or DEFAULT_REASON}
            if out["reason"] == "not_for_kb":
                out = {k: v for k, v in out.items() if k != "missing"}
        about = sorted({kb_of[ref] for ref in self.refs if ref in kb_of})
        if about:
            out = {**out, "about": about}
        if self.follow in KEPT_FOLLOWS:
            out = {**out, "follow": self.follow}
            quote = self.correction if self.follow == "corrected" else self.rephrased
            if quote:
                out = {**out, ("correction" if self.follow == "corrected" else "rephrased"): quote}
        return out


async def queue_check(session: AsyncSession, ended: TurnEnded) -> None:
    """记一轮待判断并排好这个空间的任务。在已经声明了空间的事务里调：回合结束（close_turn）、评分变了（requeue_rated）。
    同一轮再结束一次（重试、重新生成）以最后的回答为准。"""
    stmt = insert(KnowledgeAnswerCheck).values(
        id=f"kac_{uuid.uuid4().hex}", workspace_id=ended.workspace_id, conversation_id=ended.conversation_id,
        turn=ended.turn, question=ended.question[:2000], answer=ended.answer)
    await session.execute(stmt.on_conflict_do_update(
        index_elements=[KnowledgeAnswerCheck.conversation_id, KnowledgeAnswerCheck.turn],
        set_={"question": stmt.excluded.question, "answer": stmt.excluded.answer}))
    await enqueue(session, KIND, workspace_id=ended.workspace_id, subject_id=ended.workspace_id, delay=BATCH_DELAY)


async def check_now(session: AsyncSession, workspace_id: str) -> None:
    """不等整点，把这个空间排队中的判断提到现在（管理端「立即检查」用）。没有排队的也排一条：判的时候没有待判断的
    就什么都不做。在调用方声明了空间的事务里调。"""
    await enqueue(session, KIND, workspace_id=workspace_id, subject_id=workspace_id)


def parse_verdicts(text: str, ids: Sequence[str]) -> dict[str, Verdict]:
    """模型的返回里取出这几轮的结论；看不懂的、不认识的 id、取值不对的都跳过（留着下次再判）。"""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        body = json.loads(match.group(0)) if match else None
    except ValueError:
        body = None
    items = body.get("results") if isinstance(body, dict) else None
    wanted = set(ids)
    found: dict[str, Verdict] = {}
    for item in items if isinstance(items, list) else ():
        if not isinstance(item, dict) or item.get("id") not in wanted or item.get("verdict") not in VERDICTS:
            continue
        missing = " ".join(str(item.get("missing") or "").split())[:MAX_MISSING] or None
        reason = item.get("reason") if item.get("reason") in REASONS else None
        refs = tuple(ref for ref in item.get("refs") or () if isinstance(ref, int) and not isinstance(ref, bool)) \
            if isinstance(item.get("refs"), list) else ()
        follow = item.get("follow") if item.get("follow") in FOLLOWS else "none"
        found[item["id"]] = Verdict(item["verdict"], missing, reason, refs, follow,
                                    _quote(item.get("correction")), _quote(item.get("rephrased")))
    return found


def _quote(value: Any) -> str | None:
    return " ".join(str(value).split())[:MAX_QUOTE] or None if isinstance(value, str) else None


def _messages(batch: Sequence[Pending]) -> list[dict[str, str]]:
    items = [{"id": p.id, "question": p.question, "answer": p.answer[:ANSWER_CHARS], "snippets": list(p.snippets),
              **({"looked_at_images": True} if p.looked_at_images else {}),
              **({"context": list(p.context)} if p.context else {}),
              **({"next_message": p.next_message} if p.next_message else {}),
              **({"feedback": p.disliked} if p.disliked is not None else {})} for p in batch]
    return [{"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)}]


class AnswerChecker:
    def __init__(self, sessionmaker: async_sessionmaker, models: ModelClient, *,
                 wait_seconds: float = NEXT_WAIT) -> None:
        self._sessionmaker = sessionmaker
        self._models = models
        self._wait = wait_seconds

    def register(self, runner: JobRunner) -> None:
        runner.register(KIND, self.handle)

    async def handle(self, ctx: JobContext) -> Done | None:
        workspace_id = ctx.workspace_id
        async with ctx.stage("load") as stage:
            pending, waiting = await self._pending(workspace_id)
            stage.record(turns=len(pending), **({"waiting": waiting} if waiting else {}))
        judged = 0
        for start in range(0, len(pending), PER_CALL):
            batch = pending[start:start + PER_CALL]
            async with ctx.stage("judge", turns=len(batch)) as stage:
                verdicts = await self._judge(workspace_id, batch)
                await self._save(workspace_id, batch, verdicts)
                stage.record(judged=len(verdicts))
                judged += len(verdicts)
        full = len(pending) >= PER_RUN and judged > 0
        if not full and not waiting:
            return None

        # 一次没判完：马上接着排下一批（判不出来的那几轮不在这里打转，等下一小时）；只剩等下一条消息的：等够了再排，
        # 不靠这个空间下一轮对话来触发（对话可能就到这为止了）
        async def more(session: AsyncSession) -> None:
            await enqueue(session, KIND, workspace_id=workspace_id, subject_id=workspace_id,
                          **({} if full else {"delay": self._wait}))

        return Done(apply=more)

    async def _pending(self, workspace_id: str) -> tuple[list[Pending], int]:
        """这一批要判的，和还在等下一条消息的有几轮（答完不到 wait_seconds、用户还没接着说）。"""
        cutoff = datetime.now(UTC) - timedelta(seconds=self._wait)
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            checks = (await session.scalars(select(KnowledgeAnswerCheck).where(
                KnowledgeAnswerCheck.workspace_id == workspace_id)
                .order_by(KnowledgeAnswerCheck.created_at).limit(PER_RUN))).all()
            pending, waiting = [], 0
            for check in checks:
                before, after = await _around(session, check.conversation_id, check.turn)
                disliked = await _disliked(session, check.conversation_id, check.turn)
                if after is None and disliked is None and check.created_at > cutoff:
                    waiting += 1
                    continue
                snippets = await _snippets(session, check.conversation_id, check.turn, check.answer)
                pending.append(Pending(
                    check.id, check.conversation_id, check.turn, check.question, check.answer, snippets,
                    await _looked_at_images(session, check.conversation_id, check.turn), before, after,
                    await _kb_of(session, check.conversation_id, [snippet["ref"] for snippet in snippets]), disliked))
            return pending, waiting

    async def _judge(self, workspace_id: str, batch: Sequence[Pending]) -> dict[str, Verdict]:
        try:
            text = await self._models.chat(workspace_id, DEFAULT_ALIAS, _messages(batch), max_tokens=1500,
                                           temperature=0.0)
        except ModelCallError as error:
            raise JobError(error.code, error.message, retryable=error.retryable) from error
        verdicts = parse_verdicts(text, [p.id for p in batch])
        if len(verdicts) < len(batch):
            log.warning("answer check got %d of %d verdicts in %s", len(verdicts), len(batch), workspace_id)
        return verdicts

    async def _save(self, workspace_id: str, batch: Sequence[Pending], verdicts: dict[str, Verdict]) -> None:
        """有结论的并进每轮汇总、删掉待判断；没结论的记一次，满 MAX_CHECK_TRIES 次放弃。"""
        missed = [p.id for p in batch if p.id not in verdicts]
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            for pending in batch:
                verdict = verdicts.get(pending.id)
                if verdict is None:
                    continue
                summary = await session.get(RunSummary, (pending.conversation_id, pending.turn))
                if summary is not None:  # 会话删了就没有汇总了，结论也不用记
                    facts = dict(summary.facts or {})
                    # 重新生成的回答再判一次：上次的缺什么、原因先去掉，这次答上了就不留旧的
                    kept = {k: v for k, v in (facts.get("knowledge") or {}).items() if k not in VERDICT_KEYS}
                    summary.facts = {**facts, "knowledge": {**kept, **verdict.facts(pending.kb_of),
                                                            **pending.rating_facts()}}
            if verdicts:
                await session.execute(delete(KnowledgeAnswerCheck).where(
                    KnowledgeAnswerCheck.id.in_(list(verdicts))))
            if missed:
                await session.execute(update(KnowledgeAnswerCheck).where(KnowledgeAnswerCheck.id.in_(missed))
                                      .values(tries=KnowledgeAnswerCheck.tries + 1))
                given_up = (await session.scalars(delete(KnowledgeAnswerCheck).where(
                    KnowledgeAnswerCheck.id.in_(missed), KnowledgeAnswerCheck.tries >= MAX_CHECK_TRIES)
                    .returning(KnowledgeAnswerCheck.id))).all()
                if given_up:
                    log.warning("answer check gave up on %d turns in %s after %d tries", len(given_up),
                                workspace_id, MAX_CHECK_TRIES)


async def requeue_rated(sessionmaker: async_sessionmaker, workspace_id: str, conversation_id: str, turn: int) -> bool:
    """评分变了（点了踩、改了说明、撤回或改成赞）：已经判过的这一轮重新排进待判断，判的时候看最新的评分。检索过知识库、
    正常答完、在 RERATE_DAYS 天内的才排；还在等着判断的不用排（判的时候会读到最新的评分）。回答从会话记录里取回最后
    一条助手消息。返回排了没有。"""
    since = datetime.now(UTC) - timedelta(days=RERATE_DAYS)
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, workspace_id)
        summary = await session.get(RunSummary, (conversation_id, turn))
        if (summary is None or summary.workspace_id != workspace_id or summary.status != "completed"
                or summary.started_at is None or summary.started_at < since):
            return False
        searched = await session.scalar(select(exists().where(
            KnowledgeSearchLog.conversation_id == conversation_id, KnowledgeSearchLog.turn == turn)))
        waiting = await session.scalar(select(exists().where(
            KnowledgeAnswerCheck.conversation_id == conversation_id, KnowledgeAnswerCheck.turn == turn)))
        if not searched or waiting:
            return False
        answer = await _final_answer(session, conversation_id, turn)
        if not answer:
            return False
        await queue_check(session, TurnEnded(workspace_id=workspace_id, conversation_id=conversation_id, turn=turn,
                                             question=summary.question or "", answer=answer, status="completed"))
        return True


async def _final_answer(session: AsyncSession, conversation_id: str, turn: int) -> str:
    """会话记录里这一轮最后一条助手回答（同回合结束时交给各模块的 answer）：从这一轮的开始帧往后，到结束帧为止。"""
    event = RunEvent.data["type"].as_string()
    start = await session.scalar(select(RunEvent.seq).where(
        RunEvent.conversation_id == conversation_id, RunEvent.kind == "session/event", event == "turn/start",
        RunEvent.data["data"]["turn"].as_integer() == turn).order_by(RunEvent.seq.desc()).limit(1))
    if start is None:
        return ""
    rows = (await session.scalars(select(RunEvent.data).where(
        RunEvent.conversation_id == conversation_id, RunEvent.kind == "session/event", RunEvent.seq > start,
        event.in_(("assistant/message", "turn/start", "turn/end"))).order_by(RunEvent.seq).limit(500))).all()
    answer = ""
    for data in rows:
        if data.get("type") != "assistant/message":
            break  # 这一轮结束了（或下一轮开始了）
        answer = assistant_text(data.get("data")) or answer
    return answer


async def _disliked(session: AsyncSession, conversation_id: str, turn: int) -> dict[str, str] | None:
    """用户给这一轮点了踩：分类（中文）与说明，都可能没有；点赞、没评分为 None。"""
    row = await session.scalar(select(AnswerFeedback).where(
        AnswerFeedback.conversation_id == conversation_id, AnswerFeedback.run_id == f"run-{turn}",
        AnswerFeedback.rating == "dislike"))
    if row is None:
        return None
    return {**({"category": CATEGORY_LABELS.get(row.category, row.category)} if row.category else {}),
            **({"note": row.note} if row.note else {})}


async def _snippets(session: AsyncSession, conversation_id: str, turn: int,
                    answer: str) -> tuple[dict[str, Any], ...]:
    """给判断看的片段原文，带编号：先放回答里标了 [n] 的（回答所依据的原文，可能是这一轮名次靠后的、或者前几轮查到
    的），再按这一轮检索的名次补满，最多 MAX_SNIPPETS 段，从引用目录按编号取。只按名次取前几段时，回答用到的第 13 段
    没给出去，判断看不到出处，把答上的判成了只答了一部分（2026-10-27 联调发现）。这里不是看引用了几条来判答上没有，
    是把回答依据的原文交给判断去核对，所以标了编号的给得长、带图的描述，按名次补的截短，合计不超过 SNIPPETS_BUDGET。"""
    ranked = (await session.scalars(
        select(KnowledgeSearchHit.number).join(KnowledgeSearchLog, KnowledgeSearchLog.id == KnowledgeSearchHit.log_id)
        .where(KnowledgeSearchLog.conversation_id == conversation_id, KnowledgeSearchLog.turn == turn,
               KnowledgeSearchHit.number.is_not(None))
        .order_by(KnowledgeSearchHit.rank, KnowledgeSearchLog.created_at))).all()
    cited = marked_numbers(answer)
    wanted = list(dict.fromkeys([*sorted(cited), *ranked]))[:MAX_SNIPPETS]
    if not wanted:
        return ()
    rows = (await session.execute(select(Citation.number, Citation.title, Citation.text, Citation.images).where(
        Citation.catalog_id == conversation_id, Citation.number.in_(wanted)))).all()
    found = {row.number: row for row in rows}
    snippets: list[dict[str, Any]] = []
    left = SNIPPETS_BUDGET
    for number in wanted:
        row = found.get(number)
        if row is None or left <= 0:
            continue
        text = _clip(row.text, min(CITED_CHARS if number in cited else SNIPPET_CHARS, left))
        left -= len(text)
        captions = _captions(row.images) if number in cited else ()
        snippets.append({"ref": number, "title": row.title, "text": text, **({"images": captions} if captions else {})})
    return tuple(snippets)


async def _around(session: AsyncSession, conversation_id: str, turn: int) -> tuple[tuple[str, ...], str | None]:
    """前 CONTEXT_TURNS 轮用户问的（按先后，空的跳过），和下一轮用户发的那句（还没有就是 None）。"""
    rows = (await session.execute(select(RunSummary.turn, RunSummary.question).where(
        RunSummary.conversation_id == conversation_id, RunSummary.turn >= turn - CONTEXT_TURNS,
        RunSummary.turn <= turn + 1, RunSummary.turn != turn).order_by(RunSummary.turn))).all()
    before = tuple(question for number, question in rows if number < turn and question)
    after = next((question for number, question in rows if number == turn + 1 and question), None)
    return before, after


async def _kb_of(session: AsyncSession, conversation_id: str, numbers: Sequence[int]) -> dict[int, str]:
    """片段编号 → 出自哪个库：引用编号在一段对话里不变，从这段对话任意一轮的检索命中里对（前几轮查到、这一轮引用的
    也对得上）。对不上的不在里面。"""
    if not numbers:
        return {}
    rows = (await session.execute(
        select(KnowledgeSearchHit.number, KnowledgeSearchHit.source_id, KnowledgeSearchHit.kb_id)
        .join(KnowledgeSearchLog, KnowledgeSearchLog.id == KnowledgeSearchHit.log_id)
        .where(KnowledgeSearchLog.conversation_id == conversation_id, KnowledgeSearchHit.number.in_(list(numbers)))
        .distinct())).all()
    return {number: f"{source}.{kb}" for number, source, kb in rows}


async def _looked_at_images(session: AsyncSession, conversation_id: str, turn: int) -> bool:
    """这一轮既取过知识库的文件（knowledge_fetch），又看过图（read_image）。只看过图的可能是用户传的附件，不算。"""
    tools = await session.scalar(select(RunSummary.tools).where(
        RunSummary.conversation_id == conversation_id, RunSummary.turn == turn))
    return {FETCH_NAME, *VISION_TOOLS} <= set(tools or ())


def _clip(text: str, limit: int) -> str:
    """压掉空白后截到 limit 字；截了的以「…」结尾，判断知道后面还有、看不到的不算没依据。"""
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[:max(limit - 1, 0)] + "…"


def _captions(images: list | None) -> list[str]:
    """片段里图的描述（图注、补的描述），按图的顺序，和回答里 [IMG:n:k] 的 k 对得上；没有描述的写「无描述」占位。"""
    return [" ".join(str(image.get("caption") or "").split())[:CAPTION_CHARS] or "无描述" for image in images or ()
            if isinstance(image, dict)]
