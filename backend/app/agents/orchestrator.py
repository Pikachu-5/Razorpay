from __future__ import annotations

import logging
import time
import uuid as uuid_lib
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select, update

from app.agents.diagnosis import Diagnosis, diagnose
from app.agents.revenue import RevenueRanking, rank_actions
from app.core.config import get_settings
from app.database.models import (
    Customer,
    DecisionAudit,
    InterventionRecord,
    Opportunity,
    Payment,
    SubscriptionState,
)
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus
from app.execution.executor import execute_action
from app.ml.actions import CUSTOMER_CONTACT_ACTIONS, INTERVENTION_COST_MINOR
from app.policy.engine import select_action_under_policy

logger = logging.getLogger("orchestrator")

# Only actions that actually put a message in front of the customer set the
# cooldown clock. `escalate_to_human` queues internal review and must not.
CONTACT_ACTIONS = CUSTOMER_CONTACT_ACTIONS


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _do_nothing_candidate(baseline: float, amount_minor: int) -> dict[str, Any]:
    """The reference point every other action is scored against."""
    return {
        "action": "do_nothing",
        "probability": None,
        "baseline_probability": round(baseline, 4),
        "incremental_probability": 0.0,
        "cost_minor": 0,
        "gross_recovery_minor": int(baseline * amount_minor),
        "expected_recovery_minor": 0,
    }


async def build_context(session, opportunity: Opportunity) -> tuple[dict[str, Any], Payment | None, Customer | None]:
    payment = (
        await session.execute(select(Payment).where(Payment.id == opportunity.payment_id))
    ).scalar_one_or_none()

    customer = None
    if payment and payment.customer_id:
        customer = (
            await session.execute(select(Customer).where(Customer.id == payment.customer_id))
        ).scalar_one_or_none()

    prior_payments = prior_successes = 0
    prior_method_match = False
    if customer is not None and payment is not None:
        history = (
            await session.execute(
                select(Payment.status, Payment.method).where(
                    Payment.customer_id == customer.id,
                    Payment.id != payment.id,
                )
            )
        ).all()
        prior_payments = len(history)
        prior_successes = sum(1 for status, _ in history if status in ("captured", "authorized"))
        prior_method_match = any(
            status in ("captured", "authorized") and method == payment.method
            for status, method in history
        )

    occurred_at = payment.occurred_at if payment else opportunity.created_at
    minutes_since_failure = max(1.0, (_utcnow() - occurred_at).total_seconds() / 60.0)

    ctx: dict[str, Any] = {
        "amount_minor": opportunity.amount_minor,
        "method": payment.method if payment else None,
        "bank": payment.bank if payment else None,
        "error_reason": payment.error_reason if payment else None,
        "is_subscription": bool(payment.subscription_id or payment.invoice_id) if payment else False,
        "customer_prior_payments": prior_payments,
        "customer_prior_successes": prior_successes,
        "customer_prior_success_with_method": prior_method_match,
        "prior_failed_same_instrument": 0,
        "minutes_since_failure": minutes_since_failure,
        "merchant_baseline_success": 0.87,
        "merchant_monthly_volume": 10000,
        "occurred_hour": occurred_at.hour,
        "occurred_weekday": occurred_at.weekday(),
    }
    return ctx, payment, customer


async def last_contact_at(opportunity_id: UUID) -> datetime | None:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(InterventionRecord.created_at)
                .where(
                    InterventionRecord.opportunity_id == opportunity_id,
                    InterventionRecord.action.in_(CONTACT_ACTIONS),
                    InterventionRecord.status == "executed",
                )
                .order_by(InterventionRecord.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    return row


async def _decide_opportunity_impl(opportunity_id: UUID, trigger: str) -> dict | None:
    started = time.perf_counter()

    # Claim the opportunity before computing an action or contacting a customer.
    # The conditional update is atomic across API requests and background
    # consumers, so only one worker can reach the external executor.
    async with session_factory() as session:
        opportunity = (
            await session.execute(
                update(Opportunity)
                .where(Opportunity.id == opportunity_id, Opportunity.status == "open")
                .values(status="decision_in_progress")
                .returning(Opportunity)
            )
        ).scalar_one_or_none()
        await session.commit()
    if opportunity is None:
        return None

    async with session_factory() as session:
        ctx, payment, customer = await build_context(session, opportunity)

    diagnosis: Diagnosis = diagnose(
        error_reason=ctx["error_reason"],
        is_subscription=ctx["is_subscription"],
        prior_failed_same_instrument=ctx["prior_failed_same_instrument"],
        method=ctx["method"],
        bank=ctx["bank"],
    )
    await bus.publish(StreamEvent(kind="diagnosis.completed", data={
        "opportunity_id": str(opportunity_id),
        "classification": diagnosis.classification,
        "confidence": diagnosis.confidence,
        "summary": diagnosis.summary,
    }))

    ranking: RevenueRanking = rank_actions(ctx, opportunity.amount_minor)
    baseline_probability = ranking.natural_recovery_probability
    if payment and payment.subscription_id:
        async with session_factory() as session:
            subscription = await session.get(SubscriptionState, payment.subscription_id)
        if subscription and subscription.status == "pending":
            # Razorpay's own retry cycle is already running. The incremental
            # value of waiting is measured against doing nothing at all, the
            # same convention `rank_actions` uses, so the two paths stay
            # comparable in the audit record.
            probability = 0.45
            incremental = max(0.0, probability - baseline_probability)
            expected = int(incremental * opportunity.amount_minor)
            ranking = RevenueRanking(
                ranked=[{
                    "action": "wait_for_native_retry", "probability": probability,
                    "baseline_probability": round(baseline_probability, 4),
                    "incremental_probability": round(incremental, 4),
                    "cost_minor": 0,
                    "gross_recovery_minor": int(probability * opportunity.amount_minor),
                    "expected_recovery_minor": expected,
                    "evidence_source": "razorpay_pending_native_retry_state",
                }, _do_nothing_candidate(baseline_probability, opportunity.amount_minor)],
                best_action="wait_for_native_retry",
                best_expected_minor=expected,
                model_version="subscription-workflow-v1", degraded=False,
                natural_recovery_probability=baseline_probability,
            )
        elif subscription and subscription.status == "halted":
            # A halted subscription will not retry itself, so the do-nothing
            # counterfactual is much weaker than the general failure baseline.
            probability = 0.35
            halted_baseline = min(baseline_probability, 0.10)
            incremental = max(0.0, probability - halted_baseline)
            cost = INTERVENTION_COST_MINOR["prompt_card_change"]
            expected = int(incremental * opportunity.amount_minor) - cost
            ranking = RevenueRanking(
                ranked=[{
                    "action": "prompt_card_change", "probability": probability,
                    "baseline_probability": round(halted_baseline, 4),
                    "incremental_probability": round(incremental, 4),
                    "cost_minor": cost,
                    "gross_recovery_minor": int(probability * opportunity.amount_minor),
                    "expected_recovery_minor": expected,
                    "evidence_source": "razorpay_halted_card_update_workflow",
                }, _do_nothing_candidate(halted_baseline, opportunity.amount_minor)],
                best_action="prompt_card_change", best_expected_minor=expected,
                model_version="subscription-workflow-v1", degraded=False,
                natural_recovery_probability=halted_baseline,
            )
    await bus.publish(StreamEvent(kind="prediction.completed", data={
        "opportunity_id": str(opportunity_id),
        "model_version": ranking.model_version,
        "degraded": ranking.degraded,
        "best_action": ranking.best_action,
        "expected_recovery_minor": ranking.best_expected_minor,
        "natural_recovery_probability": ranking.natural_recovery_probability,
        "top_predictions": ranking.ranked[:4],
    }))

    decision, chosen = select_action_under_policy(
        ranking.ranked,
        amount_minor=opportunity.amount_minor,
        contact_attempts=opportunity.contact_attempts,
        last_contact_at=await last_contact_at(opportunity_id),
    )
    chosen_action = decision.action
    expected_minor = int((chosen or {}).get("expected_recovery_minor", 0))
    predicted_probability = (chosen or {}).get("probability")

    await bus.publish(StreamEvent(kind="policy.evaluated", data={
        "opportunity_id": str(opportunity_id),
        "action": chosen_action,
        "allowed": decision.allowed,
        "rules_failed": [r.rule for r in decision.rules if not r.passed],
    }))

    is_control_holdout = opportunity.experiment_group == "control"
    execution: dict[str, Any] = {"status": "not_executed"}
    final_status = "open"

    if is_control_holdout:
        chosen_action = "do_nothing"
        execution = {
            "status": "holdout",
            "note": "control group holdout for counterfactual causal lift attribution",
        }
        final_status = "control_holdout"
    elif decision.allowed and chosen_action != "do_nothing":
        async with session_factory() as session:
            still_claimed = (
                await session.execute(
                    select(Opportunity.status).where(Opportunity.id == opportunity_id)
                )
            ).scalar_one_or_none()
        if still_claimed != "decision_in_progress":
            execution = {
                "status": "cancelled",
                "note": f"opportunity became {still_claimed or 'missing'} before execution",
            }
            final_status = still_claimed or "escalated"
        else:
            result = await execute_action(
                chosen_action,
                opportunity_id=opportunity_id,
                amount_minor=opportunity.amount_minor,
                customer_name=customer.name if customer else None,
                customer_email=customer.email if customer else None,
                customer_contact=customer.contact if customer else None,
                rationale=f"EV ₹{expected_minor / 100:,.0f} · p={predicted_probability}",
                subscription_id=payment.subscription_id,
                reason=(
                    f"₹{opportunity.amount_minor / 100:,.0f} exceeds the "
                    f"₹{get_settings().policy_max_amount_minor / 100:,.0f} automated value cap; "
                    "a person decides this one"
                    if chosen_action == "escalate_to_human" else None
                ),
            )
            execution = {"status": result.status, **result.detail}
            if chosen_action == "escalate_to_human" and result.status == "executed":
                final_status = "escalated"
            elif result.status == "executed":
                final_status = "intervention_pending"
            elif result.status == "customer_action_required":
                final_status = "intervention_pending"
            elif result.status == "deferred":
                final_status = "native_retry_pending"
            elif result.status == "shadowed":
                final_status = "shadow_observation"
            else:
                execution["note"] = execution.get(
                    "error", "execution failed after bounded retries; human review required"
                )
                final_status = "escalated"
    elif chosen_action == "do_nothing":
        reason = next((r.detail for r in reversed(decision.rules) if not r.passed), None) or \
            "no positive-expected-value action available"
        execution = {"status": "closed", "note": reason}
        final_status = "closed_not_viable"
    else:
        failed_rules = [r.rule for r in decision.rules if not r.passed]
        execution = {"status": "blocked", "note": f"policy blocked: {', '.join(failed_rules)}"}
        final_status = "escalated"


    audit = DecisionAudit(
        id=uuid_lib.uuid4(),
        opportunity_id=opportunity_id,
        trigger=trigger,
        diagnosis={
            "classification": diagnosis.classification,
            "summary": diagnosis.summary,
            "confidence": diagnosis.confidence,
            "evidence": diagnosis.evidence,
        },
        model_version=ranking.model_version,
        predictions={item["action"]: item for item in ranking.ranked},
        feature_snapshot=ctx,
        recommended_action=ranking.best_action,
        expected_recovery_minor=expected_minor,
        policy_decision=decision.to_dict(),
        executed_action=chosen_action,
        execution_result=execution,
        verified_outcome=("do_nothing" if final_status == "closed_not_viable" else "pending"),
    )

    async with session_factory() as session:
        final_values: dict[str, Any] = {"status": final_status}
        if final_status == "intervention_pending":
            final_values.update({
                "best_action": chosen_action,
                "expected_recovery_minor": expected_minor,
                "contact_attempts": Opportunity.contact_attempts + 1,
            })
        elif final_status == "native_retry_pending":
            final_values.update({
                "best_action": chosen_action,
                "expected_recovery_minor": expected_minor,
            })
        elif final_status == "escalated" and chosen_action == "escalate_to_human":
            # Escalation is a decision, not an absence of one. Recording the
            # action keeps the operator queue from describing these as "no
            # action chosen" when policy deliberately chose a person.
            final_values["best_action"] = chosen_action
        if final_status in ("closed_not_viable", "control_holdout", "escalated", "shadow_observation"):
            # Prefer the executor's stated reason over its free-text note: the
            # note carries the expected-value rationale, which for an escalation
            # reads "EV ₹0 · p=None" and tells the operator nothing.
            final_values["closed_reason"] = execution.get("reason") or execution.get("note")
        finalized = await session.execute(
            update(Opportunity)
            .where(
                Opportunity.id == opportunity_id,
                Opportunity.status == "decision_in_progress",
            )
            .values(**final_values)
        )
        if finalized.rowcount != 1:
            # A concurrently-arrived payment success won the race. Keep that
            # outcome rather than overwriting it with an intervention state.
            execution["note"] = "opportunity resolved while decision was executing"
            current_status = (
                await session.execute(
                    select(Opportunity.status).where(Opportunity.id == opportunity_id)
                )
            ).scalar_one_or_none()
            if current_status:
                final_status = current_status
        session.add(audit)
        await session.commit()
        audit_id = audit.id

    await bus.publish(StreamEvent(kind="decision.finalized", data={
        "opportunity_id": str(opportunity_id),
        "audit_id": str(audit_id),
        "action": chosen_action,
        "allowed": decision.allowed,
        "final_status": final_status,
        "latency_ms": int((time.perf_counter() - started) * 1000),
    }))

    return {
        "audit_id": str(audit_id),
        "opportunity_status": final_status,
        "action": chosen_action,
        "allowed": decision.allowed,
        "execution": execution,
    }


async def decide_opportunity(
    opportunity_id: UUID, trigger: str = "opportunity.created"
) -> dict | None:
    """Run a decision without leaving a permanently claimed opportunity on failure."""
    try:
        return await _decide_opportunity_impl(opportunity_id, trigger)
    except Exception as exc:
        logger.exception("decision failed for opportunity %s", opportunity_id)
        note = f"decision pipeline failed: {type(exc).__name__}: {str(exc)[:500]}"
        async with session_factory() as session:
            restored = await session.execute(
                update(Opportunity)
                .where(
                    Opportunity.id == opportunity_id,
                    Opportunity.status == "decision_in_progress",
                )
                .values(status="escalated", closed_reason=note)
            )
            if restored.rowcount == 1:
                session.add(
                    DecisionAudit(
                        id=uuid_lib.uuid4(),
                        opportunity_id=opportunity_id,
                        trigger=trigger,
                        executed_action=None,
                        execution_result={"status": "failed", "error": note},
                        verified_outcome="exception",
                    )
                )
            await session.commit()
        await bus.publish(
            StreamEvent(
                kind="decision.failed",
                data={"opportunity_id": str(opportunity_id), "error": note},
            )
        )
        return {
            "opportunity_status": "escalated",
            "action": None,
            "allowed": False,
            "execution": {"status": "failed", "error": note},
        }
