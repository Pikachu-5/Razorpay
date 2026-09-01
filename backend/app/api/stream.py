import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header
from sqlalchemy import func, select
from sse_starlette.sse import EventSourceResponse

from app.database.models import DurableEvent
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus

router = APIRouter(tags=["stream"])

PING_INTERVAL_SECONDS = 15


async def event_frames(queue: asyncio.Queue[StreamEvent] | None = None):
    if queue is None:
        async for frame in durable_event_frames():
            yield frame
        return
    try:
        yield {"event": "connected", "data": "{}"}
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=PING_INTERVAL_SECONDS)
                yield {
                    "event": evt.kind.replace(".", "_"),
                    "data": json.dumps(evt.envelope(), default=str),
                }
            except asyncio.TimeoutError:
                yield {"comment": "ping"}
    finally:
        bus.unsubscribe(queue)


async def durable_event_frames(last_id: int | None = None):
    if last_id is None:
        async with session_factory() as session:
            last_id = int(
                (await session.execute(select(func.coalesce(func.max(DurableEvent.id), 0)))).scalar_one()
            )
    yield {"event": "connected", "data": "{}"}
    while True:
        async with session_factory() as session:
            rows = (
                await session.execute(
                    select(DurableEvent)
                    .where(DurableEvent.id > last_id)
                    .order_by(DurableEvent.id.asc())
                    .limit(100)
                )
            ).scalars().all()
        if rows:
            for event in rows:
                last_id = event.id
                payload = event.data
                yield {
                    "id": str(event.id),
                    "event": event.kind.replace(".", "_"),
                    "data": json.dumps(payload, default=str),
                }
        else:
            await asyncio.sleep(1)
            yield {"comment": f"ping {datetime.now(timezone.utc).isoformat()}"}


@router.get("/api/stream")
async def stream(
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    parsed_last_id = int(last_event_id) if last_event_id and last_event_id.isdigit() else None
    return EventSourceResponse(
        durable_event_frames(parsed_last_id),
        ping=PING_INTERVAL_SECONDS,
    )
