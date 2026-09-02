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


def operating_mode_payload() -> dict[str, Any]:
    """The one description of how this install is running.

    Exported because `/api/razorpay/state` embeds the same block; it used to
    build its own two-field copy, which silently drifted out of date whenever
    this one gained a field.
    """
    settings = get_settings()
    return {
        "shadow_mode": settings.shadow_mode,
        "razorpay_mode": settings.razorpay_mode,
        "razorpay_configured": settings.razorpay_configured,
        "simulate_interventions": settings.simulate_interventions,
        # The only combination that can contact a real customer.
        "customer_side_effects_enabled": settings.razorpay_configured and not settings.shadow_mode,
        # Whether operator actions on this install are authenticated at all, and
        # if not, whether that is a declared demo posture or local development.
        # The console badges this rather than letting a reviewer assume the
        # buttons are protected.
        "control_plane_authenticated": bool(settings.control_plane_api_key),
        "control_plane_open_demo": settings.open_demo_active,
        # `shadow_mode` is a process-local override (see `set_shadow_mode`), so
        # this answer describes the worker that served the request. The deployed
        # install runs a single uvicorn worker, which is what makes that safe;
        # scaling out horizontally would need the override moved to durable
        # storage before this field could speak for the whole install.
        "shadow_mode_scope": "process",
    }


@router.get("/operating-mode")
async def operating_mode() -> dict[str, Any]:
    return operating_mode_payload()


@router.post("/shadow-mode")
async def set_shadow_mode(
    req: ShadowModeRequest, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    """Turn customer-facing execution on or off for this running process.

    Deliberately NOT written back to `.env`: a restart always returns to the
    checked-in default, so an operator can never leave live execution armed by
    forgetting to switch it back.

    The override is process-local, which is only correct because the deployed
    install runs a single uvicorn worker (`backend/startup.sh`). Running more
    than one worker or App Service instance would let workers disagree about
    the execution mode, so that change has to come with a durable, shared store
    for the override. `/api/policy/operating-mode` reports `shadow_mode_scope`
    so no caller has to infer this.
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
    return {"updated": True, **operating_mode_payload()}
