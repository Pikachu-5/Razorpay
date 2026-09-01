import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.database.models import DurableEvent
from app.database.session import session_factory


@dataclass(frozen=True)
class StreamEvent:
    kind: str
    data: dict[str, Any] = field(default_factory=dict)
    ts: str = ""

    def __post_init__(self) -> None:
        if not self.ts:
            object.__setattr__(self, "ts", datetime.now(timezone.utc).isoformat())

    def envelope(self) -> dict[str, Any]:
        return {"kind": self.kind, "data": self.data, "ts": self.ts}


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[StreamEvent]] = set()

    def subscribe(self) -> asyncio.Queue[StreamEvent]:
        queue: asyncio.Queue[StreamEvent] = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[StreamEvent]) -> None:
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    async def publish(self, event: StreamEvent) -> int:
        # Persist first so another API replica and reconnecting SSE clients see it.
        async with session_factory() as session:
            session.add(DurableEvent(kind=event.kind, data=event.envelope()))
            await session.commit()
        delivered = 0
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
                delivered += 1
            except asyncio.QueueFull:
                pass
        return delivered


bus = EventBus()
