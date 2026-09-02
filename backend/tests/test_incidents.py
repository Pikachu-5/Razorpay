import asyncio
import time
import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.core.config import get_settings
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


def test_open_demo_clamps_simulation_size(monkeypatch):
    """A public visitor gets tighter ceilings than a local operator."""
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    s = SimulationConfig(payments_per_minute=600, duration_seconds=900).sanitized()
    assert s.payments_per_minute == settings.demo_max_payments_per_minute
    assert s.duration_seconds == settings.demo_max_duration_seconds
    # The bound that matters: total synthetic volume per run.
    assert s.payments_per_minute * s.duration_seconds / 60 <= 300


def test_console_presets_are_unaffected_by_the_demo_clamp(monkeypatch):
    """The limits must bite abuse only -- never ordinary use.

    The console's largest preset is 120/min for 60s. If a clamp changed that,
    a judge would silently get a different scenario than the card described.
    """
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    for rate, duration in [(90, 60), (60, 60), (120, 45), (80, 60)]:
        s = SimulationConfig(payments_per_minute=rate, duration_seconds=duration).sanitized()
        assert (s.payments_per_minute, s.duration_seconds) == (rate, duration)


def test_local_install_is_not_clamped(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", False)

    s = SimulationConfig(payments_per_minute=600, duration_seconds=900).sanitized()
    assert s.payments_per_minute == 600
    assert s.duration_seconds == 900


@pytest.mark.asyncio
async def test_open_demo_refuses_once_synthetic_ceiling_is_reached(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")
    monkeypatch.setattr(settings, "demo_max_synthetic_payments", 1)
    monkeypatch.setattr(settings, "demo_simulation_cooldown_seconds", 0)
    monkeypatch.setattr(engine, "_synthetic_payment_count", lambda: _select(5000))

    result = await start_simulation(SimulationConfig(duration_seconds=30))
    assert result["started"] is False
    assert "capacity" in result["reason"]


@pytest.mark.asyncio
async def test_open_demo_paces_runs_with_a_global_cooldown(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")
    monkeypatch.setattr(settings, "demo_simulation_cooldown_seconds", 30)
    monkeypatch.setattr(engine, "_last_finished_at", time.time())

    result = await start_simulation(SimulationConfig(duration_seconds=30))
    assert result["started"] is False
    assert "cooldown" in result["reason"]


@pytest.mark.asyncio
async def test_local_install_has_no_cooldown_or_ceiling(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "control_plane_open_demo", False)
    monkeypatch.setattr(engine, "_last_finished_at", time.time())

    result = await start_simulation(SimulationConfig(duration_seconds=30))
    assert result["started"] is True
    await stop_simulation()
