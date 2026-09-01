"""Prove one real end-to-end recovery against Razorpay Test Mode.

This is the counterpart to the simulator.  The simulator shows the *product*
working at volume with invented data; this script shows the *integration*
working for real: it creates a genuine Razorpay Test Mode payment link for one
real opportunity, waits for you to pay it with a test card, then runs the same
verification path a production webhook would trigger.

Nothing here needs a registered webhook.  It polls the Razorpay API instead,
which is exactly what `reconcile_open_state` does when a webhook is missed.

Safety
------
* Refuses to run unless RAZORPAY_MODE=test.
* Creates exactly ONE payment link per run, for ONE opportunity.
* Test Mode money is not real money.

Usage
-----
    .venv\\Scripts\\python scripts\\live_recovery_demo.py --dry-run
    .venv\\Scripts\\python scripts\\live_recovery_demo.py
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid as uuid_lib
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.agents.verification import verify_payment_link_paid  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.database.models import Customer, InterventionRecord, Opportunity, Payment  # noqa: E402
from app.database.session import session_factory  # noqa: E402
from app.integrations.razorpay.client import get_client  # noqa: E402
from app.integrations.razorpay.errors import RazorpayError  # noqa: E402

ELIGIBLE = ("open", "shadow_observation", "escalated", "control_holdout", "intervention_pending")
TEST_CARD = "4111 1111 1111 1111  ·  any future expiry  ·  any CVV"


async def pick_opportunity() -> tuple[Opportunity, Payment, Customer | None] | None:
    """Highest-value real (non-synthetic) unresolved opportunity."""
    async with session_factory() as session:
        row = (
            await session.execute(
                select(Opportunity, Payment)
                .join(Payment, Opportunity.payment_id == Payment.id)
                .where(
                    Opportunity.is_synthetic.is_(False),
                    Opportunity.status.in_(ELIGIBLE),
                )
                .order_by(Opportunity.amount_minor.desc())
                .limit(1)
            )
        ).first()
        if row is None:
            return None
        opportunity, payment = row
        customer = None
        if payment.customer_id:
            customer = await session.get(Customer, payment.customer_id)
        return opportunity, payment, customer


async def wait_for_payment(link_id: str, timeout_seconds: int) -> dict | None:
    """Poll the Razorpay API until the link is paid, or give up."""
    client = await get_client()
    deadline = datetime.now(timezone.utc) + timedelta(seconds=timeout_seconds)
    last_status = None
    while datetime.now(timezone.utc) < deadline:
        try:
            link = await client.fetch_payment_link(link_id)
        except RazorpayError as exc:
            print(f"  ! poll failed: {exc}")
            await asyncio.sleep(5)
            continue
        status = link.get("status")
        if status != last_status:
            print(f"  link status: {status}")
            last_status = status
        if status == "paid":
            return link
        await asyncio.sleep(5)
    return None


async def main(dry_run: bool, timeout_seconds: int) -> int:
    settings = get_settings()

    if settings.razorpay_mode != "test":
        print(f"REFUSING: RAZORPAY_MODE is '{settings.razorpay_mode}', not 'test'.")
        print("This script only ever runs against Test Mode.")
        return 2
    if not settings.razorpay_configured:
        print("FAIL: RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET missing in .env")
        return 2

    picked = await pick_opportunity()
    if picked is None:
        print("No eligible real opportunity found.")
        print("Seed one first:  .venv\\Scripts\\python scripts\\seed_demo_baseline.py")
        return 1
    opportunity, payment, customer = picked

    print("Target opportunity")
    print(f"  id       : {opportunity.id}")
    print(f"  amount   : Rs {opportunity.amount_minor / 100:,.2f}")
    print(f"  status   : {opportunity.status}")
    print(f"  payment  : {payment.razorpay_payment_id} ({payment.method}/{payment.bank})")
    print(f"  customer : {(customer.email if customer else None) or 'unknown'}")
    print()

    if dry_run:
        print("DRY RUN - no Razorpay call made, no link created.")
        print("Re-run without --dry-run to create a real Test Mode payment link.")
        return 0

    client = await get_client()
    reference = f"opp_{opportunity.id}"
    print("Creating a REAL Razorpay Test Mode payment link ...")
    try:
        link = await client.create_payment_link(
            amount_minor=opportunity.amount_minor,
            reference_id=f"{reference}_{uuid_lib.uuid4().hex[:6]}",
            customer_name=customer.name if customer else None,
            customer_email=customer.email if customer else None,
            customer_contact=customer.contact if customer else None,
            description="Complete your pending payment",
            notes={"source": "live_recovery_demo", "opportunity_id": str(opportunity.id)},
            expire_by=int((datetime.now(timezone.utc) + timedelta(hours=2)).timestamp()),
            reminder_enable=False,
        )
    except RazorpayError as exc:
        print(f"FAIL: could not create payment link: {exc}")
        return 1

    link_id = str(link.get("id"))
    short_url = link.get("short_url")
    print(f"  link id  : {link_id}")
    print(f"  PAY HERE : {short_url}")
    print()

    # Record the intervention so verification can attribute the recovery,
    # exactly as the executor would in a non-shadow run.
    async with session_factory() as session:
        session.add(
            InterventionRecord(
                id=uuid_lib.uuid4(),
                opportunity_id=opportunity.id,
                action="send_payment_link",
                status="executed",
                idempotency_key=f"{opportunity.id}:send_payment_link:{link_id}",
                razorpay_reference=link_id,
                payload={
                    "razorpay_payment_link_id": link_id,
                    "short_url": short_url,
                    "amount_minor": opportunity.amount_minor,
                    "live_api_call": True,
                    "source": "live_recovery_demo",
                },
            )
        )
        target = await session.get(Opportunity, opportunity.id)
        if target is not None:
            target.status = "intervention_pending"
            target.best_action = "send_payment_link"
        await session.commit()

    print(f"Open the link above and pay with a Razorpay test card:\n  {TEST_CARD}")
    print(f"Waiting up to {timeout_seconds}s for payment ...")
    paid_link = await wait_for_payment(link_id, timeout_seconds)

    if paid_link is None:
        print()
        print("Timed out. The link is still live - pay it any time, then run:")
        print("  Invoke-RestMethod -Method Post http://localhost:8000/api/reconciliation/run")
        print("which polls Razorpay and attributes the recovery the same way.")
        return 1

    payments = paid_link.get("payments") or []
    entity = payments[0] if payments else {"id": None, "amount": opportunity.amount_minor}
    event = await verify_payment_link_paid(
        link_id,
        {
            "id": entity.get("payment_id") or entity.get("id"),
            "amount": entity.get("amount") or opportunity.amount_minor,
            "currency": "INR",
            "status": "captured",
        },
    )

    print()
    if event is None:
        print("Link was paid, but verification did not attribute it.")
        print("Check that the opportunity is still in intervention_pending.")
        return 1

    print("RECOVERED.")
    print(f"  opportunity {opportunity.id} -> recovered_intervention")
    print(f"  amount      Rs {opportunity.amount_minor / 100:,.2f}")
    print()
    print("This is a real Razorpay API object, paid through Razorpay's real")
    print("Test Mode checkout, verified by your own attribution logic.")
    print("Check the Overview tab: net recovered has moved.")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="show the target opportunity without creating a link")
    parser.add_argument("--timeout", type=int, default=300,
                        help="seconds to wait for the payment (default: 300)")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.dry_run, args.timeout)))
