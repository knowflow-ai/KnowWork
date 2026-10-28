"""用量记录：同一步只记一次，同事务累加日汇总；按用户、模型、天查询；费用按单价估算。"""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from knowwork_server.access.rls import set_workspace
from knowwork_server.models.errors import Invalid
from knowwork_server.models.recorder import GroupBy, UsageRecorder, estimate_cost
from knowwork_server.models.schemas import ModelCreate, ProviderCreate
from knowwork_server.models.tables import UsageRecord
from knowwork_server.models.usage import UsageContext, usage_from_event

pytestmark = pytest.mark.anyio


def entry(user="alice", model="ds", turn=1, step=0, inputs=1000, outputs=500, at=None, ws="ws1", conv="c1"):
    ctx = UsageContext(workspace_id=ws, user_id=user, conversation_id=conv, model_alias=model)
    data = {"turn": turn, "step": step, "usage": {"inputTokens": inputs, "outputTokens": outputs}}
    return usage_from_event("assistant/message", data, ctx, at or datetime(2026, 10, 18, 4, tzinfo=UTC))


async def _priced_model(catalog):
    provider = await catalog.create_provider("root", ProviderCreate(name="p", base_url="https://h/v1"))
    await catalog.create_model("root", ModelCreate(provider_id=provider.id, alias="ds", name="deepseek-chat",
                                                         input_price_per_mtok=Decimal(2),
                                                         output_price_per_mtok=Decimal(8)))


async def test_record_dedupes_and_aggregates(models, sessionmaker):
    await _priced_model(models.catalog)
    recorder = models.recorder
    assert await recorder.record(entry())
    assert not await recorder.record(entry()), "宿主重放同一步不重复计量"
    assert await recorder.record(entry(step=1, inputs=200, outputs=100))
    assert await recorder.record(entry(user="carol", model="unpriced", conv="c2"))
    day = date(2026, 10, 18)
    by_user = await recorder.summary("ws1", day, day, GroupBy.USER)
    assert [(s.key, s.calls, s.input_tokens, s.output_tokens, s.total_tokens) for s in by_user] == [
        ("alice", 2, 1200, 600, 1800), ("carol", 1, 1000, 500, 1500)]
    assert by_user[0].cost == Decimal("0.007200") and by_user[1].cost == Decimal(0)
    by_model = await recorder.summary("ws1", day, day, GroupBy.MODEL)
    assert [(s.key, s.calls) for s in by_model] == [("ds", 2), ("unpriced", 1)]
    mine = await recorder.summary("ws1", day, day, GroupBy.MODEL, user_id="carol")
    assert [(s.key, s.total_tokens) for s in mine] == [("unpriced", 1500)]
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, "ws1")
        rows = (await session.scalars(select(UsageRecord).order_by(UsageRecord.id))).all()
    assert len(rows) == 3 and rows[0].model_id is not None and rows[2].model_id is None
    assert rows[0].cost == Decimal("0.006000") and rows[2].cost is None


async def test_day_follows_timezone(sessionmaker):
    recorder = UsageRecorder(sessionmaker)  # 默认 Asia/Shanghai
    await recorder.record(entry(at=datetime(2026, 10, 17, 17, tzinfo=UTC)))  # 北京时间 18 日 01:00
    await recorder.record(entry(step=1, at=datetime(2026, 10, 17, 15, tzinfo=UTC)))  # 北京时间 17 日 23:00
    by_day = await recorder.summary("ws1", date(2026, 10, 1), date(2026, 10, 31), GroupBy.DAY)
    assert [(s.key, s.calls) for s in by_day] == [("2026-10-17", 1), ("2026-10-18", 1)]
    utc = UsageRecorder(sessionmaker, timezone="UTC")
    await utc.record(entry(conv="c9", at=datetime(2026, 10, 17, 17, tzinfo=UTC)))
    assert (await utc.summary("ws1", date(2026, 10, 17), date(2026, 10, 17), GroupBy.DAY))[0].calls == 2


async def test_usage_is_isolated_per_workspace(sessionmaker):
    recorder = UsageRecorder(sessionmaker)
    await recorder.record(entry(ws="ws1"))
    await recorder.record(entry(ws="ws2"))
    day = date(2026, 10, 18)
    assert (await recorder.summary("ws2", day, day, GroupBy.USER))[0].calls == 1
    async with sessionmaker() as session, session.begin():
        await set_workspace(session, "ws2")
        assert await session.scalar(select(func.count()).select_from(UsageRecord)) == 1


async def test_rejects_bad_input(sessionmaker):
    recorder = UsageRecorder(sessionmaker)
    naive = entry(at=datetime(2026, 10, 18, 4))
    with pytest.raises(Invalid):
        await recorder.record(naive)
    with pytest.raises(Invalid):
        await recorder.summary("ws1", date(2026, 10, 2), date(2026, 10, 1), GroupBy.USER)
    with pytest.raises(Invalid):
        await recorder.summary("ws1", date(2025, 1, 1), date(2026, 10, 1), GroupBy.USER)
    with pytest.raises(Invalid):
        await recorder.summary("bad ws", date(2026, 10, 1), date(2026, 10, 1), GroupBy.USER)


def test_estimate_cost():
    e = entry(inputs=1_000_000, outputs=250_000)
    assert estimate_cost(e, None, None) is None
    assert estimate_cost(e, Decimal(2), None) == Decimal("2.000000")
    assert estimate_cost(e, Decimal(2), Decimal(8)) == Decimal("4.000000")
