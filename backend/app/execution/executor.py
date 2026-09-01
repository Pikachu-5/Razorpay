from __future__ import annotations

import logging
import uuid as uuid_lib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.database.models import InterventionRecord
from app.database.session import session_factory
from app.integrations.razorpay.client import get_client
from app.integrations.razorpay.errors import RazorpayError

logger = logging.getLogger("executor")

RECOVERY_LINK_DESCRIPTION = "Complete your pending payment"


@dataclass(frozen=True)
class ExecutionResult:
    action: str
    status: str
    detail: dict[str, Any]


async def _is_synthetic_opportunity(opportunity_id: UUID) -> bool:
    from app.database.models import Opportunity

    async with session_factory() as session:
        return bool(
            (
                await session.execute(
                    select(Opportunity.is_synthetic).where(Opportunity.id == opportunity_id)
                )
            ).scalar_one_or_none()
        )


async def _execute_payment_link(
    opportunity_id: UUID,
    amount_minor: int,
    customer_name: str | None,
    customer_email: str | None,
    customer_contact: str | None,
) -> ExecutionResult:
    from app.core.config import get_settings

    settings = get_settings()

    # Invented traffic NEVER reaches Razorpay, in any mode. The simulator can
    # produce hundreds of failures a minute against customers who do not exist;
    # turning shadow mode off must not turn that into hundreds of real payment
    # links. Synthetic opportunities therefore always take the local path, and
    # only genuine payments are governed by shadow_mode below.
    is_synthetic = await _is_synthetic_opportunity(opportunity_id)

    if is_synthetic or settings.shadow_mode:
        # Simulated traffic may still produce an attributable link so the
        # recovery/verification/experiment stages have something to measure.
        # This is strictly local: no Razorpay call, and never for real payments.
        if settings.simulate_interventions and is_synthetic:
            simulated_link_id = f"plink_sim_{uuid_lib.uuid4().hex[:14]}"
            return ExecutionResult(
                action="send_payment_link",
                status="executed",
                detail={
                    "razorpay_payment_link_id": simulated_link_id,
                    "short_url": f"https://rzp.io/i/{simulated_link_id}",
                    "amount_minor": amount_minor,
                    "link_status": "created",
                    "notification": "simulated",
                    "reference": f"opp_{opportunity_id}",
                    "live_api_call": False,
                    "simulated": True,
                    "note": "simulated payment link for synthetic demo traffic; no Razorpay call was made",
                },
            )
        return ExecutionResult(
            action="send_payment_link", status="shadowed",
            detail={
                "live_api_call": False,
                "note": (
                    "synthetic opportunity: customer contact is never sent for simulated traffic"
                    if is_synthetic
                    else "shadow mode blocked customer contact"
                ),
            },
        )
    from app.events.razorpay_lifecycle import active_downtime_methods

    unavailable = await active_downtime_methods()
    methods = {name: name not in unavailable for name in ("card", "netbanking", "upi", "wallet")}
    if not any(methods.values()):
        return ExecutionResult(
            action="send_payment_link", status="failed",
            detail={"live_api_call": False, "error": "all supported recovery methods are in downtime"},
        )
    client = await get_client()
    reference = f"opp_{opportunity_id}"
    try:
        link = await client.create_payment_link(
            amount_minor=amount_minor,
            reference_id=reference,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_contact=customer_contact,
            description=RECOVERY_LINK_DESCRIPTION,
            notes={"source": "recovery_control_plane", "opportunity_id": str(opportunity_id)},
            expire_by=int((datetime.now(timezone.utc) + timedelta(hours=48)).timestamp()),
            reminder_enable=False,
            options={"checkout": {"method": methods}},
        )
    except RazorpayError as exc:
        return ExecutionResult(
            action="send_payment_link", status="failed",
            detail={"error": str(exc), "reference": reference},
        )

    notify_status = "skipped"
    if link.get("id") and (customer_contact or customer_email):
        medium = "sms" if customer_contact else "email"
        try:
            await client.notify_payment_link(str(link["id"]), medium)
            notify_status = f"notified_via_{medium}"
        except RazorpayError as exc:
            notify_status = f"notify_failed: {exc}"

    return ExecutionResult(
        action="send_payment_link",
        status="executed",
        detail={
            "razorpay_payment_link_id": link.get("id"),
            "short_url": link.get("short_url"),
            "amount_minor": link.get("amount"),
            "link_status": link.get("status"),
            "notification": notify_status,
            "reference": reference,
            "live_api_call": True,
        },
    )


async def record_intervention(
    opportunity_id: UUID,
    result: ExecutionResult,
    extra_payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
) -> UUID:
    payload: dict[str, Any] = dict(result.detail)
    if extra_payload:
        payload.update(extra_payload)
    record = InterventionRecord(
        id=uuid_lib.uuid4(),
        opportunity_id=opportunity_id,
        action=result.action,
        status=result.status,
        idempotency_key=idempotency_key or f"{opportunity_id}:{result.action}:{uuid_lib.uuid4()}",
        razorpay_reference=result.detail.get("razorpay_payment_link_id"),
        payload=payload,
    )
    async with session_factory() as session:
        session.add(record)
        await session.commit()
        return record.id


async def _claim_intervention(opportunity_id: UUID, action: str) -> tuple[UUID, str] | None:
    """Durably reserve an external side effect before performing it.

    The unique key is intentionally action-specific: a future, explicitly
    authorized recovery attempt may use a different action, but two workers
    can never send the same action for one opportunity.
    """
    key = f"{opportunity_id}:{action}"
    record = InterventionRecord(
        id=uuid_lib.uuid4(),
        opportunity_id=opportunity_id,
        action=action,
        status="executing",
        idempotency_key=key,
        payload={"idempotency_key": key, "execution_state": "claimed"},
    )
    async with session_factory() as session:
        session.add(record)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return None
    return record.id, key


async def _finalize_claimed_intervention(record_id: UUID, result: ExecutionResult) -> None:
    async with session_factory() as session:
        record = await session.get(InterventionRecord, record_id)
        if record is None:
            logger.error("claimed intervention %s disappeared before finalization", record_id)
            return
        record.status = result.status
        record.razorpay_reference = result.detail.get("razorpay_payment_link_id")
        record.payload = {**result.detail, "idempotency_key": record.idempotency_key}
        await session.commit()


async def find_open_intervention_by_reference(reference: str) -> InterventionRecord | None:
    async with session_factory() as session:
        result = await session.execute(
            select(InterventionRecord)
            .where(InterventionRecord.razorpay_reference == reference)
            .where(InterventionRecord.status == "executed")
            .order_by(InterventionRecord.created_at.desc())
        )
        return result.scalar_one_or_none()


async def execute_action(
    action: str,
    *,
    opportunity_id: UUID,
    amount_minor: int,
    customer_name: str | None = None,
    customer_email: str | None = None,
    customer_contact: str | None = None,
    rationale: str | None = None,
    subscription_id: str | None = None,
    reason: str | None = None,
) -> ExecutionResult:
    claim = await _claim_intervention(opportunity_id, action)
    if claim is None:
        return ExecutionResult(
            action=action,
            status="duplicate",
            detail={"error": "an equivalent intervention is already claimed", "live_api_call": False},
        )
    record_id, _ = claim
    if action == "send_payment_link":
        result = await _execute_payment_link(
            opportunity_id, amount_minor, customer_name, customer_email, customer_contact
        )
    elif action == "prompt_card_change":
        from app.core.config import get_settings

        settings = get_settings()
        if not subscription_id:
            result = ExecutionResult(
                action=action, status="rejected",
                detail={"error": "subscription_id required for card-update checkout", "live_api_call": False},
            )
        else:
            result = ExecutionResult(
                action=action, status="customer_action_required",
                detail={
                    "live_api_call": False,
                    "checkout_options": {
                        "key": settings.razorpay_key_id,
                        "subscription_id": subscription_id,
                        "subscription_card_change": True,
                    },
                    "note": "open Razorpay Checkout to update the subscription payment instrument",
                },
            )
    elif action == "wait_for_native_retry":
        result = ExecutionResult(
            action=action, status="deferred",
            detail={"channel": "razorpay_native_subscription_retry", "note": rationale or "", "live_api_call": False},
        )
    elif action == "escalate_to_human":
        result = ExecutionResult(
            action=action,
            status="executed",
            detail={
                "channel": "internal_review_queue",
                "note": rationale or "",
                "customer_contacted": False,
                "reason": reason or "queued for operator review",
                "live_api_call": False,
            },
        )
    elif action in ("send_reminder", "do_nothing"):
        result = ExecutionResult(
            action=action,
            status="executed" if action != "do_nothing" else "closed",
            detail={
                "channel": "internal_notification_log" if action == "send_reminder" else None,
                "note": rationale or "",
                "live_api_call": False,
            },
        )
    else:
        result = ExecutionResult(action=action, status="rejected",
                                 detail={"error": "action not supported by executor"})

    await _finalize_claimed_intervention(record_id, result)
    return result
