"""回合结束时告诉扩展的知识源：这一轮的回答引用了它的哪几份文档、是谁问的（声明了 cited 能力的才告诉，例如按人记
常用资料）。按回答里标的编号查会话的引用目录，同一份文档只给一次；没答完、不知道是谁问的不给；一个知识源出错不影响别的。"""

from dataclasses import dataclass, field

import pytest

from knowwork_server.conversations.summaries import TurnEnded
from knowwork_server.knowledge.cited import CitationNotifier
from knowwork_server.knowledge.models import Caller, CitedDocument, Evidence
from knowwork_server.knowledge.provided import ProvidedSource, ProvidedSources

pytestmark = pytest.mark.anyio


@dataclass
class Listening:
    source_id: str
    features: frozenset = frozenset({"cited"})
    heard: list = field(default_factory=list)
    broken: bool = False

    async def cited(self, caller, documents):
        if self.broken:
            raise RuntimeError("挂了")
        self.heard.append((caller, documents))


def ev(source: str, doc: str, span: str) -> Evidence:
    return Evidence(source_id=source, kb_id="kb1", doc_ref=f"kb1/{doc}", span_ref=span, title=f"文档{doc}",
                    text=f"{span} 的正文")


def ended(answer: str, status: str = "completed", user_id: str | None = "alice") -> TurnEnded:
    return TurnEnded(workspace_id="ws1", conversation_id="c-9", turn=1, question="报销", answer=answer, status=status,
                     user_id=user_id)


async def test_sources_hear_which_of_their_documents_were_cited(env):
    mine, deaf, broken = Listening("mine"), Listening("deaf", features=frozenset()), Listening("oops", broken=True)
    provided = ProvidedSources()
    for item in (mine, deaf, broken):
        provided.add(ProvidedSource(id=item.source_id, name=item.source_id, kind="test", source=item))
    await env.knowledge.citations.register("ws1", "c-9", [
        ev("mine", "a", "a1"), ev("mine", "a", "a2"), ev("deaf", "b", "b1"), ev("oops", "c", "c1"),
        ev("mine", "d", "d1")])
    notifier = CitationNotifier(env.sessionmaker, provided)

    assert await notifier.turn_ended(ended("先填单 [1]，再审批 [2][3][4]。")) is None
    assert mine.heard == [(Caller(workspace_id="ws1", user_id="alice"), (CitedDocument("kb1/a", "文档a"),))], \
        "同一份文档只给一次，没引用的不给"
    assert deaf.heard == [], "没声明 cited 的不告诉"

    for skipped in (ended("[5]", status="cancelled"), ended("[5]", user_id=None), ended("没有引用")):
        await notifier.turn_ended(skipped)
    assert len(mine.heard) == 1, "没答完、不知道是谁、没引用的不告诉"
