import asyncio
import json

import pytest

from app.api.stream import event_frames
from app.events.bus import StreamEvent, bus

pytestmark = pytest.mark.asyncio


async def test_event_frames_deliver_published_events():
    queue = bus.subscribe()
    gen = event_frames(queue)
    try:
        first = await gen.__anext__()
        assert first["event"] == "connected"

        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.05)
        await bus.publish(
            StreamEvent(
                kind="payment.recorded",
                data={"razorpay_payment_id": "pay_SSE1", "status": "failed"},
            )
        )
        frame = await asyncio.wait_for(task, timeout=5)
        assert frame["event"] == "payment_recorded"
        payload = json.loads(frame["data"])
        assert payload["kind"] == "payment.recorded"
        assert payload["data"]["razorpay_payment_id"] == "pay_SSE1"
    finally:
        await gen.aclose()


async def test_metrics_summary_reflects_pipeline_state(client):
    resp = await client.get("/api/metrics/summary")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) >= {
        "revenue_at_risk_minor",
        "open_opportunities",
        "recovered_today_minor",
        "events_received_today",
        "payments_by_status",
        "success_rate_today",
    }
