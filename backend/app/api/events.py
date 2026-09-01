from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import RawEvent
from app.database.session import get_session

router = APIRouter(prefix="/api/events", tags=["events"])


@router.get("/recent")
async def recent_events(limit: int = 50, session: AsyncSession = Depends(get_session)) -> list[dict]:
    limit = max(1, min(limit, 200))
    result = await session.execute(
        select(RawEvent).order_by(RawEvent.received_at.desc()).limit(limit)
    )
    rows = result.scalars().all()
    return [
        {
            "event_uid": row.event_uid,
            "event_type": row.event_type,
            "entity_type": row.entity_type,
            "entity_id": row.entity_id,
            "received_at": row.received_at.isoformat(),
            "source": row.source,
            "processed_at": row.processed_at.isoformat() if row.processed_at else None,
            "attempts": row.attempts,
            "last_error": row.last_error,
        }
        for row in rows
    ]
