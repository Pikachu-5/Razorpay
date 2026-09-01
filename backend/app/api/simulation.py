from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import OperatorPrincipal, require_operator
from app.core.locks import run_with_singleton_lock
from app.database.models import Incident, Opportunity, Payment
from app.database.session import get_session
from app.simulation import engine
from app.simulation.incidents import detector_cycle, respond_to_incident

router = APIRouter(tags=["simulation"])


class SimulationRequest(BaseModel):
    method: str = Field(default="upi", pattern="^(upi|card|netbanking|wallet)$")
    bank: str = Field(default="HDFC", max_length=50)
    failure_rate: float = Field(default=0.65, ge=0.0, le=1.0)
    payments_per_minute: int = Field(default=120, ge=10, le=600)
    amount_min_minor: int = Field(default=100_000, ge=100)
    amount_max_minor: int = Field(default=3_500_000, ge=100)
    duration_seconds: int = Field(default=300, ge=30, le=900)
    subscription_share: float = Field(default=0.0, ge=0.0, le=1.0)
    label: str = Field(default="incident simulation", max_length=100)
    # Opt-in: how often simulated customers pay after being contacted (treatment)
    # versus left alone (control). The gap between them is the recovery lift.
    recovery_rate_treatment: float = Field(default=0.0, ge=0.0, le=0.95)
    recovery_rate_control: float = Field(default=0.0, ge=0.0, le=0.95)

    @model_validator(mode="after")
    def validate_amount_range(self) -> "SimulationRequest":
        if self.amount_min_minor > self.amount_max_minor:
            raise ValueError("amount_min_minor must be less than or equal to amount_max_minor")
        return self


@router.post("/api/simulation/start")
async def start_simulation(
    req: SimulationRequest, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    return await engine.start_simulation(engine.SimulationConfig(**req.model_dump()))


@router.post("/api/simulation/stop")
async def stop_simulation(_: OperatorPrincipal = Depends(require_operator)) -> dict[str, Any]:
    return await engine.stop_simulation()


@router.get("/api/simulation/status")
async def simulation_status() -> dict[str, Any]:
    return engine.simulation_status()


@router.post("/api/incidents/scan")
async def trigger_scan(_: OperatorPrincipal = Depends(require_operator)) -> dict[str, Any]:
    result = await run_with_singleton_lock("recovery-incident-detector", detector_cycle)
    return result or {"alarms": 0, "incidents_created": 0, "interventions": 0, "skipped": "scan already running"}


@router.get("/api/incidents")
async def list_incidents(
    limit: int = Query(default=25, ge=1, le=100), session: AsyncSession = Depends(get_session)
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Incident).order_by(desc(Incident.started_at)).limit(limit)
        )
    ).scalars().all()
    return [_incident_dict(i) for i in rows]


@router.get("/api/incidents/{incident_id}")
async def incident_detail(
    incident_id: UUID, session: AsyncSession = Depends(get_session)
) -> dict[str, Any]:
    incident = (
        await session.execute(select(Incident).where(Incident.id == incident_id))
    ).scalar_one_or_none()
    if incident is None:
        raise HTTPException(status_code=404, detail="incident not found")

    affected_opps = (
        await session.execute(
            select(Opportunity, Payment.method)
            .join(Payment, Opportunity.payment_id == Payment.id)
            .where(Payment.method == incident.method)
            .where(Payment.bank == incident.bank)
            .order_by(desc(Opportunity.created_at))
            .limit(50)
        )
    ).all()

    return {
        **_incident_dict(incident),
        "detection_stats": incident.detection_stats,
        "affected_opportunities": [
            {
                "id": str(opp.id),
                "status": opp.status,
                "amount_minor": opp.amount_minor,
                "best_action": opp.best_action,
                "expected_recovery_minor": opp.expected_recovery_minor,
            }
            for opp, _ in affected_opps
        ],
    }


@router.post("/api/incidents/{incident_id}/respond")
async def manual_respond(
    incident_id: UUID, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    result = await respond_to_incident(incident_id)
    return {
        "incident_id": str(incident_id),
        "batch_executed": result["executed"],
        "candidates_considered": result["considered"],
        "candidates_found": result["candidates"],
        "reason": result["reason"],
    }


def _incident_dict(i: Incident) -> dict[str, Any]:
    return {
        "id": str(i.id),
        "status": i.status,
        "title": i.title,
        "severity": i.severity,
        "method": i.method,
        "bank": i.bank,
        "revenue_at_risk_minor": i.revenue_at_risk_minor,
        "affected_failures": i.affected_failures,
        "interventions_executed": i.interventions_executed,
        "intervention_budget": i.intervention_budget,
        "source": i.source,
        "started_at": i.started_at.isoformat(),
        "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
        "detection_stats": i.detection_stats,
    }
