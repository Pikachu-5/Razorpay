import pytest

from app.core.config import get_settings

pytestmark = pytest.mark.asyncio


async def test_mutating_control_plane_endpoints_require_key_outside_demo(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "operator-secret")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "admin-secret")

    denied = await client.post("/api/incidents/scan")
    assert denied.status_code == 401

    allowed = await client.post(
        "/api/incidents/scan", headers={"X-Control-Plane-Key": "operator-secret"}
    )
    assert allowed.status_code == 200


async def test_force_promotion_requires_distinct_admin_key(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "operator-secret")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "admin-secret")

    denied = await client.post(
        "/api/ml/promote",
        json={"version": "v2", "force": True},
        headers={"X-Control-Plane-Key": "operator-secret"},
    )
    assert denied.status_code == 403

    allowed = await client.post(
        "/api/ml/promote",
        json={"version": "v2", "force": True},
        headers={"X-Control-Plane-Key": "admin-secret"},
    )
    assert allowed.status_code == 200


async def test_production_without_any_key_fails_closed(client, monkeypatch):
    """The default posture. An unset APP_ENV must never mean "no auth"."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", False)

    refused = await client.post("/api/incidents/scan")
    assert refused.status_code == 503


async def test_open_demo_allows_operator_actions_without_a_key(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    allowed = await client.post("/api/incidents/scan")
    assert allowed.status_code == 200


async def test_open_demo_never_grants_admin(client, monkeypatch):
    """Unauthenticated by design is not the same as trusted."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    forced = await client.post("/api/ml/promote", json={"version": "v2", "force": True})
    assert forced.status_code == 403

    # Promotion is keyed for an open demo whether or not it is forced: the
    # pointer it rewrites governs every later decision, for every visitor.
    ordinary = await client.post("/api/ml/promote", json={"version": "v2"})
    assert ordinary.status_code == 403

    # An action confined to the demo's own synthetic data stays open.
    assert (await client.post("/api/incidents/scan")).status_code == 200


async def test_open_demo_is_refused_outside_razorpay_test_mode(client, monkeypatch):
    """The flag cannot be carried into a live install by copying an env file."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "live")

    refused = await client.post("/api/incidents/scan")
    assert refused.status_code == 503


async def test_operating_mode_reports_control_plane_posture(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    body = (await client.get("/api/policy/operating-mode")).json()
    assert body["control_plane_authenticated"] is False
    assert body["control_plane_open_demo"] is True
    assert body["shadow_mode_scope"] == "process"


async def test_open_demo_cannot_promote_or_reconcile(client, monkeypatch):
    """Actions that reach outside the demo sandbox stay behind a key."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_admin_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", True)
    monkeypatch.setattr(settings, "razorpay_mode", "test")

    assert (await client.post("/api/ml/promote", json={"version": "v2"})).status_code == 403
    assert (await client.post("/api/reconciliation/run")).status_code == 403

    # The self-contained actions stay available to the same anonymous caller.
    assert (await client.post("/api/incidents/scan")).status_code == 200


async def test_local_operator_may_still_promote_and_reconcile(client, monkeypatch):
    """The demo restriction must not leak into a normal keyless local install."""
    settings = get_settings()
    monkeypatch.setattr(settings, "app_env", "dev")
    monkeypatch.setattr(settings, "control_plane_api_key", "")
    monkeypatch.setattr(settings, "control_plane_open_demo", False)

    assert (await client.post("/api/ml/promote", json={"version": "v2"})).status_code == 200
