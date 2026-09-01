from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import nullslast, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.orchestrator import decide_opportunity
from app.api.auth import OperatorPrincipal, require_operator
from app.database.models import Customer, DecisionAudit, InterventionRecord, Opportunity, Payment
from app.database.session import get_session

router = APIRouter(prefix="/api/opportunities", tags=["opportunities"])


def _serialize_opportunity(o: Opportunity) -> dict[str, Any]:
    return {
        "id": str(o.id),
        "status": o.status,
        "category": o.category,
        "amount_minor": o.amount_minor,
        "experiment_group": o.experiment_group,
        "source": o.source,
        "is_synthetic": o.is_synthetic,
        "simulation_run_id": o.simulation_run_id,
        "contact_attempts": o.contact_attempts,
        "best_action": o.best_action,
        "expected_recovery_minor": o.expected_recovery_minor,
        # The triage queue shows *why* an opportunity is waiting on a human,
        # which is the difference between a work queue and a list.
        "closed_reason": o.closed_reason,
        "window_ends_at": o.window_ends_at.isoformat(),
        "created_at": o.created_at.isoformat(),
    }


def _serialize_audit(audit: DecisionAudit) -> dict[str, Any]:
    return {
        "id": str(audit.id),
        "trigger": audit.trigger,
        "diagnosis": audit.diagnosis,
        "model_version": audit.model_version,
        "predictions": audit.predictions,
        "feature_snapshot": audit.feature_snapshot,
        "recommended_action": audit.recommended_action,
        "expected_recovery_minor": audit.expected_recovery_minor,
        "policy_decision": audit.policy_decision,
        "executed_action": audit.executed_action,
        "execution_result": audit.execution_result,
        "verified_outcome": audit.verified_outcome,
        "recovered_amount_minor": audit.recovered_amount_minor,
        "created_at": audit.created_at.isoformat(),
    }


@router.get("")
async def list_opportunities(
    status: str | None = None, limit: int = 50, session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 200))
    query = select(Opportunity).order_by(
        nullslast(Opportunity.expected_recovery_minor.desc())
    ).limit(limit)
    if status:
        query = query.where(Opportunity.status == status)
    rows = (await session.execute(query)).scalars().all()
    return [_serialize_opportunity(o) for o in rows]


# Everything the operator queue can still act on, or is still owed an outcome.
QUEUE_STATUSES = (
    "escalated",
    "open",
    "decision_in_progress",
    "intervention_pending",
    "native_retry_pending",
    "shadow_observation",
)


@router.get("/queue")
async def opportunity_queue(
    limit: int = 200, session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    """Unresolved work, for the triage queue.

    This exists because the list endpoint orders by expected recovery value, and
    the rows the queue cares most about — escalations that policy refused to
    decide — carry an expected value of zero. They sorted to the very bottom, so
    a client paging the value-ranked list could never see them. Filtering by
    status server-side is the only way the queue sees its own work.
    """
    limit = max(1, min(limit, 500))
    rows = (
        await session.execute(
            select(Opportunity)
            .where(Opportunity.status.in_(QUEUE_STATUSES))
            # Soonest deadline first: urgency is the queue's organising idea,
            # and the client re-ranks within each lane by what acting is worth.
            .order_by(Opportunity.window_ends_at.asc())
            .limit(limit)
        )
    ).scalars().all()
    return [_serialize_opportunity(o) for o in rows]


@router.get("/recent-decisions")
async def recent_decisions(limit: int = 25, session: AsyncSession = Depends(get_session)) -> list[dict[str, Any]]:
    limit = max(1, min(limit, 100))
    audits = (
        await session.execute(
            select(DecisionAudit).order_by(DecisionAudit.created_at.desc()).limit(limit)
        )
    ).scalars().all()
    return [_serialize_audit(a) for a in audits]


@router.get("/{opportunity_id}")
async def opportunity_detail(
    opportunity_id: UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    opp = (
        await session.execute(select(Opportunity).where(Opportunity.id == opportunity_id))
    ).scalar_one_or_none()
    if opp is None:
        raise HTTPException(status_code=404, detail="opportunity not found")

    payment = (
        await session.execute(select(Payment).where(Payment.id == opp.payment_id))
    ).scalar_one_or_none()
    customer = (
        await session.execute(select(Customer).where(Customer.id == payment.customer_id))
    ).scalar_one_or_none() if payment and payment.customer_id else None
    audits = (
        await session.execute(
            select(DecisionAudit)
            .where(DecisionAudit.opportunity_id == opp.id)
            .order_by(DecisionAudit.created_at.asc())
        )
    ).scalars().all()
    interventions = (
        await session.execute(
            select(InterventionRecord)
            .where(InterventionRecord.opportunity_id == opp.id)
            .order_by(InterventionRecord.created_at.asc())
        )
    ).scalars().all()

    return {
        "opportunity": {
            "id": str(opp.id),
            "status": opp.status,
            "category": opp.category,
            "amount_minor": opp.amount_minor,
            "experiment_group": opp.experiment_group,
            "source": opp.source,
            "is_synthetic": opp.is_synthetic,
            "simulation_run_id": opp.simulation_run_id,
            "assignment_probability": opp.assignment_probability,
            "contact_attempts": opp.contact_attempts,
            "best_action": opp.best_action,
            "expected_recovery_minor": opp.expected_recovery_minor,
            "closed_reason": opp.closed_reason,
            "window_ends_at": opp.window_ends_at.isoformat(),
            "created_at": opp.created_at.isoformat(),
        },
        "payment": {
            "razorpay_payment_id": payment.razorpay_payment_id if payment else None,
            "method": payment.method if payment else None,
            "bank": payment.bank if payment else None,
            "error_reason": payment.error_reason if payment else None,
            "error_description": payment.error_description if payment else None,
            "occurred_at": payment.occurred_at.isoformat() if payment else None,
            "order_id": payment.order_id if payment else None,
            "subscription_id": payment.subscription_id if payment else None,
            "source": payment.source if payment else None,
        } if payment else None,
        "customer": {
            "identity_key": customer.identity_key if customer else None,
            "email": customer.email if customer else None,
        } if customer else None,
        "decisions": [_serialize_audit(a) for a in audits],
        "interventions": [
            {
                "action": iv.action,
                "status": iv.status,
                "reference": iv.razorpay_reference,
                "payload": iv.payload,
                "created_at": iv.created_at.isoformat(),
            }
            for iv in interventions
        ],
    }


@router.post("/{opportunity_id}/decide")
async def re_decide(
    opportunity_id: UUID, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    result = await decide_opportunity(opportunity_id, trigger="manual.re-decide")
    if result is None:
        raise HTTPException(status_code=409, detail="opportunity not found or not open")
    return result
