from __future__ import annotations

from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.database.models import InterventionRecord, Opportunity, Payment, SubscriptionState
from app.database.session import session_factory
from app.events.razorpay_lifecycle import (
    process_downtime_event,
    process_invoice_event,
    process_order_event,
    process_payment_link_event,
    process_subscription_event,
)
from app.integrations.razorpay.client import get_client


async def reconcile_open_state(limit: int = 25) -> dict[str, Any]:
    """Repair missed webhook state using bounded Razorpay fetches."""
    client = await get_client()
    source = get_settings().razorpay_source
    summary: dict[str, Any] = {
        "payment_links": 0, "orders": 0, "subscriptions": 0, "downtimes": 0, "errors": [],
    }

    async with session_factory() as session:
        link_ids = list((await session.execute(
            select(InterventionRecord.razorpay_reference)
            .where(InterventionRecord.razorpay_reference.like("plink_%"))
            .where(InterventionRecord.status == "executed")
            .order_by(InterventionRecord.created_at.desc()).limit(limit)
        )).scalars().all())
        order_ids = list(dict.fromkeys((await session.execute(
            select(Payment.order_id)
            .join(Opportunity, Opportunity.payment_id == Payment.id)
            .where(Payment.order_id.is_not(None), Payment.is_synthetic.is_(False))
            .where(Opportunity.status.in_(("open", "intervention_pending", "control_holdout", "shadow_observation")))
            .limit(limit)
        )).scalars().all()))
        subscription_ids = list((await session.execute(
            select(SubscriptionState.id)
            .where(SubscriptionState.status.in_(("pending", "halted", "active")))
            .limit(limit)
        )).scalars().all())

    for link_id in link_ids:
        try:
            link = await client.fetch_payment_link(str(link_id))
            await process_payment_link_event(
                f"payment_link.{link.get('status', 'updated')}",
                {"payment_link": {"entity": link}}, source,
            )
            summary["payment_links"] += 1
        except Exception as exc:
            summary["errors"].append({"entity": str(link_id), "error": type(exc).__name__})

    for order_id in order_ids:
        try:
            order = await client.fetch_order(str(order_id))
            payments = await client.fetch_order_payments(str(order_id))
            captured = next((p for p in payments if p.get("status") in {"captured", "authorized"}), None)
            await process_order_event(
                {"order": {"entity": order}, "payment": {"entity": captured} if captured else {}}, source
            )
            summary["orders"] += 1
        except Exception as exc:
            summary["errors"].append({"entity": str(order_id), "error": type(exc).__name__})

    for subscription_id in subscription_ids:
        try:
            subscription = await client.fetch_subscription(str(subscription_id))
            await process_subscription_event(
                f"subscription.{subscription.get('status', 'updated')}",
                {"subscription": {"entity": subscription}}, source,
            )
            for invoice in await client.fetch_subscription_invoices(str(subscription_id)):
                await process_invoice_event(
                    f"invoice.{invoice.get('status', 'updated')}",
                    {"invoice": {"entity": invoice}}, source,
                )
            summary["subscriptions"] += 1
        except Exception as exc:
            summary["errors"].append({"entity": str(subscription_id), "error": type(exc).__name__})

    try:
        for downtime in (await client.fetch_downtimes())[:limit]:
            await process_downtime_event(
                f"payment.downtime.{downtime.get('status', 'updated')}",
                {"payment.downtime": {"entity": downtime}}, source,
            )
            summary["downtimes"] += 1
    except Exception as exc:
        summary["errors"].append({"entity": "downtimes", "error": type(exc).__name__})

    summary["ok"] = not summary["errors"]
    return summary
