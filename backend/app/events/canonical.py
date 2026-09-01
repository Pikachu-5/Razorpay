import hashlib
import json
from dataclasses import dataclass, field


def canonical_hash(body: dict) -> str:
    serialized = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventMeta:
    event_type: str | None
    primary_entity_type: str | None
    primary_entity_id: str | None
    entity_ids: list[str] = field(default_factory=list)


def extract_event_meta(body: dict) -> EventMeta:
    event_type = body.get("event")
    payload = body.get("payload") or {}
    entity_ids: list[str] = []
    primary_type: str | None = None
    primary_id: str | None = None
    for entity_type, wrapper in payload.items():
        if not isinstance(wrapper, dict):
            continue
        entity = wrapper.get("entity")
        if not isinstance(entity, dict):
            continue
        entity_id = entity.get("id")
        if not isinstance(entity_id, str):
            continue
        entity_ids.append(f"{entity_type}:{entity_id}")
        if primary_id is None:
            primary_type = entity_type
            primary_id = entity_id
    return EventMeta(
        event_type=event_type,
        primary_entity_type=primary_type,
        primary_entity_id=primary_id,
        entity_ids=entity_ids,
    )
