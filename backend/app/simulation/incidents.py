from __future__ import annotations

import logging
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.agents.detection import Alarm, scan_for_anomalies
from app.agents.orchestrator import decide_opportunity
from app.core.locks import run_with_singleton_lock
from app.database.models import Incident, Opportunity, Payment
from app.database.session import session_factory
from app.events.bus import StreamEvent, bus

logger = logging.getLogger("incidents")

RESPONSE_BATCH_SIZE = 10
DEFAULT_INTERVENTION_BUDGET = 25


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def create_incident_from_alarm(alarm: Alarm, source: str = "detector") -> Incident:
    seg = alarm.segment
    segment_label = " ".join(filter(None, [seg.bank, seg.method and seg.method.upper()]))
    incident = Incident(
        id=uuid_lib.uuid4(),
        status="detected",
        method=seg.method,
        bank=seg.bank,
        title=f"{segment_label} failure spike",
        severity=alarm.severity,
        diagnosis={
            "summary": f"Failure rate {seg.recent_failure_rate:.0%} vs baseline {seg.baseline_failure_rate:.0%} "
                       f"over last {10} minutes on {segment_label}",
            "detectors": alarm.detectors_fired,
            "z_score": alarm.z_score,
            "cusum": alarm.cusum,
            "affected_segment": seg.to_dict(),
        },
        revenue_at_risk_minor=seg.revenue_at_risk_minor,
        affected_failures=seg.recent_failures,
        intervention_budget=DEFAULT_INTERVENTION_BUDGET,
        detection_stats={
            **seg.to_dict(),
            "z_score": alarm.z_score,
            "cusum": alarm.cusum,
            "detectors_fired": alarm.detectors_fired,
        },
        source=source,
    )
    async with session_factory() as session:
        session.add(incident)
        await session.commit()

    await bus.publish(StreamEvent(kind="incident.detected", data={
        "incident_id": str(incident.id),
        "title": incident.title,
        "severity": incident.severity,
        "revenue_at_risk_minor": incident.revenue_at_risk_minor,
        "detectors": alarm.detectors_fired,
    }))
    logger.warning("INCIDENT %s: %s (risk ₹%.0f)", incident.id, incident.title,
                   incident.revenue_at_risk_minor / 100)
    return incident


async def get_active_incident(method: str | None, bank: str | None) -> Incident | None:
    async with session_factory() as session:
        result = await session.execute(
            select(Incident)
            .where(Incident.status.in_(("detected", "responding")))
            .where(Incident.method == method)
            .where(Incident.bank == bank)
            .order_by(Incident.started_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()


# An intervention consumes incident budget whenever the pipeline reached a
# terminal decision for that opportunity -- not only when a live Razorpay call
# happened.  `shadowed` is the normal outcome while SHADOW_MODE=true (the
# default), so counting only "executed" made the batch permanently report zero
# work and left the budget bar stuck at 0/25 during every local demo.
BUDGET_CONSUMING_STATUSES = frozenset(
    {"executed", "shadowed", "customer_action_required", "deferred"}
)


async def _respond_to_incident_impl(incident_id: UUID) -> dict[str, Any]:
    executed = 0
    async with session_factory() as session:
        incident = (
            await session.execute(select(Incident).where(Incident.id == incident_id))
        ).scalar_one_or_none()
        if incident is None:
            return {"executed": 0, "considered": 0, "candidates": 0,
                    "reason": "incident not found"}
        if incident.status not in ("detected", "responding"):
            return {"executed": 0, "considered": 0, "candidates": 0,
                    "reason": f"incident is {incident.status}; only detected/responding accept a batch"}
        if incident.interventions_executed >= incident.intervention_budget:
            return {"executed": 0, "considered": 0, "candidates": 0,
                    "reason": "intervention budget for this incident is already exhausted"}
        remaining_budget = incident.intervention_budget - incident.interventions_executed
        affected_method = incident.method
        affected_bank = incident.bank
        incident.status = "responding"
        await session.commit()

    batch_size = min(RESPONSE_BATCH_SIZE, remaining_budget)
    async with session_factory() as session:
        candidates = (
            await session.execute(
                select(Opportunity.id)
                .join(Payment, Opportunity.payment_id == Payment.id)
                .where(Opportunity.status == "open")
                .where(Payment.method == affected_method)
                .where(Payment.bank == affected_bank)
                .order_by(Opportunity.amount_minor.desc())
                .limit(batch_size * 4)
            )
        ).scalars().all()

    considered = 0
    for opportunity_id in candidates:
        if executed >= batch_size:
            break
        considered += 1
        result = await decide_opportunity(opportunity_id, trigger=f"incident:{incident_id}")
        if (
            result
            and result["allowed"]
            and result["execution"].get("status") in BUDGET_CONSUMING_STATUSES
        ):
            executed += 1

    if executed or candidates:
        async with session_factory() as session:
            inc = (
                await session.execute(select(Incident).where(Incident.id == incident_id))
            ).scalar_one()
            inc.interventions_executed += executed
            await session.commit()
        await bus.publish(StreamEvent(kind="incident.response", data={
            "incident_id": str(incident_id),
            "batch_executed": executed,
            "candidates_considered": considered,
            "note": "policy skipped blocked/out-of-budget candidates",
        }))

    # An empty candidate list is the single most confusing outcome for an
    # operator, because the incident flips to "responding" and then nothing
    # visibly happens.  Say why instead of silently returning zero.
    reason = None
    if not candidates:
        reason = (
            f"no opportunity is still awaiting a decision for {affected_bank or 'unknown bank'} "
            f"{(affected_method or '').upper()}. The pipeline decides each opportunity "
            "automatically the moment it is created, so a manual batch only finds work when "
            "new failures have arrived since the last decision cycle."
        )
    elif not executed:
        reason = "every candidate was blocked by the safety policy or already resolved"

    return {
        "executed": executed,
        "considered": considered,
        "candidates": len(candidates),
        "reason": reason,
    }


async def respond_to_incident(incident_id: UUID) -> dict[str, Any]:
    """Serialize budget allocation for one incident across all entry points."""
    result = await run_with_singleton_lock(
        f"recovery-incident-response:{incident_id}",
        lambda: _respond_to_incident_impl(incident_id),
    )
    if result is None:
        return {"executed": 0, "considered": 0, "candidates": 0,
                "reason": "a response batch for this incident is already running"}
    return result


async def resolve_stale_incidents(max_age_minutes: int = 30) -> int:
    cutoff = _utcnow() - timedelta(minutes=max_age_minutes)
    async with session_factory() as session:
        stale = (
            await session.execute(
                select(Incident).where(
                    Incident.status.in_(("detected", "responding")),
                    Incident.started_at < cutoff,
                )
            )
        ).scalars().all()
        count = 0
        for incident in stale:
            incident.status = "resolved"
            incident.resolved_at = _utcnow()
            count += 1
        await session.commit()

    for incident in stale:
        await bus.publish(StreamEvent(kind="incident.resolved", data={
            "incident_id": str(incident.id),
            "interventions_executed": incident.interventions_executed,
        }))
    return count


async def detector_cycle() -> dict[str, Any]:
    alarms = await scan_for_anomalies()
    created = 0
    responded = 0
    for alarm in alarms:
        active = await get_active_incident(alarm.segment.method, alarm.segment.bank)
        if active is None:
            incident = await create_incident_from_alarm(alarm)
            created += 1
        else:
            incident = active
            async with session_factory() as session:
                current = await session.get(Incident, incident.id)
                if current is not None:
                    current.revenue_at_risk_minor = alarm.segment.revenue_at_risk_minor
                    current.affected_failures = alarm.segment.recent_failures
                    current.detection_stats = {
                        **alarm.segment.to_dict(),
                        "z_score": alarm.z_score,
                        "cusum": alarm.cusum,
                        "detectors_fired": alarm.detectors_fired,
                    }
                    await session.commit()
        responded += (await respond_to_incident(incident.id))["executed"]
    await resolve_stale_incidents()
    return {"alarms": len(alarms), "incidents_created": created, "interventions": responded}
