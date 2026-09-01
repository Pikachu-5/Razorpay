from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select

from app.database.models import DecisionAudit, Opportunity
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus
from app.execution.executor import find_open_intervention_by_reference

logger = logging.getLogger("verification")


async def _latest_audit(session, opportunity_id) -> DecisionAudit | None:
    result = await session.execute(
        select(DecisionAudit)
        .where(DecisionAudit.opportunity_id == opportunity_id)
        .order_by(DecisionAudit.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def verify_payment_link_paid(
    link_id: str, payment_entity: dict[str, Any]
) -> StreamEvent | None:
    record = await find_open_intervention_by_reference(link_id)
    if record is None:
        logger.info("payment_link.paid for unknown/unmatched link %s", link_id)
        return None

    amount_minor = int(payment_entity.get("amount") or 0)
    recovered_payment_id = payment_entity.get("id")

    async with session_factory() as session:
        opp = (
            await session.execute(select(Opportunity).where(Opportunity.id == record.opportunity_id))
        ).scalar_one_or_none()
        if opp is None or opp.status not in {"intervention_pending", "escalated"}:
            return None

        opp.status = "recovered_intervention"
        opp.closed_reason = f"recovery payment link {link_id} paid"

        audit = await _latest_audit(session, opp.id)
        if audit is not None:
            audit.verified_outcome = "verified_recovered"
            audit.recovered_amount_minor = amount_minor
            exec_result = dict(audit.execution_result or {})
            exec_result["verified_payment_id"] = recovered_payment_id
            exec_result["verified_amount_minor"] = amount_minor
            audit.execution_result = exec_result

        await session.commit()

    event = StreamEvent(kind="opportunity.resolved", data={
        "opportunity_id": str(record.opportunity_id),
        "resolution": "recovered_intervention",
        "amount_minor": amount_minor,
        "razorpay_payment_id": recovered_payment_id,
        "via": link_id,
    })
    await bus.publish(event)
    return event


async def mark_no_response(opportunity_id, note: str) -> None:
    async with session_factory() as session:
        opp = (
            await session.execute(select(Opportunity).where(Opportunity.id == opportunity_id))
        ).scalar_one_or_none()
        if opp is None:
            return
        opp.status = "closed_no_response"
        opp.closed_reason = note
        audit = await _latest_audit(session, opp.id)
        if audit is not None and audit.verified_outcome == "pending":
            audit.verified_outcome = "no_response"
        await session.commit()
