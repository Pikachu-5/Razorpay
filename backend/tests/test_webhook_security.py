import pytest
from conftest import encode_body, payment_failed_body, sign_payload
from sqlalchemy import func, select

from app.database.models import RawEvent
from app.database.session import session_factory

pytestmark = pytest.mark.asyncio


async def post(client, body: dict, *, signature: str | None = "computed", event_id: str | None = "evt_A"):
    headers: dict[str, str] = {}
    if signature == "computed":
        headers["X-Razorpay-Signature"] = sign_payload(encode_body(body))
    elif signature is not None:
        headers["X-Razorpay-Signature"] = signature
    if event_id:
        headers["x-razorpay-event-id"] = event_id
    return await client.post("/webhooks/razorpay", content=encode_body(body), headers=headers)


async def count_rows() -> int:
    async with session_factory() as session:
        result = await session.execute(select(func.count()).select_from(RawEvent))
        return int(result.scalar_one())


async def test_valid_signature_accepted_and_persisted(client):
    resp = await post(client, payment_failed_body())
    assert resp.status_code == 200
    assert resp.json() == {"status": "accepted"}
    assert await count_rows() == 1


async def test_forged_signature_rejected(client):
    body = payment_failed_body()
    resp = await post(client, body, signature="0" * 64)
    assert resp.status_code == 400
    assert await count_rows() == 0


async def test_missing_signature_header_rejected(client):
    resp = await post(client, payment_failed_body(), signature=None)
    assert resp.status_code == 400
    assert await count_rows() == 0


async def test_signature_over_modified_body_rejected(client):
    signed_body = payment_failed_body()
    tampered_body = payment_failed_body(amount_minor=99999999)
    headers = {"X-Razorpay-Signature": sign_payload(encode_body(signed_body))}
    resp = await client.post("/webhooks/razorpay", content=encode_body(tampered_body), headers=headers)
    assert resp.status_code == 400


async def test_unconfigured_secret_fails_closed(client, monkeypatch):
    class StubSettings:
        razorpay_webhook_secret = ""
        webhook_secret_configured = False

    import app.api.webhooks as webhooks_module

    monkeypatch.setattr(webhooks_module, "get_settings", lambda: StubSettings())
    resp = await post(client, payment_failed_body())
    assert resp.status_code == 503
    assert await count_rows() == 0


async def test_invalid_json_after_valid_signature_rejected(client):
    raw = b"{not-json"
    headers = {"X-Razorpay-Signature": sign_payload(raw), "x-razorpay-event-id": "evt_X"}
    resp = await client.post("/webhooks/razorpay", content=raw, headers=headers)
    assert resp.status_code == 400
