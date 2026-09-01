from sqlalchemy import select

from app.database.models import Incident, PaymentDowntime, PaymentLinkState, RevenueAdjustment
from app.database.session import session_factory
from app.events.processor import process_raw_event


async def test_payment_link_partial_tracks_due_without_claiming_recovery():
    outcomes = await process_raw_event("evt_link_partial", {
        "event": "payment_link.partially_paid",
        "payload": {"payment_link": {"entity": {
            "id": "plink_partial_1", "status": "partially_paid",
            "amount": 100_000, "amount_paid": 35_000,
        }}},
    })
    assert outcomes[0].kind == "payment_link.partial"
    assert outcomes[0].data["amount_due_minor"] == 65_000
    async with session_factory() as session:
        state = await session.get(PaymentLinkState, "plink_partial_1")
        assert state is not None
        assert state.status == "partially_paid"
        assert state.amount_paid_minor == 35_000


async def test_official_downtime_opens_then_resolves_incident():
    started = {"event": "payment.downtime.started", "payload": {
        "payment.downtime": {"entity": {
            "id": "down_1", "status": "started", "method": "netbanking",
            "severity": "high", "instrument": {"bank": "HDFC"},
        }}
    }}
    await process_raw_event("evt_down_start", started)
    async with session_factory() as session:
        state = await session.get(PaymentDowntime, "down_1")
        incident = (await session.execute(select(Incident).where(Incident.source == "razorpay_downtime"))).scalar_one()
        assert state is not None and state.status == "started"
        assert incident.status == "detected"

    started["event"] = "payment.downtime.resolved"
    started["payload"]["payment.downtime"]["entity"]["status"] = "resolved"
    await process_raw_event("evt_down_resolved", started)
    async with session_factory() as session:
        incident = (await session.execute(select(Incident).where(Incident.source == "razorpay_downtime"))).scalar_one()
        assert incident.status == "resolved"
        assert incident.resolved_at is not None


async def test_refund_is_persisted_as_signed_revenue_adjustment():
    outcomes = await process_raw_event("evt_refund", {
        "event": "refund.processed",
        "payload": {"refund": {"entity": {
            "id": "rfnd_1", "payment_id": "pay_1", "status": "processed",
            "amount": 42_500,
        }}},
    })
    assert outcomes[0].kind == "revenue.refund.processed"
    async with session_factory() as session:
        row = (await session.execute(select(RevenueAdjustment))).scalar_one()
        assert row.kind == "refund"
        assert row.amount_minor == 42_500
        assert row.razorpay_payment_id == "pay_1"
