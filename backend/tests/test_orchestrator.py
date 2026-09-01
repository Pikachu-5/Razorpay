import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.agents.orchestrator import decide_opportunity
from app.agents.verification import verify_payment_link_paid
from app.database.models import DecisionAudit, InterventionRecord, Opportunity
from app.database.session import session_factory
from app.events.processor import process_payment_event, sweep_expired_opportunities
from app.ml.predictor import Prediction


class _StubLinkClient:
    def __init__(self):
        self.created_links = []

    async def create_payment_link(self, **kwargs):
        self.created_links.append(kwargs)
        return {"id": f"plink_TEST{len(self.created_links)}",
                "short_url": "https://rzp.io/i/testlink", "amount": kwargs["amount_minor"],
                "status": "created"}

    async def notify_payment_link(self, link_id, medium):
        return {"success": True}


async def seed_failed_opportunity(payment_id="pay_ORCH01", amount=420000) -> str:
    await process_payment_event("payment.failed", {
        "id": payment_id,
        "amount": amount,
        "currency": "INR",
        "status": "failed",
        "method": "upi",
        "bank": "HDFC",
        "error_code": "BAD_REQUEST_ERROR",
        "error_reason": "timeout",
        "email": "orch@example.com",
        "contact": "+919555000111",
        "created_at": int(datetime.now(timezone.utc).timestamp()) - 120,
    })
    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity))).scalars().one()
        return str(opp.id)


@pytest.mark.asyncio
async def test_full_decision_chain_executes_link(monkeypatch):
    opportunity_id = await seed_failed_opportunity()

    import app.execution.executor as executor_module

    stub = _StubLinkClient()
    monkeypatch.setattr(executor_module, "get_client", _async_returning(stub))

    import app.agents.revenue as revenue
    monkeypatch.setattr(
        revenue, "get_prediction_service",
        lambda: _StubPredictor({"send_payment_link": 0.78, "send_reminder": 0.40}),
    )

    result = await decide_opportunity(__import__("uuid").UUID(opportunity_id))
    assert result is not None
    assert result["action"] == "send_payment_link"
    assert result["allowed"] is True
    assert result["execution"]["status"] == "executed"
    assert result["execution"]["live_api_call"] is True
    assert stub.created_links[0]["reference_id"] == f"opp_{opportunity_id}"

    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity))).scalars().one()
        assert opp.status == "intervention_pending"
        assert opp.best_action == "send_payment_link"
        assert opp.contact_attempts == 1

        audits = (await session.execute(select(DecisionAudit))).scalars().all()
        assert len(audits) == 1
        audit = audits[0]
        assert audit.diagnosis["classification"] == "transient_failure"
        assert audit.predictions["send_payment_link"]["probability"] == 0.78
        assert audit.policy_decision["allowed"] is True
        assert audit.verified_outcome == "pending"

        interventions = (await session.execute(select(InterventionRecord))).scalars().all()
        assert len(interventions) == 1
        assert interventions[0].razorpay_reference == "plink_TEST1"


@pytest.mark.asyncio
async def test_low_value_case_results_in_do_nothing(monkeypatch):
    opportunity_id = await seed_failed_opportunity("pay_ORCH_SMALL", amount=2000)

    import app.execution.executor as executor_module
    monkeypatch.setattr(executor_module, "get_client", _async_returning(_StubLinkClient()))

    import app.agents.revenue as revenue
    monkeypatch.setattr(
        revenue, "get_prediction_service",
        lambda: _StubPredictor({"send_payment_link": 0.10, "send_reminder": 0.05}),
    )

    result = await decide_opportunity(__import__("uuid").UUID(opportunity_id))
    assert result["action"] == "do_nothing"

    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity))).scalars().one()
        assert opp.status == "closed_not_viable"
        audits = (await session.execute(select(DecisionAudit))).scalars().all()
        assert audits[0].verified_outcome == "do_nothing"


@pytest.mark.asyncio
async def test_verification_of_paid_link(monkeypatch):
    opportunity_id = await seed_failed_opportunity("pay_VERIF01")

    # This test proves intervention attribution, not control-group behavior.
    # Make the test independent of the deterministic assignment hash.
    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity).where(Opportunity.id == __import__("uuid").UUID(opportunity_id)))).scalar_one()
        opp.experiment_group = "treatment"
        await session.commit()

    import app.execution.executor as executor_module
    monkeypatch.setattr(executor_module, "get_client", _async_returning(_StubLinkClient()))
    import app.agents.revenue as revenue
    monkeypatch.setattr(
        revenue, "get_prediction_service",
        lambda: _StubPredictor({"send_payment_link": 0.85, "send_reminder": 0.30}),
    )
    await decide_opportunity(__import__("uuid").UUID(opportunity_id))

    event = await verify_payment_link_paid(
        "plink_TEST1", {"id": "pay_NEWPAID1", "amount": 420000}
    )
    assert event is not None
    assert event.data["resolution"] == "recovered_intervention"
    assert event.data["amount_minor"] == 420000

    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity))).scalars().one()
        assert opp.status == "recovered_intervention"
        audit = (await session.execute(select(DecisionAudit))).scalars().one()
        assert audit.verified_outcome == "verified_recovered"
        assert audit.recovered_amount_minor == 420000


@pytest.mark.asyncio
async def test_sweeper_closes_stale_pending():
    opportunity_id = await seed_failed_opportunity("pay_STALE01")
    oid = __import__("uuid").UUID(opportunity_id)
    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity).where(Opportunity.id == oid))).scalar_one()
        opp.status = "intervention_pending"
        opp.window_ends_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await session.commit()

    closed = await sweep_expired_opportunities()
    assert closed >= 1

    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity).where(Opportunity.id == oid))).scalar_one()
        assert opp.status == "closed_no_response"
        audit = (
            await session.execute(
                select(DecisionAudit).order_by(DecisionAudit.created_at.desc()).limit(1)
            )
        ).scalars().first()
        if audit and audit.opportunity_id == oid:
            assert audit.verified_outcome == "no_response"


@pytest.mark.asyncio
async def test_concurrent_decisions_create_only_one_external_intervention(monkeypatch):
    opportunity_id = await seed_failed_opportunity("pay_ORCH_RACE")
    oid = __import__("uuid").UUID(opportunity_id)
    async with session_factory() as session:
        opp = (await session.execute(select(Opportunity).where(Opportunity.id == oid))).scalar_one()
        opp.experiment_group = "treatment"
        await session.commit()

    class _SlowLinkClient(_StubLinkClient):
        async def create_payment_link(self, **kwargs):
            await asyncio.sleep(0.05)
            return await super().create_payment_link(**kwargs)

    import app.agents.revenue as revenue
    import app.execution.executor as executor_module

    stub = _SlowLinkClient()
    monkeypatch.setattr(executor_module, "get_client", _async_returning(stub))
    monkeypatch.setattr(
        revenue, "get_prediction_service",
        lambda: _StubPredictor({"send_payment_link": 0.90, "send_reminder": 0.10}),
    )

    first, second = await asyncio.gather(
        decide_opportunity(oid, trigger="race.one"),
        decide_opportunity(oid, trigger="race.two"),
    )
    assert sum(result is not None for result in (first, second)) == 1
    assert len(stub.created_links) == 1

    async with session_factory() as session:
        records = (await session.execute(select(InterventionRecord))).scalars().all()
        assert len(records) == 1
        assert records[0].idempotency_key == f"{opportunity_id}:send_payment_link"


class _StubPredictor:
    def __init__(self, probabilities):
        self.probabilities = probabilities

    def predict(self, ctx):
        return Prediction(probabilities=self.probabilities, source="stub",
                          model_version="stub-v9", degraded=False)


def _async_returning(value):
    async def _get_client():
        return value
    return _get_client
