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
  没答全时写一句缺什么，原因选 kb_lacks（知识库里没有）、own_knowledge（用的是模型自己的知识）、off_topic（答非所问）。
  不看回答引用了几条：智能体答不上来时常常还会引用沾边的背景，引用条数说明不了答没答上。
- 结论并进每轮汇总的知识事实（verdict、missing、reason），判完删掉待判断那一行。用默认模型。
- 一批的返回看不懂、或者缺了某几轮，这几轮留着下次再判，判 MAX_CHECK_TRIES 次都没结论就放弃（这一轮不算没答上）；
  模型调不通按服务问题重试（不算这一轮的次数）。
- 管理端可以「立即检查」（check_now）：排队中的任务提到现在，不等整点。
"""

import json
import logging
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..access.rls import set_workspace
from ..conversations.models import RunSummary
from ..conversations.summaries import TurnEnded
from ..jobs import Done, JobContext, JobError, JobKind, JobRunner, enqueue
from ..models.catalog import DEFAULT_ALIAS
from ..models.client import ModelCallError, ModelClient
from ..profile.facts import VISION_TOOLS
from .citations import marked_numbers
from .fetch_tool import FETCH_NAME
from .tables import Citation, KnowledgeAnswerCheck, KnowledgeSearchHit, KnowledgeSearchLog

log = logging.getLogger(__name__)

# 放富化池：一次判 50 轮要调十次模型，占着 core（文档、附件解析，用户等着）的槽好几分钟，上传的文档就得排队
KIND = JobKind("knowledge.answer_check", pool="enrichment", max_tries=5, priority=-10, label="判断回答答上了没有")
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
VERDICT_KEYS = frozenset({"verdict", "missing", "reason"})  # 判断写进知识事实的几项
REASONS = frozenset({"kb_lacks", "own_knowledge", "off_topic"})
MAX_MISSING = 200

PROMPT = """你在给企业知识库做质检。下面每一项是用户的一个问题、智能体的最终回答，以及知识库里的片段（ref 是片段编号，
回答里的 [n] 指编号为 n 的片段，[IMG:n:k] 是把片段 n 的第 k 张图放进了回答，k 从 0 数）。判断：知识库里的内容答上这个问题了没有。

- answered：用户问的都答上了，回答的主体内容在片段里有依据；问题本来就不需要知识库（写作、计算、闲聊）的也算。
- partial：用户问的有一部分没答上，或者回答的主体内容有一部分在片段里找不到依据。
- unanswered：没答上：说找不到、答非所问，或者答案不是出自片段。

怎么判：
- 只按用户实际问的判断，不要替用户把问题扩大。问「有哪些」，回答列出了知识库里有的那些、并说明库里只有这些，算答上；
  用户没问的细节（主创、评分、日期之类）回答没写，不算缺。
- 回答里顺带的背景、建议、提示缺依据，不影响结论；只看回答问题的那部分。
- 片段结尾是「…」的截短了，回答依据的内容可能在截掉的部分，回答引用了这个片段、内容又和片段前文对得上的，不算没依据。
- images 是片段里图的描述，按图的顺序。图本身你看不到。用户只是要找这张图，回答把图放出来了，算答上；用户问图里画了
  什么，回答要说出画面才算答上，说出的画面和图的描述对得上，或者这一项带 looked_at_images（智能体这一轮把知识库里的图
  取出来看过了），都算有依据，不是模型自己的知识；回答说描述不了画面的，不算答上。

不是 answered 时：
- missing：一句话写知识库该补上的资料是哪一类（如「会员续费的退款规则」「差旅住宿标准」），不超过 60 字；
  写资料的名称，不要抄回答或片段里的原句；
- reason：kb_lacks（片段里没有相关内容）、own_knowledge（回答里有片段之外的内容，是模型自己的知识）、
  off_topic（片段里有，但回答没答到点上）三选一。

只输出 JSON，不要别的文字：
{"results": [{"id": "项的 id", "verdict": "answered|partial|unanswered", "missing": "...", "reason": "..."}]}"""


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


@dataclass(frozen=True)
class Verdict:
    verdict: str
    missing: str | None
    reason: str | None

    def facts(self) -> dict[str, Any]:
        if self.verdict == "answered":
            return {"verdict": self.verdict}
        return {"verdict": self.verdict, "missing": self.missing or "", "reason": self.reason or "kb_lacks"}


async def queue_check(session: AsyncSession, ended: TurnEnded) -> None:
    """记一轮待判断并排好这个空间的任务。在 close_turn 的事务里调（已经声明了空间）。同一轮再结束一次（重试、
    重新生成）以最后的回答为准。"""
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
        found[item["id"]] = Verdict(item["verdict"], missing, reason)
    return found


def _messages(batch: Sequence[Pending]) -> list[dict[str, str]]:
    items = [{"id": p.id, "question": p.question, "answer": p.answer[:ANSWER_CHARS], "snippets": list(p.snippets),
              **({"looked_at_images": True} if p.looked_at_images else {})} for p in batch]
    return [{"role": "system", "content": PROMPT},
            {"role": "user", "content": json.dumps(items, ensure_ascii=False)}]


class AnswerChecker:
    def __init__(self, sessionmaker: async_sessionmaker, models: ModelClient) -> None:
        self._sessionmaker = sessionmaker
        self._models = models

    def register(self, runner: JobRunner) -> None:
        runner.register(KIND, self.handle)

    async def handle(self, ctx: JobContext) -> Done | None:
        workspace_id = ctx.workspace_id
        async with ctx.stage("load") as stage:
            pending = await self._pending(workspace_id)
            stage.record(turns=len(pending))
        judged = 0
        for start in range(0, len(pending), PER_CALL):
            batch = pending[start:start + PER_CALL]
            async with ctx.stage("judge", turns=len(batch)) as stage:
                verdicts = await self._judge(workspace_id, batch)
                await self._save(workspace_id, batch, verdicts)
                stage.record(judged=len(verdicts))
                judged += len(verdicts)
        if len(pending) < PER_RUN or judged == 0:
            return None

        # 一次没判完：马上接着排下一批（判不出来的那几轮不在这里打转，等下一小时）
        async def more(session: AsyncSession) -> None:
            await enqueue(session, KIND, workspace_id=workspace_id, subject_id=workspace_id)

        return Done(apply=more)

    async def _pending(self, workspace_id: str) -> list[Pending]:
        async with self._sessionmaker() as session, session.begin():
            await set_workspace(session, workspace_id)
            checks = (await session.scalars(select(KnowledgeAnswerCheck).where(
                KnowledgeAnswerCheck.workspace_id == workspace_id)
                .order_by(KnowledgeAnswerCheck.created_at).limit(PER_RUN))).all()
            return [Pending(c.id, c.conversation_id, c.turn, c.question, c.answer,
                            await _snippets(session, c.conversation_id, c.turn, c.answer),
                            await _looked_at_images(session, c.conversation_id, c.turn)) for c in checks]

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
                    summary.facts = {**facts, "knowledge": {**kept, **verdict.facts()}}
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
