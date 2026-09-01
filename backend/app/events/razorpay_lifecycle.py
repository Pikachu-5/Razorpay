from __future__ import annotations

import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.database.models import (
    DecisionAudit,
    Incident,
    InterventionRecord,
    InvoiceState,
    Opportunity,
    Payment,
    PaymentDowntime,
    PaymentLinkState,
    RazorpayOrder,
    RevenueAdjustment,
    SubscriptionState,
)
from app.database.session import session_factory
from app.events.bus import StreamEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _time(value: Any) -> datetime | None:
    if value in (None, 0, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return None


def _entity(payload: dict[str, Any], name: str) -> dict[str, Any] | None:
    wrapper = payload.get(name)
    entity = wrapper.get("entity") if isinstance(wrapper, dict) else None
    return entity if isinstance(entity, dict) else None


async def process_order_event(payload: dict[str, Any], source: str) -> list[StreamEvent]:
    order = _entity(payload, "order")
    payment = _entity(payload, "payment")
    if not order or not order.get("id"):
        return []
    order_id = str(order["id"])
    async with session_factory() as session:
        row = await session.get(RazorpayOrder, order_id)
        if row is None:
            row = RazorpayOrder(id=order_id, status=str(order.get("status") or "unknown"))
            session.add(row)
        row.status = str(order.get("status") or row.status)
        row.amount_minor = int(order.get("amount") or 0)
        row.amount_paid_minor = int(order.get("amount_paid") or 0)
        row.amount_due_minor = int(order.get("amount_due") or 0)
        row.currency = str(order.get("currency") or "INR")
        row.attempts = int(order.get("attempts") or 0)
        row.receipt = order.get("receipt")
        row.source = source
        row.is_synthetic = source == "simulation"
        row.payload = order
        row.occurred_at = _time(order.get("created_at")) or _utcnow()
        await session.commit()

    emitted = [StreamEvent(kind="order.updated", data={
        "order_id": order_id, "status": order.get("status"), "attempts": order.get("attempts", 0),
    })]
    if payment:
        payment = {**payment, "_source": source}
        from app.events.processor import process_payment_event

        emitted.extend(await process_payment_event("payment.captured", payment))

    if order.get("status") == "paid":
        async with session_factory() as session:
            opportunities = (await session.execute(
                select(Opportunity)
                .join(Payment, Opportunity.payment_id == Payment.id)
                .where(Payment.order_id == order_id)
                .where(Opportunity.status.in_((
                    "open", "decision_in_progress", "control_holdout", "shadow_observation",
                    "closed_not_viable", "closed_expired", "closed_no_response", "escalated",
                )))
            )).scalars().all()
            for opportunity in opportunities:
                opportunity.status = "recovered_natural"
                opportunity.closed_reason = "Razorpay order paid through a payment attempt"
            await session.commit()
        for opportunity in opportunities:
            emitted.append(StreamEvent(kind="opportunity.resolved", data={
                "opportunity_id": str(opportunity.id), "resolution": "recovered_natural",
                "amount_minor": opportunity.amount_minor, "order_id": order_id,
            }))
    return emitted


async def process_payment_link_event(
    event_type: str, payload: dict[str, Any], source: str
) -> list[StreamEvent]:
    link = _entity(payload, "payment_link")
    payment = _entity(payload, "payment")
    if not link or not link.get("id"):
        return []
    link_id = str(link["id"])
    reference_id = link.get("reference_id")
    opportunity_id = None
    async with session_factory() as session:
        intervention = (await session.execute(
            select(InterventionRecord)
            .where(InterventionRecord.razorpay_reference == link_id)
            .order_by(InterventionRecord.created_at.desc()).limit(1)
        )).scalar_one_or_none()
        if intervention:
            opportunity_id = intervention.opportunity_id
        elif isinstance(reference_id, str) and reference_id.startswith("opp_"):
            try:
                opportunity_id = uuid_lib.UUID(reference_id.removeprefix("opp_"))
            except ValueError:
                pass
        state = await session.get(PaymentLinkState, link_id)
        if state is None:
            state = PaymentLinkState(id=link_id, status=str(link.get("status") or "unknown"))
            session.add(state)
        state.opportunity_id = opportunity_id
        state.status = str(link.get("status") or event_type.rsplit(".", 1)[-1])
        state.amount_minor = int(link.get("amount") or 0)
        state.amount_paid_minor = int(link.get("amount_paid") or 0)
        state.reference_id = reference_id
        state.short_url = link.get("short_url")
        reminders = link.get("reminders") or {}
        state.reminder_status = reminders.get("status") if isinstance(reminders, dict) else None
        state.expire_by = _time(link.get("expire_by"))
        state.source = source
        state.payload = link
        await session.commit()

    if event_type == "payment_link.paid":
        from app.agents.verification import verify_payment_link_paid

        outcome = await verify_payment_link_paid(link_id, payment or {})
        return [outcome] if outcome else [StreamEvent(kind="payment_link.updated", data={
            "link_id": link_id, "status": "paid", "matched": False,
        })]
    if event_type == "payment_link.partially_paid":
        return [StreamEvent(kind="payment_link.partial", data={
            "link_id": link_id, "amount_minor": int(link.get("amount") or 0),
            "amount_paid_minor": int(link.get("amount_paid") or 0),
            "amount_due_minor": max(0, int(link.get("amount") or 0) - int(link.get("amount_paid") or 0)),
        })]
    if opportunity_id and event_type in {"payment_link.expired", "payment_link.cancelled"}:
        async with session_factory() as session:
            opportunity = await session.get(Opportunity, opportunity_id)
            if opportunity and opportunity.status == "intervention_pending":
                opportunity.status = "closed_no_response"
                opportunity.closed_reason = f"Razorpay Payment Link {event_type.rsplit('.', 1)[-1]}"
                audit = (await session.execute(
                    select(DecisionAudit).where(DecisionAudit.opportunity_id == opportunity_id)
                    .order_by(DecisionAudit.created_at.desc()).limit(1)
                )).scalar_one_or_none()
                if audit and audit.verified_outcome == "pending":
                    audit.verified_outcome = event_type.rsplit(".", 1)[-1]
                await session.commit()
    return [StreamEvent(kind="payment_link.updated", data={
        "link_id": link_id, "status": event_type.rsplit(".", 1)[-1],
        "opportunity_id": str(opportunity_id) if opportunity_id else None,
    })]


async def process_downtime_event(
    event_type: str, payload: dict[str, Any], source: str
) -> list[StreamEvent]:
    entity = _entity(payload, "payment.downtime") or _entity(payload, "payment_downtime")
    if not entity or not entity.get("id"):
        return []
    downtime_id = str(entity["id"])
    status = str(entity.get("status") or event_type.rsplit(".", 1)[-1])
    async with session_factory() as session:
        row = await session.get(PaymentDowntime, downtime_id)
        if row is None:
            row = PaymentDowntime(id=downtime_id, status=status)
            session.add(row)
        row.status = status
        row.method = entity.get("method")
        row.severity = str(entity.get("severity") or "medium")
        row.scheduled = bool(entity.get("scheduled", False))
        row.instrument = entity.get("instrument")
        row.begin_at = _time(entity.get("begin"))
        row.end_at = _time(entity.get("end"))
        row.source = source
        row.payload = entity

        incident = (await session.execute(
            select(Incident).where(
                Incident.source == "razorpay_downtime",
                Incident.status.in_(("detected", "responding")),
                Incident.title.contains(downtime_id),
            ).limit(1)
        )).scalar_one_or_none()
        if status in {"started", "updated", "scheduled"} and incident is None:
            incident = Incident(
                id=uuid_lib.uuid4(), status="detected", method=row.method,
                bank=(row.instrument or {}).get("issuer") or (row.instrument or {}).get("bank"),
                title=f"Razorpay downtime {downtime_id}", severity=row.severity,
                diagnosis={"downtime_id": downtime_id, "official_signal": True, "instrument": row.instrument},
                source="razorpay_downtime", intervention_budget=25,
            )
            session.add(incident)
        elif status == "resolved" and incident:
            incident.status = "resolved"
            incident.resolved_at = _utcnow()
        await session.commit()
    return [StreamEvent(kind=f"downtime.{status}", data={
        "downtime_id": downtime_id, "method": entity.get("method"),
        "severity": entity.get("severity"), "instrument": entity.get("instrument"),
    })]


async def active_downtime_methods() -> set[str]:
    async with session_factory() as session:
        return set((await session.execute(
            select(PaymentDowntime.method).where(
                PaymentDowntime.status.in_(("started", "updated")),
                PaymentDowntime.method.is_not(None),
            )
        )).scalars().all())


async def cancel_payment_links_for_opportunities(opportunity_ids: list[uuid_lib.UUID]) -> int:
    """Cancel stale recovery links after the original order/payment succeeds."""
    from app.core.config import get_settings

    if get_settings().shadow_mode or not opportunity_ids:
        return 0
    async with session_factory() as session:
        references = list((await session.execute(
            select(InterventionRecord.razorpay_reference)
            .where(InterventionRecord.opportunity_id.in_(opportunity_ids))
            .where(InterventionRecord.status == "executed")
            .where(InterventionRecord.razorpay_reference.like("plink_%"))
        )).scalars().all())
    if not references:
        return 0
    from app.integrations.razorpay.client import get_client

    client = await get_client()
    cancelled = 0
    for link_id in references:
        try:
            link = await client.cancel_payment_link(str(link_id))
            await process_payment_link_event(
                "payment_link.cancelled", {"payment_link": {"entity": link}},
                get_settings().razorpay_source,
            )
            cancelled += 1
        except Exception:
            # Reconciliation will retry status repair; payment recovery itself
            # must never be rolled back because link cancellation failed.
            continue
    return cancelled


async def process_subscription_event(
    event_type: str, payload: dict[str, Any], source: str
) -> list[StreamEvent]:
    entity = _entity(payload, "subscription")
    payment = _entity(payload, "payment")
    if not entity or not entity.get("id"):
        return []
    subscription_id = str(entity["id"])
    status = str(entity.get("status") or event_type.rsplit(".", 1)[-1])
    async with session_factory() as session:
        row = await session.get(SubscriptionState, subscription_id)
        if row is None:
            row = SubscriptionState(id=subscription_id, status=status)
            session.add(row)
        row.status = status
        row.plan_id = entity.get("plan_id")
        row.customer_id = entity.get("customer_id")
        row.paid_count = int(entity.get("paid_count") or 0)
        row.remaining_count = int(entity.get("remaining_count") or 0)
        row.current_start = _time(entity.get("current_start"))
        row.current_end = _time(entity.get("current_end"))
        row.source = source
        row.payload = entity
        await session.commit()

    emitted = [StreamEvent(kind=f"subscription.{status}", data={
        "subscription_id": subscription_id, "status": status,
        "native_retry_active": status == "pending",
    })]
    if payment and payment.get("status") in {"failed", "captured", "authorized"}:
        from app.events.processor import process_payment_event

        payment = {**payment, "subscription_id": subscription_id, "_source": source}
        emitted.extend(await process_payment_event(f"payment.{payment['status']}", payment))
    if status in {"active", "activated", "charged"}:
        async with session_factory() as session:
            opportunities = (await session.execute(
                select(Opportunity).join(Payment, Opportunity.payment_id == Payment.id)
                .where(Payment.subscription_id == subscription_id)
                .where(Opportunity.status.in_(("open", "control_holdout", "shadow_observation", "escalated")))
            )).scalars().all()
            for opportunity in opportunities:
                opportunity.status = "recovered_natural"
                opportunity.closed_reason = "subscription recovered through Razorpay native lifecycle"
            await session.commit()
    return emitted


async def process_invoice_event(
    event_type: str, payload: dict[str, Any], source: str
) -> list[StreamEvent]:
    entity = _entity(payload, "invoice")
    payment = _entity(payload, "payment")
    if not entity or not entity.get("id"):
        return []
    invoice_id = str(entity["id"])
    status = str(entity.get("status") or event_type.rsplit(".", 1)[-1])
    subscription_id = entity.get("subscription_id")
    async with session_factory() as session:
        row = await session.get(InvoiceState, invoice_id)
        if row is None:
            row = InvoiceState(id=invoice_id, status=status)
            session.add(row)
        row.subscription_id = subscription_id
        row.payment_id = entity.get("payment_id") or (payment or {}).get("id")
        row.status = status
        row.amount_minor = int(entity.get("amount") or entity.get("gross_amount") or 0)
        row.amount_paid_minor = int(entity.get("amount_paid") or 0)
        row.amount_due_minor = int(entity.get("amount_due") or 0)
        row.source = source
        row.payload = entity
        await session.commit()
    emitted = [StreamEvent(kind=f"invoice.{status}", data={
        "invoice_id": invoice_id, "subscription_id": subscription_id,
        "amount_paid_minor": int(entity.get("amount_paid") or 0),
        "amount_due_minor": int(entity.get("amount_due") or 0),
    })]
    if payment and payment.get("status") in {"captured", "authorized"}:
        from app.events.processor import process_payment_event

        emitted.extend(await process_payment_event(
            f"payment.{payment['status']}",
            {**payment, "invoice_id": invoice_id, "subscription_id": subscription_id, "_source": source},
        ))
    if status == "paid" and subscription_id:
        async with session_factory() as session:
            opportunities = (await session.execute(
                select(Opportunity).join(Payment, Opportunity.payment_id == Payment.id)
                .where(Payment.subscription_id == str(subscription_id))
                .where(Opportunity.status.in_((
                    "open", "native_retry_pending", "control_holdout", "shadow_observation", "escalated",
                )))
            )).scalars().all()
            for opportunity in opportunities:
                opportunity.status = "recovered_natural"
                opportunity.closed_reason = f"subscription invoice {invoice_id} paid"
            await session.commit()
    return emitted


async def process_revenue_adjustment(
    event_type: str, payload: dict[str, Any], source: str
) -> list[StreamEvent]:
    kind = "refund" if event_type.startswith("refund.") else "dispute"
    entity = _entity(payload, kind)
    if not entity or not entity.get("id"):
        return []
    external_id = str(entity["id"])
    async with session_factory() as session:
        row = (await session.execute(
            select(RevenueAdjustment).where(RevenueAdjustment.external_id == external_id)
        )).scalar_one_or_none()
        if row is None:
            row = RevenueAdjustment(id=uuid_lib.uuid4(), external_id=external_id, kind=kind, status="created")
            session.add(row)
        row.razorpay_payment_id = entity.get("payment_id")
        row.kind = kind
        row.status = str(entity.get("status") or event_type.rsplit(".", 1)[-1])
        row.amount_minor = int(entity.get("amount") or 0)
        row.source = source
        row.payload = entity
        row.occurred_at = _time(entity.get("created_at")) or _utcnow()
        await session.commit()
    return [StreamEvent(kind=f"revenue.{kind}.{row.status}", data={
        "external_id": external_id, "payment_id": row.razorpay_payment_id,
        "amount_minor": row.amount_minor, "status": row.status,
    })]
