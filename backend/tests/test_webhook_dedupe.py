import pytest
from conftest import encode_body, payment_failed_body, sign_payload
from sqlalchemy import func, select

from app.database.models import RawEvent
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus

pytestmark = pytest.mark.asyncio


async def post(client, body: dict, *, event_id: str | None = "evt_A", signature: str | None = "computed"):
    headers: dict[str, str] = {}
    if signature == "computed":
        headers["X-Razorpay-Signature"] = sign_payload(encode_body(body))
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return await client.post("/webhooks/razorpay", content=encode_body(body), headers=headers)


async def count_rows() -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(RawEvent))
        return int(result.scalar_one())


async def test_header_event_id_dedupes_redelivery(client):
    body = payment_failed_body()
    first = await post(client, body, event_id="evt_SAME")
    second = await post(client, body, event_id="evt_SAME")
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    assert await count_rows() == 1


async def test_content_hash_fallback_dedupe_without_event_id_header(client):
    body = payment_failed_body()
    first = await post(client, body, event_id=None)
    second = await post(client, body, event_id=None)
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "duplicate"}
    assert await count_rows() == 1


async def test_distinct_events_both_stored(client):
    first = await post(client, payment_failed_body(payment_id="pay_A"), event_id="evt_1")
    second = await post(client, payment_failed_body(payment_id="pay_B"), event_id="evt_2")
    assert first.json() == {"status": "accepted"}
    assert second.json() == {"status": "accepted"}
    assert await count_rows() == 2


async def test_bus_publishes_once_for_duplicate_delivery(client):
    queue = bus.subscribe()
    try:
        body = payment_failed_body()
        await post(client, body)
        await post(client, body)
        published: list[StreamEvent] = []
        while not queue.empty():
            published.append(queue.get_nowait())
        assert len(published) == 1
        assert published[0].data["event_type"] == "payment.failed"
        assert published[0].data["entity_id"] == "pay_TESTFAIL001"
    finally:
        bus.unsubscribe(queue)


async def test_event_meta_extracted_correctly(client):
    await post(client, payment_failed_body(payment_id="pay_META"))
    async with session_factory() as session:
        row = (await session.execute(select(RawEvent))).scalars().one()
    assert row.event_type == "payment.failed"
    assert row.entity_type == "payment"
    assert row.entity_id == "pay_META"
    assert row.entity_ids == ["payment:pay_META"]
    assert row.payload["payload"]["payment"]["entity"]["amount"] == 420000
