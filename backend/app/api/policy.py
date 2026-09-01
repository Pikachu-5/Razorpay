import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import OperatorPrincipal, require_operator
from app.core.config import get_settings

logger = logging.getLogger("policy")

router = APIRouter(prefix="/api/policy", tags=["policy"])


@router.get("/config")
async def policy_config() -> dict[str, Any]:
    settings = get_settings()
    return {
        "kill_switch": settings.policy_kill_switch,
        "max_amount_minor": settings.policy_max_amount_minor,
        "max_amount_inr": settings.policy_max_amount_minor / 100,
        "max_contact_attempts": settings.policy_max_contact_attempts,
        "cooldown_minutes": settings.policy_cooldown_minutes,
        "confidence_floor": settings.policy_confidence_floor,
        "min_ev_margin_minor": settings.policy_min_ev_margin_minor,
        "min_ev_margin_inr": settings.policy_min_ev_margin_minor / 100,
    }


class ShadowModeRequest(BaseModel):
    enabled: bool


def _operating_mode() -> dict[str, Any]:
    settings = get_settings()
    return {
        "shadow_mode": settings.shadow_mode,
        "razorpay_mode": settings.razorpay_mode,
        "razorpay_configured": settings.razorpay_configured,
        "simulate_interventions": settings.simulate_interventions,
        # The only combination that can contact a real customer.
        "customer_side_effects_enabled": settings.razorpay_configured and not settings.shadow_mode,
    }


@router.get("/operating-mode")
async def operating_mode() -> dict[str, Any]:
    return _operating_mode()


@router.post("/shadow-mode")
async def set_shadow_mode(
    req: ShadowModeRequest, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    """Turn customer-facing execution on or off for this running process.

    Deliberately NOT written back to `.env`: a restart always returns to the
    checked-in default, so an operator can never leave live execution armed by
    forgetting to switch it back.
    """
    settings = get_settings()

    if not req.enabled:
        # Disabling shadow mode arms real customer contact. Refuse in any
        # configuration where that would be unsafe or meaningless.
        if settings.razorpay_mode != "test":
            raise HTTPException(
                status_code=409,
                detail=f"refusing to disable shadow mode while RAZORPAY_MODE is "
                       f"'{settings.razorpay_mode}'; live execution requires a separate review",
            )
        if not settings.razorpay_configured:
            raise HTTPException(
                status_code=503,
                detail="Razorpay credentials are not configured; nothing could be sent anyway",
            )

    settings.shadow_mode = req.enabled
    logger.warning(
        "SHADOW MODE %s by operator — customer side effects %s",
        "ENABLED" if req.enabled else "DISABLED",
        "blocked" if req.enabled else "ARMED for real payments",
    )
    return {"updated": True, **_operating_mode()}
