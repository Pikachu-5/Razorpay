from decimal import Decimal

import pytest

from app.api.metrics import calculate_experiment_results
from app.api.ml import _write_promoted_pointer

pytestmark = pytest.mark.asyncio


async def test_healthz(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "revenue-recovery-control-plane"


async def test_readyz_reports_db_and_keys(client):
    resp = await client.get("/readyz")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["checks"]) == {"database", "razorpay_keys", "webhook_secret"}
    assert body["checks"]["database"] in {"up", "down"}


async def test_policy_config_endpoint(client):
    resp = await client.get("/api/policy/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "kill_switch" in body
    assert "max_amount_minor" in body
    assert "max_contact_attempts" in body
    assert "cooldown_minutes" in body
    assert "confidence_floor" in body


async def test_model_card_endpoint(client):
    resp = await client.get("/api/ml/model-card")
    assert resp.status_code == 200
    body = resp.json()
    assert "version" in body
    assert "model_type" in body


async def test_experiment_metrics_endpoint(client):
    resp = await client.get("/api/metrics/experiment")
    assert resp.status_code == 200
    body = resp.json()
    assert "treatment" in body
    assert "control" in body
    assert "causal_lift_pct" in body
    assert "incremental_revenue_minor" in body
    assert "p_value" in body


async def test_model_comparison_and_promote_endpoints(client):
    resp = await client.get("/api/ml/comparison")
    assert resp.status_code == 200
    body = resp.json()
    assert "champion" in body or "active_version" in body

    # Test promotion
    resp_promote = await client.post("/api/ml/promote", json={"version": "v2", "force": True})
    assert resp_promote.status_code == 200
    promote_body = resp_promote.json()
    assert promote_body["promoted"] is True
    assert promote_body["version"] == "v2"


async def test_experiment_results_preserve_negative_incremental_revenue():
    result = calculate_experiment_results(
        treatment_total=100,
        treatment_recovered=10,
        treatment_amount_sum=1_000_000,
        treatment_recovered_minor=100_000,
        control_total=100,
        control_recovered=20,
        control_recovered_minor=200_000,
    )
    assert result["delta_conversion_rate"] == -0.1
    assert result["incremental_revenue_minor"] == -100_000
    assert result["causal_lift_pct"] == -50.0


async def test_experiment_results_accept_postgres_decimal_aggregates():
    result = calculate_experiment_results(
        treatment_total=Decimal("10"), treatment_recovered=Decimal("5"),
        treatment_amount_sum=Decimal("100000"), treatment_recovered_minor=Decimal("50000"),
        control_total=Decimal("10"), control_recovered=Decimal("2"),
        control_recovered_minor=Decimal("20000"),
    )
    assert result["incremental_revenue_minor"] == 30_000


async def test_promoted_pointer_write_is_complete_json(tmp_path):
    _write_promoted_pointer(tmp_path, {"artifact": "recovery_model_v99.pkl", "version": "v99"})
    import json

    assert json.loads((tmp_path / "PROMOTED.json").read_text()) == {
        "artifact": "recovery_model_v99.pkl",
        "version": "v99",
    }
    assert not list(tmp_path.glob(".PROMOTED-*.tmp"))
