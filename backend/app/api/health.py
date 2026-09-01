from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.core.config import get_settings
from app.database.session import engine

router = APIRouter(tags=["health"])


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    settings = get_settings()
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
    }


@router.get("/readyz")
async def readyz(response: Response) -> dict[str, Any]:
    settings = get_settings()
    db_ok = False
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False
    if not db_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if db_ok else "degraded",
        "checks": {
            "database": "up" if db_ok else "down",
            "razorpay_keys": "configured" if settings.razorpay_configured else "missing",
            "webhook_secret": "configured" if settings.webhook_secret_configured else "missing",
        },
        "operating_mode": {
            "razorpay_mode": settings.razorpay_mode,
            "shadow_mode": settings.shadow_mode,
            "customer_side_effects_enabled": settings.razorpay_configured and not settings.shadow_mode,
        },
    }
