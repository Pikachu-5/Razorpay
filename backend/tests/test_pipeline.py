import pytest
from conftest import payment_failed_body
from sqlalchemy import select

from app.database.models import Customer, Opportunity, Payment
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus
from app.events.processor import assign_experiment_group, handle_stream_event, process_raw_event

pytestmark = pytest.mark.asyncio


async def test_handle_stream_event_glue_end_to_end():
    queue = bus.subscribe()
    try:
        body = body_for("pay_GLUE01")
        stream_event = StreamEvent(
            kind="razorpay.event",
            data={"event_uid": "evt_glue_1", "event_type": "payment.failed", "payload": body},
        )
        outcomes = await handle_stream_event(stream_event)
        assert "opportunity.created" in [o.kind for o in outcomes]

        published = []
        while not queue.empty():
            published.append(queue.get_nowait())
        assert any(p.kind == "opportunity.created" for p in published)

        payment = await get_payment("pay_GLUE01")
        assert payment is not None
    finally:
        bus.unsubscribe(queue)


async def test_handle_stream_event_drops_malformed():
    outcomes = await handle_stream_event(
        StreamEvent(kind="razorpay.event", data={"event_uid": "evt_x"})
    )
    assert outcomes == []


def body_for(
    payment_id: str,
    event: str = "payment.failed",
    amount_minor: int = 420000,
    email: str = "customer@example.com",
) -> dict:
    return payment_failed_body(payment_id=payment_id, amount_minor=amount_minor) | {
        "event": event,
        "payload": {
            "payment": {
                "entity": {
                    "id": payment_id,
                    "entity": "payment",
                    "amount": amount_minor,
                    "currency": "INR",
                    "status": event.split(".")[1],
                    "method": "netbanking",
                    "bank": "HDFC",
                    "email": email,
                    "contact": "+919999900000",
                    "error_code": "BAD_REQUEST_ERROR" if event == "payment.failed" else None,
                    "error_reason": "payment_failed" if event == "payment.failed" else None,
                    "created_at": 1724567890,
                }
            }
        },
    }


async def get_payment(razorpay_id: str) -> Payment | None:
    async with session_factory() as session:
        result = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == razorpay_id)
        )
        return result.scalar_one_or_none()


async def get_opportunities() -> list[Opportunity]:
    async with session_factory() as session:
        result = await session.execute(select(Opportunity))
        return list(result.scalars().all())


async def test_failed_payment_creates_customer_payment_opportunity():
    outcomes = await process_raw_event("evt_uid_1", body_for("pay_PIPE001"))

    kinds = [o.kind for o in outcomes]
    assert "payment.recorded" in kinds
    assert "opportunity.created" in kinds

    async with session_factory() as session:
        customers = list((await session.execute(select(Customer))).scalars())
    assert len(customers) == 1
    assert customers[0].identity_key == "email:customer@example.com"

    payment = await get_payment("pay_PIPE001")
    assert payment is not None
    assert payment.status == "failed"
    assert payment.bank == "HDFC"
    assert payment.error_reason == "payment_failed"

    opps = await get_opportunities()
    assert len(opps) == 1
    assert opps[0].status == "open"
    assert opps[0].amount_minor == 420000
    assert opps[0].experiment_group in {"treatment", "control"}
    assert opps[0].window_ends_at is not None


async def test_duplicate_failure_does_not_create_second_opportunity():
    await process_raw_event("evt_uid_dup", body_for("pay_PIPE002"))
    await process_raw_event("evt_uid_dup", body_for("pay_PIPE002"))
    assert len(await get_opportunities()) == 1


async def test_late_capture_resolves_opportunity_as_natural_recovery():
    await process_raw_event("evt_uid_f", body_for("pay_PIPE003", "payment.failed"))
    outcomes = await process_raw_event("evt_uid_c", body_for("pay_PIPE003", "payment.captured"))

    resolutions = [o for o in outcomes if o.kind == "opportunity.resolved"]
    assert len(resolutions) == 1
    assert resolutions[0].data["resolution"] == "recovered_natural"

    opps = await get_opportunities()
    assert len(opps) == 1
    assert opps[0].status == "recovered_natural"

    payment = await get_payment("pay_PIPE003")
    assert payment.status == "captured"


async def test_out_of_order_capture_then_failed_skips_opportunity():
    await process_raw_event("evt_uid_cap", body_for("pay_PIPE004", "payment.captured"))
    outcomes = await process_raw_event("evt_uid_fail", body_for("pay_PIPE004", "payment.failed"))

    skips = [o for o in outcomes if o.kind == "opportunity.skipped"]
    assert len(skips) == 1
    assert "already captured" in skips[0].data["reason"]
    assert await get_opportunities() == []
    payment = await get_payment("pay_PIPE004")
    assert payment is not None
    assert payment.status == "captured"


async def test_control_holdout_can_record_natural_recovery():
    await process_raw_event("evt_uid_control_f", body_for("pay_CONTROL", "payment.failed"))
    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity))).scalar_one()
        opp.status = "control_holdout"
        await session.commit()

    await process_raw_event("evt_uid_control_c", body_for("pay_CONTROL", "payment.captured"))
    opps = await get_opportunities()
    assert opps[0].status == "recovered_natural"


async def test_experiment_assignment_deterministic():
    assert assign_experiment_group("pay_X") == assign_experiment_group("pay_X")
    groups = {assign_experiment_group(f"pay_{i}") for i in range(200)}
    assert groups == {"treatment", "control"}


async def test_unknown_event_type_is_observed_not_crashing():
    outcomes = await process_raw_event(
        "evt_uid_refund", {"event": "refund.processed", "payload": {}}
    )
    assert [o.kind for o in outcomes] == ["event.observed"]
    assert await get_opportunities() == []


async def test_customer_upsert_reuses_existing_identity():
    await process_raw_event("evt_uid_a", body_for("pay_PIPE005", email="repeat@example.com"))
    await process_raw_event("evt_uid_b", body_for("pay_PIPE006", email="repeat@example.com"))
    async with session_factory() as session:
        customers = list((await session.execute(select(Customer))).scalars())
    assert len(customers) == 1


async def _make_opportunity(status: str, amount_minor: int, expected_minor: int,
                            closed_reason: str | None = None) -> None:
    """Insert one opportunity with the payment row its FK requires."""
    import uuid as uuid_lib
    from datetime import datetime, timedelta, timezone

    from app.database.models import Opportunity, Payment
    from app.database.session import session_factory

    now = datetime.now(timezone.utc)
    payment_id = uuid_lib.uuid4()
    async with session_factory() as session:
        session.add(
            Payment(
                id=payment_id,
                razorpay_payment_id=f"pay_queue_{payment_id.hex[:12]}",
                status="failed", amount_minor=amount_minor, currency="INR",
                method="upi", bank="HDFC", error_reason="timeout",
                occurred_at=now,
            )
        )
        # The unit of work has no reason to order these, and the FK does.
        await session.flush()
        session.add(
            Opportunity(
                id=uuid_lib.uuid4(), payment_id=payment_id, status=status,
                category="failed_payment", amount_minor=amount_minor,
                expected_recovery_minor=expected_minor, experiment_group="treatment",
                window_ends_at=now + timedelta(hours=48),
                closed_reason=closed_reason,
            )
        )
        await session.commit()


async def test_queue_surfaces_escalations_the_value_ranked_list_buries(client):
    """Escalated opportunities carry zero expected value and sort to the bottom.

    The triage queue must not be a page of the value-ranked list, or the work it
    exists to surface is exactly the work it cannot see.
    """
    await _make_opportunity("escalated", 9_000_000, 0, "policy blocked: amount_cap")
    for index in range(3):
        await _make_opportunity("open", 100_000, 500_000 + index)

    ranked = (await client.get("/api/opportunities?limit=3")).json()
    assert all(o["status"] != "escalated" for o in ranked), "setup: escalation should sort last"

    queue = (await client.get("/api/opportunities/queue")).json()
    escalations = [o for o in queue if o["status"] == "escalated"]
    assert len(escalations) == 1
    assert escalations[0]["closed_reason"] == "policy blocked: amount_cap"


async def test_queue_excludes_resolved_opportunities(client):
    for status in ("recovered_natural", "recovered_intervention", "closed_not_viable"):
        await _make_opportunity(status, 500_000, 0)

    assert (await client.get("/api/opportunities/queue")).json() == []
