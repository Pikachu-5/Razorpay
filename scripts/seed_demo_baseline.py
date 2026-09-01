"""Seed a deterministic, historical payment baseline for the local demo.

Everything this script writes is flagged `is_synthetic`, because everything this
script writes is invented.  That means it is excluded from operational KPIs and
from the default causal experiment view, exactly like simulator traffic.

This matters more than it looks.  An earlier version of this seeder wrote its
payments with `is_synthetic=False`, so fabricated demo history was counted as
real-world evidence in the treatment/control experiment.  Nothing had recovered
yet, so the number was zero and the bug was invisible -- but the moment the
seeder produced recoveries it would have reported a fabricated "statistically
significant causal lift" through the one surface in the product that is supposed
to be trustworthy.

To watch the experiment machinery work, seed a baseline and tick "Include
simulated traffic" on the Evidence tab.  The UI labels that view as a
demonstration of the measurement, not as evidence of real-world lift, and that
label is the honest description of this data.

  --days 1    (default) enough traffic for Monitor and Incidents to be meaningful
  --days 30   ~2 minutes; enough volume for the experiment to clear its sample gate
"""

import argparse
import asyncio
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.agents.orchestrator import decide_opportunity  # noqa: E402
from app.events.processor import process_payment_event  # noqa: E402
from app.ml.baseline import DEFAULT_NATURAL_RECOVERY_BASELINE  # noqa: E402
from app.ml.features import error_group  # noqa: E402

SEGMENTS = [("upi", "HDFC"), ("card", "ICICI"), ("netbanking", "SBI"), ("wallet", "AXIS")]
SLOTS_PER_HOUR = 3

# Recovery rates for the seeded history, per failure class.
#
# The control rates are the natural-recovery baseline the model was actually
# trained against (`app.ml.baseline`), so the seeded history has the same shape
# as the data the ranker learned from -- a timeout resolves itself far more
# often than a dead card, and the console should show that rather than one flat
# rate pretending every failure is alike. Treatment adds a uniform uplift.
#
# These are assumptions baked into demo data, not measurements, which is
# precisely why everything this script writes carries `is_synthetic`.
TREATMENT_UPLIFT = 0.11

# Failure reasons weighted to published Indian merchant mixes: bank/server
# timeouts and network faults dominate at roughly 35-45% plus 10-15%, wrong PIN
# or failed authentication runs 20-30%, insufficient balance 15-25%, and blocked
# or deactivated instruments 5-10%. Seeding a single reason made every failure
# look identical, which is the opposite of the point the product makes.
FAILURE_REASONS = [
    ("timeout", 22), ("gateway_error", 12), ("network_error", 10),
    ("user_authentication_failed", 14), ("authentication_required", 10),
    ("insufficient_funds", 20),
    ("card_expired", 5), ("card_blocked", 4), ("invalid_vpa", 3),
]
_REASON_NAMES = [name for name, _ in FAILURE_REASONS]
_REASON_WEIGHTS = [weight for _, weight in FAILURE_REASONS]

# Most demo payments are ordinary retail tickets, but a realistic merchant also
# takes occasional large ones. The tail matters: amounts above the policy value
# cap are exactly what the control plane refuses to decide automatically, so
# without it the "needs a person" queue is always empty and the escalation path
# is never demonstrated.
AMOUNT_TYPICAL = (100_000, 1_500_000)
AMOUNT_LARGE = (2_600_000, 8_000_000)
LARGE_TICKET_SHARE = 0.07

# The experiment splits opportunities 80/20 into treatment/control, and
# `/api/metrics/experiment` refuses to claim a lift below this many per group.
MIN_PER_GROUP = 100
CONTROL_SHARE = 0.2


async def run_decisions() -> tuple[int, int]:
    """Run the real decision pipeline over the seeded opportunities.

    The seeder writes events directly rather than through the webhook consumer,
    so nothing would otherwise be diagnosed, scored or policy-checked -- the
    console would open on a queue of opportunities with no action, no expected
    value and no audit trail, which is the least interesting version of this
    product. This walks them through the same orchestrator production uses.
    """
    from sqlalchemy import select

    from app.database.models import Opportunity
    from app.database.session import session_factory

    async with session_factory() as session:
        ids = (
            await session.execute(
                select(Opportunity.id).where(Opportunity.status == "open")
            )
        ).scalars().all()

    decided = escalated = 0
    for opportunity_id in ids:
        result = await decide_opportunity(opportunity_id, trigger="demo_baseline_seed")
        if result is None:
            continue
        decided += 1
        if result.get("opportunity_status") == "escalated":
            escalated += 1
    return decided, escalated


async def replay_recoveries(failed_payment_ids: list[str], rng: random.Random) -> tuple[int, int]:
    """Let a share of the seeded failures recover, split by experiment group.

    Recovery is replayed as a real `payment.captured` event on the original
    payment, so it travels the same resolution path as production traffic and
    lands in the audit trail the same way. The treatment and control rates
    differ, which is what gives the holdout something to measure.

    The group split is read back from the database rather than assumed: the
    80/20 assignment is made when the opportunity is created, and inventing our
    own split here would measure nothing but this script's own arithmetic.
    """
    from sqlalchemy import select

    from app.database.models import Opportunity, Payment
    from app.database.session import session_factory

    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Payment.razorpay_payment_id, Opportunity.experiment_group,
                       Payment.amount_minor, Payment.method, Payment.bank,
                       Payment.error_reason, Payment.occurred_at)
                .join(Opportunity, Opportunity.payment_id == Payment.id)
                .where(Payment.razorpay_payment_id.in_(failed_payment_ids))
            )
        ).all()

    recovered = {"treatment": 0, "control": 0}
    for payment_id, group, amount_minor, method, bank, error_reason, occurred_at in rows:
        natural = DEFAULT_NATURAL_RECOVERY_BASELINE.get(
            error_group(error_reason), DEFAULT_NATURAL_RECOVERY_BASELINE["unknown"]
        )
        rate = natural if group == "control" else min(0.95, natural + TREATMENT_UPLIFT)
        if rng.random() >= rate:
            continue
        await process_payment_event("payment.captured", {
            "id": payment_id,
            "amount": amount_minor,
            "currency": "INR",
            "method": method,
            "bank": bank,
            "error_reason": error_reason,
            "created_at": int((occurred_at + timedelta(minutes=37)).timestamp()),
            "status": "captured",
            "_synthetic": True,
        })
        recovered[group if group in recovered else "treatment"] += 1
    return recovered["treatment"], recovered["control"]


async def seed(days: int, failure_rate: float) -> int:
    rng = random.Random(20260828)
    now = datetime.now(timezone.utc)
    created = 0
    failures = 0
    failed_payment_ids: list[str] = []
    for hour in range(days * 24, 0, -1):
        for method, bank in SEGMENTS:
            for slot in range(SLOTS_PER_HOUR):
                created += 1
                failed = rng.random() < failure_rate
                entity = {
                    "id": f"pay_demo_baseline_{created:06d}",
                    "amount": rng.randrange(
                        *(AMOUNT_LARGE if rng.random() < LARGE_TICKET_SHARE else AMOUNT_TYPICAL),
                        100,
                    ),
                    "currency": "INR",
                    "method": method,
                    "bank": bank,
                    "email": f"baseline.{created % 80}@demo.local",
                    "contact": "+919800000000",
                    "created_at": int((now - timedelta(hours=hour, minutes=slot * 20)).timestamp()),
                    "status": "failed" if failed else "captured",
                    # Invented traffic is labelled as such at the point it enters
                    # the system, so every downstream KPI, experiment and export
                    # can exclude it without knowing where it came from.
                    "_synthetic": True,
                }
                if failed:
                    failures += 1
                    reason = rng.choices(_REASON_NAMES, weights=_REASON_WEIGHTS)[0]
                    entity.update({
                        "error_reason": reason,
                        "error_source": "gateway" if reason.endswith("_error") else "bank",
                    })
                    await process_payment_event("payment.failed", entity)
                    failed_payment_ids.append(entity["id"])
                else:
                    await process_payment_event("payment.captured", entity)
    print(f"Seeded {created} historical baseline payments across {len(SEGMENTS)} segments.")
    print(f"  {failures} failed -> {failures} recovery opportunities "
          f"(~{int(failures * (1 - CONTROL_SHARE))} treatment / ~{int(failures * CONTROL_SHARE)} control)")

    decided, escalated = await run_decisions()
    print(f"  decided {decided} opportunities ({escalated} escalated for human review)")

    treated, controlled = await replay_recoveries(failed_payment_ids, rng)
    print(f"  replayed recoveries: {treated} treatment, {controlled} control")

    print()
    print("This data is flagged is_synthetic. The Evidence tab excludes it by default;")
    print('tick "Include simulated traffic" to watch the holdout measurement work.')

    needed = int(MIN_PER_GROUP / CONTROL_SHARE)
    if failures < needed:
        shortfall_days = max(1, round(needed / max(failures, 1) * days))
        print()
        print(f"NOTE: the experiment refuses to claim a lift below {MIN_PER_GROUP} opportunities in")
        print(f"      BOTH groups, i.e. about {needed} failures. This run produced {failures}.")
        print(f"      Re-run with --days {shortfall_days} to seed enough history.")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=1,
                        help="days of hourly baseline history to seed (default: 1)")
    parser.add_argument("--failure-rate", type=float, default=0.06,
                        help="baseline failure rate; keep low so the anomaly detector "
                             "does not treat the baseline itself as an incident (default: 0.06)")
    args = parser.parse_args()
    if args.days < 1:
        parser.error("--days must be at least 1")
    if not 0.0 <= args.failure_rate < 0.5:
        parser.error("--failure-rate must be between 0.0 and 0.5")
    asyncio.run(seed(args.days, args.failure_rate))


if __name__ == "__main__":
    main()
