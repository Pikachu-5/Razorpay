import hashlib
import hmac
import json
import logging
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.core.config import get_settings
from app.database.models import RawEvent
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus
from app.events.canonical import canonical_hash, extract_event_meta

logger = logging.getLogger("webhooks")

router = APIRouter(tags=["webhooks"])

SIGNATURE_HEADER = "X-Razorpay-Signature"
EVENT_ID_HEADER = "x-razorpay-event-id"


def _verify_signature(secret: str, raw_body: bytes, received_signature: str) -> bool:
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, received_signature.strip().lower())


async def _store_event(event_uid: str, body: dict[str, Any]) -> RawEvent | None:
    meta = extract_event_meta(body)
    async with session_factory() as session:
        stmt = (
            pg_insert(RawEvent)
            .values(
                id=uuid4(),
                event_uid=event_uid,
                event_type=meta.event_type,
                entity_type=meta.primary_entity_type,
                entity_id=meta.primary_entity_id,
                entity_ids=meta.entity_ids,
                payload=body,
                source=get_settings().razorpay_source,
            )
            .on_conflict_do_nothing(index_elements=["event_uid"])
            .returning(RawEvent.id)
        )
        result = await session.execute(stmt)
        row = result.first()
        await session.commit()
        if row is None:
            return None
        return RawEvent(
            id=row[0],
            event_uid=event_uid,
            event_type=meta.event_type,
            entity_type=meta.primary_entity_type,
            entity_id=meta.primary_entity_id,
            entity_ids=meta.entity_ids,
            payload=body,
        )


@router.post("/webhooks/razorpay")
async def ingest_razorpay_webhook(request: Request) -> dict[str, str]:
    settings = get_settings()
    if not settings.webhook_secret_configured:
        raise HTTPException(status_code=503, detail="webhook secret not configured; refusing to accept events")

    raw_body = await request.body()
    received_signature = request.headers.get(SIGNATURE_HEADER, "")
    if not received_signature or not _verify_signature(settings.razorpay_webhook_secret, raw_body, received_signature):
        logger.warning("webhook rejected: invalid or missing signature")
        raise HTTPException(status_code=400, detail="invalid signature")

    try:
        body = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="invalid json after valid signature") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="invalid payload shape")

    header_event_id = request.headers.get(EVENT_ID_HEADER)
    event_uid = f"evt-header:{header_event_id}" if header_event_id else f"evt-hash:{canonical_hash(body)}"

    stored = await _store_event(event_uid, body)
    if stored is None:
        return {"status": "duplicate"}

    await bus.publish(
        StreamEvent(
            kind="razorpay.event",
            data={
                "event_uid": stored.event_uid,
                "event_type": stored.event_type,
                "entity_type": stored.entity_type,
                "entity_id": stored.entity_id,
                "payload": body,
            },
        )
    )
    return {"status": "accepted"}
