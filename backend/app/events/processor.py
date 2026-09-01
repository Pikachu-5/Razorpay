import asyncio
import contextlib
import hashlib
import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.agents.orchestrator import decide_opportunity
from app.agents.verification import verify_payment_link_paid
from app.database.models import Customer, DecisionAudit, Opportunity, Payment, RawEvent
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus

logger = logging.getLogger("processor")

TREATMENT_SHARE_PCT = 80
CONTACT_WINDOW_HOURS = 48
CLAIM_TIMEOUT_SECONDS = 300

PAYMENT_EVENTS = {
    "payment.failed": "failed",
    "payment.captured": "captured",
    "payment.authorized": "authorized",
    "payment.pending": "pending",
}
SUCCESS_STATES = {"captured", "authorized"}
OPEN_OPPORTUNITY_STATUSES = {"open", "decision_in_progress", "intervention_pending", "native_retry_pending"}
NATURAL_RECOVERY_STATUSES = OPEN_OPPORTUNITY_STATUSES | {
    "control_holdout",
    "closed_not_viable",
    "closed_no_response",
    "closed_expired",
    "escalated",
    "shadow_observation",
}
TRACKED_OPPORTUNITY_STATUSES = OPEN_OPPORTUNITY_STATUSES | {"control_holdout"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _entity_time(entity: dict[str, Any]) -> datetime:
    ts = entity.get("created_at")
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    except (TypeError, ValueError):
        return _utcnow()


def assign_experiment_group(assignment_key: str) -> str:
    bucket = int(hashlib.sha256(assignment_key.encode("utf-8")).hexdigest(), 16) % 100
    return "treatment" if bucket < TREATMENT_SHARE_PCT else "control"


@dataclass(frozen=True)
class CustomerIdentity:
    identity_key: str | None
    email: str | None
    contact: str | None
    name: str | None


def extract_customer_identity(entity: dict[str, Any]) -> CustomerIdentity:
    email = entity.get("email") or None
    contact = entity.get("contact") or None
    name = entity.get("name") or None
    if email:
        key = f"email:{email.strip().lower()}"
    elif contact:
        key = f"contact:{contact}"
    else:
        key = None
    return CustomerIdentity(identity_key=key, email=email, contact=contact, name=name)


async def _resolve_customer(session: Any, identity: CustomerIdentity) -> Any:
    if identity.identity_key is None:
        return None
    stmt = (
        pg_insert(Customer)
        .values(
            id=uuid_lib.uuid4(),
            identity_key=identity.identity_key,
            email=identity.email,
            contact=identity.contact,
            name=identity.name,
        )
        .on_conflict_do_nothing(index_elements=["identity_key"])
        .returning(Customer.id)
    )
    result = await session.execute(stmt)
    row = result.first()
    if row is not None:
        return row[0]
    existing = await session.execute(
        select(Customer.id).where(Customer.identity_key == identity.identity_key)
    )
    return existing.scalar_one_or_none()


def _extract_subscription_id(entity: dict[str, Any]) -> str | None:
    subscription_id = entity.get("subscription_id")
    return str(subscription_id) if subscription_id else None


async def process_payment_event(event_type: str, entity: dict[str, Any]) -> list[StreamEvent]:
    razorpay_payment_id = entity.get("id")
    if not razorpay_payment_id:
        logger.warning("%s without entity id", event_type)
        return []

    from app.core.config import get_settings

    new_status = PAYMENT_EVENTS[event_type]
    source = str(entity.get("_source") or get_settings().razorpay_source)
    is_synthetic = bool(entity.get("_synthetic", False))
    simulation_run_id = entity.get("_simulation_run_id")
    emitted: list[StreamEvent] = []
    resolved_opportunity_ids: list[UUID] = []

    async with session_factory() as session:
        identity = extract_customer_identity(entity)
        customer_id = await _resolve_customer(session, identity)

        values = dict(
            customer_id=customer_id,
            amount_minor=int(entity.get("amount") or 0),
            currency=entity.get("currency") or "INR",
            status=new_status,
            method=entity.get("method"),
            bank=entity.get("bank"),
            vpa=entity.get("vpa"),
            card_id=entity.get("card_id"),
            order_id=entity.get("order_id"),
            invoice_id=entity.get("invoice_id"),
            subscription_id=_extract_subscription_id(entity),
            source=source,
            is_synthetic=is_synthetic,
            simulation_run_id=str(simulation_run_id) if simulation_run_id else None,
            error_code=entity.get("error_code"),
            error_description=entity.get("error_description"),
            error_source=entity.get("error_source"),
            error_step=entity.get("error_step"),
            error_reason=entity.get("error_reason"),
            occurred_at=_entity_time(entity),
        )

        existing = await session.execute(
            select(Payment).where(Payment.razorpay_payment_id == razorpay_payment_id)
        )
        payment = existing.scalar_one_or_none()

        if payment is None:
            payment = Payment(
                id=uuid_lib.uuid4(), razorpay_payment_id=razorpay_payment_id, **values
            )
            session.add(payment)
            await session.flush()
            previous_status: str | None = None
            emitted.append(
                StreamEvent(
                    kind="payment.recorded",
                    data={
                        "razorpay_payment_id": razorpay_payment_id,
                        "status": new_status,
                        "amount_minor": values["amount_minor"],
                        "method": values["method"],
                        "bank": values["bank"],
                    },
                )
            )
        else:
            previous_status = payment.status
            if event_type == "payment.failed" and previous_status in SUCCESS_STATES:
                emitted.append(
                    StreamEvent(
                        kind="opportunity.skipped",
                        data={
                            "razorpay_payment_id": razorpay_payment_id,
                            "reason": f"payment already {previous_status}; late failure delivery ignored",
                        },
                    )
                )
                await session.commit()
                return emitted
            if previous_status == "captured" and new_status == "authorized":
                return emitted
            for field_name, value in values.items():
                # A success event carries no error fields, and blindly copying
                # them over erased why the payment failed in the first place.
                # That history is the whole basis of failure-class reporting and
                # of any later training export: a recovered payment still failed
                # for a reason, and we need to know which one.
                if field_name.startswith("error_") and value is None:
                    continue
                setattr(payment, field_name, value)
            await session.flush()
            if previous_status != new_status:
                emitted.append(
                    StreamEvent(
                        kind="payment.updated",
                        data={
                            "razorpay_payment_id": razorpay_payment_id,
                            "previous_status": previous_status,
                            "status": new_status,
                            "amount_minor": values["amount_minor"],
                        },
                    )
                )

        if event_type == "payment.failed":
            open_query = select(Opportunity).where(
                Opportunity.status.in_(NATURAL_RECOVERY_STATUSES),
                Opportunity.window_ends_at >= _utcnow(),
            )
            if payment.order_id:
                open_query = open_query.join(Payment, Opportunity.payment_id == Payment.id).where(
                    Payment.order_id == payment.order_id
                )
            else:
                open_query = open_query.where(Opportunity.payment_id == payment.id)
            open_opp = (await session.execute(open_query.limit(1))).scalar_one_or_none()
            if open_opp is None:
                opportunity = Opportunity(
                    id=uuid_lib.uuid4(),
                    payment_id=payment.id,
                    status="open",
                    category="failed_payment",
                    amount_minor=payment.amount_minor,
                    assignment_key=(identity.identity_key or payment.order_id or razorpay_payment_id),
                    assignment_probability=TREATMENT_SHARE_PCT / 100.0,
                    experiment_group=assign_experiment_group(
                        identity.identity_key or payment.order_id or razorpay_payment_id
                    ),
                    source=source,
                    is_synthetic=is_synthetic,
                    simulation_run_id=str(simulation_run_id) if simulation_run_id else None,
                    window_ends_at=_utcnow() + timedelta(hours=CONTACT_WINDOW_HOURS),
                )
                session.add(opportunity)
                await session.flush()
                emitted.append(
                    StreamEvent(
                        kind="opportunity.created",
                        data={
                            "opportunity_id": str(opportunity.id),
                            "razorpay_payment_id": razorpay_payment_id,
                            "amount_minor": opportunity.amount_minor,
                            "experiment_group": opportunity.experiment_group,
                        },
                    )
                )

        elif event_type in {"payment.captured", "payment.authorized"}:
            open_opps = (
                await session.execute(
                    select(Opportunity).where(
                        Opportunity.payment_id == payment.id,
                        Opportunity.status.in_(NATURAL_RECOVERY_STATUSES),
                    )
                )
            ).scalars().all()
            for opp in open_opps:
                opp.status = "recovered_natural"
                opp.closed_reason = (
                    "payment succeeded without intervention "
                    "(late authorization or native retry)"
                )
                latest_audit = (
                    await session.execute(
                        select(DecisionAudit)
                        .where(DecisionAudit.opportunity_id == opp.id)
                        .order_by(DecisionAudit.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if latest_audit is not None:
                    latest_audit.verified_outcome = "recovered_natural"
                    latest_audit.recovered_amount_minor = opp.amount_minor
                emitted.append(
                    StreamEvent(
                        kind="opportunity.resolved",
                        data={
                            "opportunity_id": str(opp.id),
                            "razorpay_payment_id": razorpay_payment_id,
                            "resolution": "recovered_natural",
                            "amount_minor": opp.amount_minor,
                        },
                    )
                )
                resolved_opportunity_ids.append(opp.id)

        await session.commit()

    if resolved_opportunity_ids:
        from app.events.razorpay_lifecycle import cancel_payment_links_for_opportunities

        await cancel_payment_links_for_opportunities(resolved_opportunity_ids)

    from app.agents.detection import update_online_detectors

    await update_online_detectors(
        entity.get("method"),
        entity.get("bank"),
        event_type == "payment.failed",
    )
    return emitted


async def process_raw_event(event_uid: str, body: dict[str, Any]) -> list[StreamEvent]:
    from app.core.config import get_settings
    from app.events.razorpay_lifecycle import (
        process_downtime_event,
        process_invoice_event,
        process_order_event,
        process_payment_link_event,
        process_revenue_adjustment,
        process_subscription_event,
    )

    event_type = body.get("event")
    payload = body.get("payload") or {}
    source = str(body.get("_source") or get_settings().razorpay_source)

    def observed() -> list[StreamEvent]:
        return [StreamEvent(kind="event.observed", data={"event_uid": event_uid, "event_type": event_type})]
    if event_type in PAYMENT_EVENTS:
        wrapper = payload.get("payment")
        entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
        if not isinstance(entity, dict):
            logger.warning("%s without payment entity", event_type)
            return []
        return await process_payment_event(event_type, {**entity, "_source": source})
    if event_type == "order.paid":
        return await process_order_event(payload, source) or observed()
    if isinstance(event_type, str) and event_type.startswith("payment_link."):
        return await process_payment_link_event(event_type, payload, source) or observed()
    if isinstance(event_type, str) and event_type.startswith("payment.downtime."):
        return await process_downtime_event(event_type, payload, source) or observed()
    if isinstance(event_type, str) and event_type.startswith("subscription."):
        return await process_subscription_event(event_type, payload, source) or observed()
    if isinstance(event_type, str) and event_type.startswith("invoice."):
        return await process_invoice_event(event_type, payload, source) or observed()
    if isinstance(event_type, str) and (
        event_type.startswith("refund.") or event_type.startswith("payment.dispute.")
    ):
        return await process_revenue_adjustment(event_type, payload, source) or observed()
    return observed()


async def process_payment_link_paid(payload: dict[str, Any]) -> list[StreamEvent]:
    link_wrapper = payload.get("payment_link")
    payment_wrapper = payload.get("payment")
    link_entity = link_wrapper.get("entity") if isinstance(link_wrapper, dict) else None
    payment_entity = payment_wrapper.get("entity") if isinstance(payment_wrapper, dict) else None
    link_id = str((link_entity or {}).get("id") or "")
    if not link_id:
        logger.warning("payment_link.paid without link id")
        return []
    outcome = await verify_payment_link_paid(link_id, payment_entity or {})
    if outcome is not None:
        return [outcome]
    return [
        StreamEvent(
            kind="event.observed",
            data={"link_id": link_id, "note": "no matching open intervention"},
        )
    ]


async def handle_stream_event(stream_event: StreamEvent) -> list[StreamEvent]:
    if stream_event.kind != "razorpay.event":
        return []
    body = stream_event.data.get("payload")
    event_uid = stream_event.data.get("event_uid")
    if not isinstance(body, dict) or not isinstance(event_uid, str):
        logger.warning("razorpay.event without payload or event_uid; dropped")
        return []
    outcomes = await process_raw_event(event_uid, body)
    for outcome in outcomes:
        await bus.publish(outcome)

    for outcome in outcomes:
        if outcome.kind == "opportunity.created":
            opportunity_id = outcome.data.get("opportunity_id")
            if opportunity_id:
                await decide_opportunity(UUID(opportunity_id), trigger="payment.failed.webhook")
    return outcomes


async def run_consumer() -> None:
    """Consume durable webhook rows with Postgres row claims across replicas."""
    logger.info("durable event processor consumer started")
    while True:
        events = await claim_raw_events()
        if not events:
            await asyncio.sleep(0.5)
            continue
        for event_id, event_uid, payload in events:
            try:
                await handle_stream_event(StreamEvent(
                    kind="razorpay.event",
                    data={"event_uid": event_uid, "payload": payload},
                ))
            except Exception as exc:
                logger.exception("failed processing %s", event_uid)
                await release_raw_event(event_id, str(exc))
            else:
                await mark_raw_event_processed(event_id)


async def claim_raw_events(limit: int = 25) -> list[tuple[UUID, str, dict[str, Any]]]:
    """Claim unprocessed webhook rows without double-consuming them across replicas."""
    expired_claim = _utcnow() - timedelta(seconds=CLAIM_TIMEOUT_SECONDS)
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(RawEvent)
                .where(RawEvent.processed_at.is_(None))
                .where(or_(RawEvent.claimed_at.is_(None), RawEvent.claimed_at < expired_claim))
                .order_by(RawEvent.received_at.asc())
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).scalars().all()
        now = _utcnow()
        for row in rows:
            row.claimed_at = now
            row.attempts += 1
        await session.commit()
        return [(row.id, row.event_uid, row.payload) for row in rows]


async def mark_raw_event_processed(event_id: UUID) -> None:
    async with session_factory() as session:
        row = await session.get(RawEvent, event_id)
        if row:
            row.processed_at = _utcnow()
            row.claimed_at = None
            row.last_error = None
            await session.commit()


async def release_raw_event(event_id: UUID, error: str) -> None:
    async with session_factory() as session:
        row = await session.get(RawEvent, event_id)
        if row:
            row.claimed_at = None
            row.last_error = error[:2000]
            await session.commit()


async def sweep_expired_opportunities() -> int:
    from app.agents.verification import mark_no_response

    async with session_factory() as session:
        stale = (
            await session.execute(
                select(Opportunity).where(
                    Opportunity.status.in_(TRACKED_OPPORTUNITY_STATUSES),
                    Opportunity.window_ends_at < _utcnow(),
                )
            )
        ).scalars().all()
        expired_ids: list[tuple[str, str]] = []
        for opp in stale:
            if opp.status == "intervention_pending":
                await mark_no_response(opp.id, "contact window elapsed without payment")
                opp.status = "closed_no_response"
            elif opp.status == "control_holdout":
                opp.status = "closed_no_response"
                opp.closed_reason = "control holdout window elapsed without natural recovery"
            else:
                opp.status = "closed_expired"
            opp.closed_reason = opp.closed_reason or "contact window elapsed"
            expired_ids.append((str(opp.id), opp.status))
        await session.commit()

    for opp_id, status in expired_ids:
        await bus.publish(StreamEvent(kind="opportunity.expired", data={
            "opportunity_id": opp_id, "final_status": status,
        }))
    return len(expired_ids)


async def run_sweeper(interval_seconds: int = 900) -> None:
    from app.core.locks import run_with_singleton_lock

    while True:
        try:
            closed = await run_with_singleton_lock("recovery-opportunity-sweeper", sweep_expired_opportunities)
            if closed:
                logger.info("sweeper closed %d expired opportunities", closed)
        except Exception:
            logger.exception("sweeper iteration failed")
        await asyncio.sleep(interval_seconds)


def start_consumer_task() -> asyncio.Task:
    return asyncio.create_task(run_consumer())


def start_sweeper_task(interval_seconds: int = 900) -> asyncio.Task:
    return asyncio.create_task(run_sweeper(interval_seconds))


async def stop_consumer_task(task: asyncio.Task) -> None:
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
