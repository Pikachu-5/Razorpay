from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DecisionAudit, Opportunity, Payment, RawEvent, RevenueAdjustment
from app.database.session import get_session
from app.ml.features import error_group

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# Every status here means "the money has not come back yet".  `shadow_observation`
# and `control_holdout` are deliberately included: in shadow mode nothing was
# actually sent to the customer, and a control-group opportunity is withheld on
# purpose, so in both cases the revenue is still genuinely at risk.  Leaving them
# out made the KPI collapse to zero the moment the pipeline finished deciding.
# This set matches the unresolved set used by reconciliation.py and
# razorpay_lifecycle.py.
OPEN_STATUSES = (
    "open",
    "decision_in_progress",
    "intervention_pending",
    "native_retry_pending",
    "shadow_observation",
    "control_holdout",
)
RECOVERED_STATUSES = ("recovered_natural", "recovered_intervention")


def _utc_today() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def calculate_experiment_results(
    *,
    treatment_total: int,
    treatment_recovered: int,
    treatment_amount_sum: int,
    treatment_recovered_minor: int,
    control_total: int,
    control_recovered: int,
    control_recovered_minor: int,
) -> dict[str, Any]:
    """Calculate signed treatment lift and its two-proportion significance test."""
    import math

    # PostgreSQL SUM(BigInteger) may arrive as Decimal via asyncpg; normalize
    # aggregate values before mixing them with floating-point rates.
    treatment_total = int(treatment_total)
    treatment_recovered = int(treatment_recovered)
    treatment_amount_sum = int(treatment_amount_sum)
    treatment_recovered_minor = int(treatment_recovered_minor)
    control_total = int(control_total)
    control_recovered = int(control_recovered)
    control_recovered_minor = int(control_recovered_minor)

    cr_treatment = treatment_recovered / treatment_total if treatment_total else 0.0
    cr_control = control_recovered / control_total if control_total else 0.0
    delta_cr = cr_treatment - cr_control
    lift_pct = (delta_cr / cr_control * 100.0) if cr_control else 0.0
    avg_treatment_amount = treatment_amount_sum / treatment_total if treatment_total else 0.0
    # Do not clamp this value: a negative result is important experiment evidence.
    incremental_revenue_minor = int(round(delta_cr * treatment_total * avg_treatment_amount))

    z_score = 0.0
    p_value = 1.0
    if treatment_total and control_total:
        pooled_p = (treatment_recovered + control_recovered) / (treatment_total + control_total)
        if 0 < pooled_p < 1:
            se = math.sqrt(pooled_p * (1 - pooled_p) * (1 / treatment_total + 1 / control_total))
            if se:
                z_score = delta_cr / se
                p_value = math.erfc(abs(z_score) / math.sqrt(2))

    return {
        "treatment": {
            "total_opportunities": treatment_total,
            "recovered_count": treatment_recovered,
            "recovered_amount_minor": int(treatment_recovered_minor),
            "conversion_rate": round(cr_treatment, 4),
        },
        "control": {
            "total_opportunities": control_total,
            "recovered_count": control_recovered,
            "recovered_amount_minor": int(control_recovered_minor),
            "conversion_rate": round(cr_control, 4),
        },
        "causal_lift_pct": round(lift_pct, 2),
        "delta_conversion_rate": round(delta_cr, 4),
        "incremental_revenue_minor": incremental_revenue_minor,
        "incremental_revenue_inr": round(incremental_revenue_minor / 100, 2),
        "z_score": round(z_score, 3),
        "p_value": round(p_value, 4),
        "statistically_significant": p_value < 0.05,
    }


@router.get("/summary")
async def summary(
    include_synthetic: bool = Query(
        default=False,
        description="Include simulated demo traffic. Off by default so operational KPIs "
                    "only ever reflect real payments.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    today = _utc_today()
    opp_real = true() if include_synthetic else Opportunity.is_synthetic.is_(False)
    pay_real = true() if include_synthetic else Payment.is_synthetic.is_(False)
    evt_real = true() if include_synthetic else (RawEvent.source != "simulation")
    adj_real = true() if include_synthetic else (RevenueAdjustment.source != "simulation")

    revenue_at_risk = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.amount_minor), 0)).where(
                Opportunity.status.in_(OPEN_STATUSES), opp_real
            )
        )
    ).scalar_one()

    open_count = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.status.in_(OPEN_STATUSES), opp_real
            )
        )
    ).scalar_one()

    recovered_natural_today = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.amount_minor), 0)).where(
                Opportunity.status == "recovered_natural",
                opp_real,
                func.coalesce(Opportunity.updated_at, Opportunity.created_at) >= today,
            )
        )
    ).scalar_one()

    recovered_intervention_today = (
        await session.execute(
            select(func.coalesce(func.sum(DecisionAudit.recovered_amount_minor), 0)).where(
                DecisionAudit.verified_outcome == "verified_recovered",
                DecisionAudit.opportunity_id.in_(
                    select(Opportunity.id).where(opp_real)
                ),
                DecisionAudit.updated_at >= today,
            )
        )
    ).scalar_one()
    recovered_today = int(recovered_natural_today) + int(recovered_intervention_today)

    events_today = (
        await session.execute(
            select(func.count()).select_from(RawEvent).where(
                RawEvent.received_at >= today, evt_real
            )
        )
    ).scalar_one()

    status_rows = (
        await session.execute(
            select(Payment.status, func.count())
            .where(Payment.occurred_at >= today, pay_real)
            .group_by(Payment.status)
        )
    ).all()
    payments_by_status = {status: count for status, count in status_rows}

    failed_today = (
        await session.execute(
            select(func.count()).select_from(Payment).where(
                Payment.status == "failed", Payment.occurred_at >= today,
                pay_real,
            )
        )
    ).scalar_one()
    succeeded_today = (
        await session.execute(
            select(func.count()).select_from(Payment).where(
                Payment.status.in_(("captured", "authorized")), Payment.occurred_at >= today,
                pay_real,
            )
        )
    ).scalar_one()

    attempts = failed_today + succeeded_today
    success_rate = (succeeded_today / attempts) if attempts else None

    adjustments_today = int((await session.execute(
        select(func.coalesce(func.sum(RevenueAdjustment.amount_minor), 0)).where(
            RevenueAdjustment.occurred_at >= today,
            adj_real,
            or_(
                (RevenueAdjustment.kind == "refund") & RevenueAdjustment.status.in_(("created", "pending", "processed")),
                (RevenueAdjustment.kind == "dispute") & RevenueAdjustment.status.in_(("created", "open", "lost")),
            ),
        )
    )).scalar_one())
    synthetic_payments = int((await session.execute(
        select(func.count()).select_from(Payment).where(Payment.is_synthetic.is_(True))
    )).scalar_one())

    return {
        "revenue_at_risk_minor": int(revenue_at_risk),
        "open_opportunities": int(open_count),
        "recovered_today_minor": int(recovered_today),
        "recovered_natural_today_minor": int(recovered_natural_today),
        "events_received_today": int(events_today),
        "payments_by_status": payments_by_status,
        "success_rate_today": success_rate,
        "revenue_adjustments_today_minor": adjustments_today,
        # Keep this signed. A negative net value is an important operational signal,
        # not a presentation problem to hide at the API boundary.
        "net_recovered_today_minor": int(recovered_today) - adjustments_today,
        "includes_simulated_traffic": include_synthetic,
        "synthetic_payments_excluded": synthetic_payments,
    }


# How each failure class is played, and why. The copy is part of the product:
# an operator seeing "instrument" needs to know it means a dead card and that
# chasing it with the same instrument is wasted contact budget.
FAILURE_CLASS_COPY: dict[str, dict[str, str]] = {
    "temporary": {
        "label": "Timeout or gateway error",
        "play": "Silent retry while intent is still warm. The customer never hears from us.",
    },
    "insufficient_funds": {
        "label": "Insufficient funds",
        "play": "Wait for the balance to move, then one link. Retrying immediately just burns the attempt.",
    },
    "auth": {
        "label": "Authentication failed",
        "play": "One payment link so the customer can re-authenticate. No silent retry — it cannot succeed.",
    },
    "instrument": {
        "label": "Dead instrument",
        "play": "The card or VPA is gone. Recovery needs a different instrument, or nothing at all.",
    },
    "unknown": {
        "label": "No reason code",
        "play": "The rails told us nothing. Treated conservatively: low confidence, low priority.",
    },
}

# Published merchant failure mixes for Indian payments, for context next to the
# measured numbers. Sources are cited in the UI rather than implied.
INDUSTRY_MIX_REFERENCE: dict[str, str] = {
    "temporary": "35–45% bank or server timeout, plus 10–15% network faults",
    "auth": "20–30% wrong PIN or failed authentication",
    "insufficient_funds": "15–25% insufficient balance",
    "instrument": "5–10% blocked or deactivated instrument",
}


@router.get("/failure-mix")
async def failure_mix(
    include_synthetic: bool = Query(
        default=True,
        description="Demo installs have only seeded traffic, so this defaults ON. "
                    "The response always reports which population it counted.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Failure classes by share of count, share of value, and how they resolve.

    Recovery here is deliberately the *natural* rate — how often each class comes
    back with no intervention at all. That is the number the expected-value
    ranker is measured against, so showing anything else would describe a
    different product than the one making the decisions.
    """
    opp_real = true() if include_synthetic else Opportunity.is_synthetic.is_(False)

    rows = (
        await session.execute(
            select(
                Payment.error_reason,
                Opportunity.status,
                func.count().label("n"),
                func.coalesce(func.sum(Opportunity.amount_minor), 0).label("value"),
            )
            .join(Payment, Payment.id == Opportunity.payment_id)
            .where(opp_real)
            .group_by(Payment.error_reason, Opportunity.status)
        )
    ).all()

    buckets: dict[str, dict[str, int]] = {}
    for error_reason, status, count, value in rows:
        group = error_group(error_reason)
        bucket = buckets.setdefault(group, {"count": 0, "value_minor": 0, "recovered": 0})
        bucket["count"] += int(count)
        bucket["value_minor"] += int(value)
        if status in RECOVERED_STATUSES:
            bucket["recovered"] += int(count)

    total_count = sum(b["count"] for b in buckets.values())
    total_value = sum(b["value_minor"] for b in buckets.values())

    classes = [
        {
            "group": group,
            "label": FAILURE_CLASS_COPY.get(group, {}).get("label", group),
            "play": FAILURE_CLASS_COPY.get(group, {}).get("play", ""),
            "count": bucket["count"],
            "value_minor": bucket["value_minor"],
            "share_of_count": round(bucket["count"] / total_count, 4) if total_count else 0.0,
            "share_of_value": round(bucket["value_minor"] / total_value, 4) if total_value else 0.0,
            "recovered_count": bucket["recovered"],
            "recovery_rate": (
                round(bucket["recovered"] / bucket["count"], 4) if bucket["count"] else 0.0
            ),
            "industry_reference": INDUSTRY_MIX_REFERENCE.get(group),
        }
        for group, bucket in buckets.items()
    ]
    classes.sort(key=lambda item: item["share_of_value"], reverse=True)

    return {
        "classes": classes,
        "total_count": total_count,
        "total_value_minor": total_value,
        "includes_simulated_traffic": include_synthetic,
    }


@router.get("/experiment")
async def experiment_metrics(
    include_synthetic: bool = Query(
        default=False,
        description="Include simulated demo traffic in the treatment/control comparison. "
                    "Results are then a demonstration of the measurement machinery, NOT "
                    "evidence of real-world lift.",
    ),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    opp_real = true() if include_synthetic else Opportunity.is_synthetic.is_(False)
    # Treatment Group Metrics
    treatment_total = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.experiment_group == "treatment", opp_real
            )
        )
    ).scalar_one()

    treatment_recovered = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.experiment_group == "treatment",
                opp_real,
                Opportunity.status.in_(RECOVERED_STATUSES),
            )
        )
    ).scalar_one()

    treatment_amount_sum = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.amount_minor), 0)).where(
                Opportunity.experiment_group == "treatment"
                , opp_real
            )
        )
    ).scalar_one()

    treatment_recovered_minor = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.amount_minor), 0)).where(
                Opportunity.experiment_group == "treatment",
                opp_real,
                Opportunity.status.in_(RECOVERED_STATUSES),
            )
        )
    ).scalar_one()

    # Control Group Metrics
    control_total = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.experiment_group == "control", opp_real
            )
        )
    ).scalar_one()

    control_recovered = (
        await session.execute(
            select(func.count()).select_from(Opportunity).where(
                Opportunity.experiment_group == "control",
                opp_real,
                Opportunity.status.in_(RECOVERED_STATUSES),
            )
        )
    ).scalar_one()

    control_recovered_minor = (
        await session.execute(
            select(func.coalesce(func.sum(Opportunity.amount_minor), 0)).where(
                Opportunity.experiment_group == "control",
                opp_real,
                Opportunity.status.in_(RECOVERED_STATUSES),
            )
        )
    ).scalar_one()

    result = calculate_experiment_results(
        treatment_total=treatment_total,
        treatment_recovered=treatment_recovered,
        treatment_amount_sum=treatment_amount_sum,
        treatment_recovered_minor=treatment_recovered_minor,
        control_total=control_total,
        control_recovered=control_recovered,
        control_recovered_minor=control_recovered_minor,
    )
    source_rows = (await session.execute(
        select(Opportunity.source, func.count())
        .where(opp_real)
        .group_by(Opportunity.source)
    )).all()
    minimum_sample_met = int(treatment_total) >= 100 and int(control_total) >= 100
    result.update({
        "synthetic_excluded": not include_synthetic,
        "includes_simulated_traffic": include_synthetic,
        "source_counts": {source: int(count) for source, count in source_rows},
        "minimum_sample_met": minimum_sample_met,
        "evidence_quality": (
            "simulated_demonstration_only" if include_synthetic
            else "eligible_for_inference" if minimum_sample_met
            else "insufficient_sample_do_not_claim_lift"
        ),
    })
    return result
