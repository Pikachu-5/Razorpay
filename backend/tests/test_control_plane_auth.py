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
