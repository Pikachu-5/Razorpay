"""Finish an interrupted seed_demo_baseline.py run.

Runs only the decisions + recoveries phases against opportunities/payments
already sitting in the database, skipping the (already-complete) payment
seeding loop. Not a general-purpose tool -- a one-off companion to recover
from a partial run without re-walking already-seeded events.
"""

import asyncio
import random
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from seed_demo_baseline import replay_recoveries, run_decisions  # noqa: E402


async def main() -> None:
    from sqlalchemy import select

    from app.database.models import Opportunity, Payment
    from app.database.session import session_factory

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Payment.razorpay_payment_id)
                .join(Opportunity, Opportunity.payment_id == Payment.id)
                .where(Opportunity.is_synthetic.is_(True))
            )
        ).scalars().all()
    failed_payment_ids = list(rows)
    print(f"Found {len(failed_payment_ids)} already-seeded synthetic failed payments.")

    decided, escalated = await run_decisions()
    print(f"  decided {decided} opportunities ({escalated} escalated for human review)")

    rng = random.Random(20260828)
    treated, controlled = await replay_recoveries(failed_payment_ids, rng)
    print(f"  replayed recoveries: {treated} treatment, {controlled} control")


if __name__ == "__main__":
    asyncio.run(main())
