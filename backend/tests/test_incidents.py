import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.database.models import Incident, Opportunity, Payment
from app.database.session import session_factory
from app.events.processor import process_payment_event
from app.simulation import engine
from app.simulation.engine import SimulationConfig, start_simulation, stop_simulation


async def burst(bank="HDFC", method="upi", n=40, amount=200000, prefix="burst") -> int:
    now_ts = int(datetime.now(timezone.utc).timestamp())
    failures = 0
    for i in range(n):
        failures += 1
        await process_payment_event("payment.failed", {
            "id": f"pay_{prefix}_{i:03d}",
            "amount": amount,
            "currency": "INR",
            "status": "failed",
            "method": method,
            "bank": bank,
            "error_reason": "timeout",
            "error_source": "network",
            "email": f"{prefix}.cust{i % 10}@test.local",
            "contact": "+919800001111",
            "created_at": now_ts,
        })
    return failures


@pytest.mark.asyncio
async def test_detector_cycle_creates_incident_and_responds():
    from app.simulation.incidents import detector_cycle

    await burst(n=40)
    result = await detector_cycle()

    assert result["incidents_created"] >= 1
    async with session_factory() as session:
        incidents = list((await session.execute(select(Incident))).scalars())
    assert len(incidents) >= 1
    incident = max(incidents, key=lambda i: i.started_at)
    assert incident.bank == "HDFC"
    assert incident.method == "upi"
    assert incident.revenue_at_risk_minor == 40 * 200000
    assert incident.status in ("detected", "responding")
    assert incident.detection_stats["detectors_fired"]


@pytest.mark.asyncio
async def test_no_duplicate_incident_for_same_segment():
    from app.simulation.incidents import detector_cycle

    await burst(prefix="dup")
    await detector_cycle()
    second = await detector_cycle()
    assert second["incidents_created"] == 0


async def _select(x):
    return x


def test_simulation_config_sanitization():
    cfg = SimulationConfig(failure_rate=5.0, payments_per_minute=10000, duration_seconds=1)
    s = cfg.sanitized()
    assert s.failure_rate == 0.95
    assert s.payments_per_minute == 600
    assert s.duration_seconds == 30


def test_simulation_rejects_inverted_amount_range():
    cfg = SimulationConfig(amount_min_minor=500_000, amount_max_minor=100_000)
    with pytest.raises(ValueError, match="amount_min_minor"):
        cfg.sanitized()


@pytest.mark.asyncio
async def test_simulation_generates_labeled_traffic():
    cfg = SimulationConfig(
        method="upi", bank="SBISIM", failure_rate=0.7,
        payments_per_minute=120, duration_seconds=3,
        seed=42, label="test burst",
    )
    started = await start_simulation(cfg)
    assert started["started"] is True
    await asyncio.sleep(4.5)

    final = engine.simulation_status()
    assert final["generated_payments"] > 0
    assert final["synthetic"] is True
    assert "SYNTHETIC" in final["label"]

    await stop_simulation()


@pytest.mark.asyncio
async def test_incident_response_only_considers_the_affected_bank(monkeypatch):
    from app.simulation import incidents as incidents_module

    now = datetime.now(timezone.utc)
    incident_id = uuid.uuid4()
    hdfc_payment_id, sbi_payment_id = uuid.uuid4(), uuid.uuid4()
    hdfc_opp_id, sbi_opp_id = uuid.uuid4(), uuid.uuid4()
    async with session_factory() as session:
        session.add(Incident(
            id=incident_id, status="detected", method="upi", bank="HDFC", title="HDFC UPI failure spike",
            severity="medium", diagnosis={}, detection_stats={}, revenue_at_risk_minor=0,
            affected_failures=0, intervention_budget=10, interventions_executed=0, source="test",
        ))
        for payment_id, bank in ((hdfc_payment_id, "HDFC"), (sbi_payment_id, "SBI")):
            session.add(Payment(
                id=payment_id, razorpay_payment_id=f"pay_{bank}_{payment_id.hex[:8]}", amount_minor=100_000,
                currency="INR", status="failed", method="upi", bank=bank, occurred_at=now,
            ))
        # Flush parent rows before adding FK-dependent opportunities. SQLAlchemy has no
        # relationship metadata to infer the ordering from these UUID-only assignments.
        await session.flush()
        for opp_id, payment_id in ((hdfc_opp_id, hdfc_payment_id), (sbi_opp_id, sbi_payment_id)):
            session.add(Opportunity(
                id=opp_id, payment_id=payment_id, status="open", category="failed_payment", amount_minor=100_000,
                experiment_group="treatment", contact_attempts=0, window_ends_at=now,
            ))
        await session.commit()

    considered = []

    async def fake_decide(opportunity_id, trigger):
        considered.append(opportunity_id)
        return {"allowed": False, "execution": {"status": "blocked"}}

    monkeypatch.setattr(incidents_module, "decide_opportunity", fake_decide)
    await incidents_module.respond_to_incident(incident_id)
    assert considered == [hdfc_opp_id]
