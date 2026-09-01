from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import OperatorPrincipal, require_operator
from app.core.config import get_settings
from app.database.models import (
    InvoiceState,
    PaymentDowntime,
    PaymentLinkState,
    RazorpayOrder,
    RevenueAdjustment,
    SubscriptionState,
)
from app.database.session import get_session
from app.events.razorpay_lifecycle import process_payment_link_event
from app.integrations.razorpay.client import get_client

router = APIRouter(prefix="/api/razorpay", tags=["razorpay-state"])


@router.get("/state")
async def state(
    limit: int = Query(default=25, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    orders = (await session.execute(select(RazorpayOrder).order_by(desc(RazorpayOrder.updated_at)).limit(limit))).scalars().all()
    links = (await session.execute(select(PaymentLinkState).order_by(desc(PaymentLinkState.updated_at)).limit(limit))).scalars().all()
    downtimes = (await session.execute(select(PaymentDowntime).order_by(desc(PaymentDowntime.updated_at)).limit(limit))).scalars().all()
    subscriptions = (await session.execute(select(SubscriptionState).order_by(desc(SubscriptionState.updated_at)).limit(limit))).scalars().all()
    invoices = (await session.execute(select(InvoiceState).order_by(desc(InvoiceState.updated_at)).limit(limit))).scalars().all()
    adjustments = (await session.execute(select(RevenueAdjustment).order_by(desc(RevenueAdjustment.occurred_at)).limit(limit))).scalars().all()
    return {
        "operating_mode": {
            "razorpay_mode": get_settings().razorpay_mode,
            "shadow_mode": get_settings().shadow_mode,
        },
        "orders": [{"id": x.id, "status": x.status, "attempts": x.attempts, "amount_minor": x.amount_minor, "amount_paid_minor": x.amount_paid_minor, "source": x.source} for x in orders],
        "payment_links": [{"id": x.id, "status": x.status, "amount_minor": x.amount_minor, "amount_paid_minor": x.amount_paid_minor, "short_url": x.short_url, "source": x.source} for x in links],
        "downtimes": [{"id": x.id, "status": x.status, "method": x.method, "severity": x.severity, "instrument": x.instrument, "source": x.source} for x in downtimes],
        "subscriptions": [{"id": x.id, "status": x.status, "plan_id": x.plan_id, "paid_count": x.paid_count, "remaining_count": x.remaining_count, "source": x.source} for x in subscriptions],
        "invoices": [{"id": x.id, "status": x.status, "subscription_id": x.subscription_id, "amount_paid_minor": x.amount_paid_minor, "amount_due_minor": x.amount_due_minor, "source": x.source} for x in invoices],
        "revenue_adjustments": [{"external_id": x.external_id, "kind": x.kind, "status": x.status, "amount_minor": x.amount_minor, "payment_id": x.razorpay_payment_id, "source": x.source} for x in adjustments],
    }


def _require_side_effects() -> None:
    settings = get_settings()
    if settings.shadow_mode:
        raise HTTPException(status_code=409, detail="shadow mode blocks customer-facing Razorpay actions")
    if not settings.razorpay_configured:
        raise HTTPException(status_code=503, detail="Razorpay credentials are not configured")


@router.post("/payment-links/{link_id}/notify")
async def notify_link(
    link_id: str,
    medium: str = Query(default="sms", pattern="^(sms|email)$"),
    _: OperatorPrincipal = Depends(require_operator),
) -> dict[str, Any]:
    _require_side_effects()
    result = await (await get_client()).notify_payment_link(link_id, medium)
    return {"link_id": link_id, "medium": medium, "result": result}


@router.post("/payment-links/{link_id}/cancel")
async def cancel_link(
    link_id: str, _: OperatorPrincipal = Depends(require_operator)
) -> dict[str, Any]:
    _require_side_effects()
    link = await (await get_client()).cancel_payment_link(link_id)
    await process_payment_link_event(
        "payment_link.cancelled", {"payment_link": {"entity": link}}, get_settings().razorpay_source
    )
    return {"link_id": link_id, "status": link.get("status", "cancelled")}
