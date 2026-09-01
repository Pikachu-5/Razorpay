from __future__ import annotations

import argparse
import json
import math
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.ml.features import error_group  # noqa: E402

METHODS = ("upi", "card", "netbanking", "wallet")
BANKS_BY_METHOD = {
    "upi": ["HDFC", "SBI", "ICICI", "AXIS", "KOTAK"],
    "card": ["HDFC", "ICICI", "AXIS", "KOTAK", "SBI"],
    "netbanking": ["HDFC", "SBI", "ICICI", "AXIS"],
    "wallet": ["PPI", "amazonpay", "freecharge"],
}
BASE_FAIL_RATE = {"upi": 0.06, "card": 0.09, "netbanking": 0.12, "wallet": 0.04}
BANK_RISK = {"HDFC": 0.00, "SBI": 0.02, "ICICI": 0.01, "AXIS": 0.03, "KOTAK": 0.02,
             "PPI": -0.02, "amazonpay": -0.01, "freecharge": 0.01}

FAILURE_REASONS = {
    "temporary": ["timeout", "network_error", "gateway_error", "processing_error"],
    "auth": ["authentication_required", "user_authentication_failed"],
    "insufficient_funds": ["insufficient_funds", "card_insufficient_funds"],
    "instrument": ["card_expired", "invalid_card", "card_blocked", "invalid_vpa"],
}
REASON_BASE_WEIGHTS = {"temporary": 0.42, "auth": 0.18, "insufficient_funds": 0.25, "instrument": 0.15}

MERCHANTS = [
    {"merchant_id": "merch_prime_retail", "baseline_success": 0.88, "volume": 42000,
     "mix": {"upi": 0.55, "card": 0.25, "netbanking": 0.14, "wallet": 0.06}},
    {"merchant_id": "merch_saas_subscriptions", "baseline_success": 0.91, "volume": 9000,
     "mix": {"card": 0.52, "upi": 0.30, "netbanking": 0.16, "wallet": 0.02}},
    {"merchant_id": "merch_d2c_fashion", "baseline_success": 0.84, "volume": 21000,
     "mix": {"upi": 0.46, "card": 0.34, "netbanking": 0.10, "wallet": 0.10}},
]

TIER_AMOUNTS = {
    "low": (350_00, 3_000_00),
    "mid": (800_00, 12_000_00),
    "high": (4_000_00, 45_000_00),
}


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class Weather:
    def __init__(self, days: int, rng: random.Random) -> None:
        self.days = days
        self.rng = rng
        self.shocks: dict[tuple[int, str], float] = {}
        self.method_shocks: dict[tuple[int, str], float] = {}
        for day in range(days):
            if rng.random() < 0.06:
                bank = rng.choice(list(BANK_RISK.keys()))
                severity = rng.uniform(0.25, 0.65)
                duration = rng.randint(1, 3)
                for d in range(duration):
                    self.shocks[(day + d, bank)] = severity
            if rng.random() < 0.04:
                method = rng.choice(METHODS)
                severity = rng.uniform(0.15, 0.45)
                duration = rng.randint(1, 2)
                for d in range(duration):
                    self.method_shocks[(day + d, method)] = severity

    def failure_boost(self, day: int, bank: str, method: str) -> float:
        return self.shocks.get((day, bank), 0.0) + self.method_shocks.get((day, method), 0.0)


def generate(outdir: Path, n_customers: int, n_attempts: int, seed: int) -> None:
    rng = random.Random(seed)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    horizon_days = 180
    weather = Weather(horizon_days, rng)

    customers: list[dict] = []
    for i in range(n_customers):
        tier = rng.choices(["low", "mid", "high"], weights=[0.5, 0.35, 0.15])[0]
        customers.append({
            "customer_id": f"cust_{i:06d}",
            "merchant_id": rng.choices([m["merchant_id"] for m in MERCHANTS],
                                       weights=[0.5, 0.25, 0.25])[0],
            "tier": tier,
            "responsiveness": 0.0,  # assigned below
            "frequency": rng.choices(["rare", "regular", "frequent"],
                                     weights=[0.4, 0.4, 0.2])[0],
            "has_subscription": rng.random() < 0.22,
            "contact_stale": False,  # assigned below
            "prefers_upi_bias": rng.uniform(-0.15, 0.25),
        })
    for cust in customers:
        responsiveness = max(0.05, min(0.97, rng.betavariate(2.2, 2.0)))
        cust["responsiveness"] = responsiveness
        # A responsive customer keeps their contact details current. Staleness is
        # therefore not independent noise -- it is a symptom of disengagement,
        # which is also what makes an intervention fail.
        cust["contact_stale"] = rng.random() < (0.02 + 0.16 * (1.0 - responsiveness))

    merch_by_id = {m["merchant_id"]: m for m in MERCHANTS}

    freq_weight = {"rare": 0.3, "regular": 1.0, "frequent": 2.6}

    attempts_rows: list[list] = []
    opps_rows: list[list] = []
    interventions_rows: list[list] = []

    prior_payments: dict[str, int] = {c["customer_id"]: 0 for c in customers}
    prior_successes: dict[str, int] = {c["customer_id"]: 0 for c in customers}
    prior_failed_instrument: dict[str, int] = {c["customer_id"]: 0 for c in customers}
    median_amounts: dict[str, float] = {}

    attempt_seq = 0
    opp_seq = 0

    # Attempt frequency carries the latent trait into observable history: an
    # engaged customer transacts more, so `customer_prior_payments` becomes a
    # real (noisy) signal rather than a decoy feature.
    weights = [
        freq_weight[c["frequency"]]
        * (1.15 if c["has_subscription"] else 1.0)
        * (0.6 + 0.8 * c["responsiveness"])
        for c in customers
    ]

    for n in range(n_attempts):
        cust = rng.choices(customers, weights=weights)[0]
        merch = merch_by_id[cust["merchant_id"]]
        day = min(horizon_days - 1, int((n / n_attempts) * horizon_days))
        ts = start + timedelta(
            days=day,
            minutes=rng.randint(0, 24 * 60 - 1),
        )

        lo, hi = TIER_AMOUNTS[cust["tier"]]
        amount_minor = int(math.exp(rng.uniform(math.log(lo), math.log(hi))))
        amount_minor = min(amount_minor, 2_500_000_00)

        mix_adjusted = dict(merch["mix"])
        mix_adjusted["upi"] = max(0.05, mix_adjusted["upi"] + cust["prefers_upi_bias"])
        total = sum(mix_adjusted.values())
        method = rng.choices(
            list(mix_adjusted.keys()),
            weights=[v / total for v in mix_adjusted.values()],
        )[0]
        bank = rng.choice(BANKS_BY_METHOD[method])

        is_subscription_charge = cust["has_subscription"] and rng.random() < 0.18
        base = BASE_FAIL_RATE[method] + BANK_RISK.get(bank, 0.01)
        boost = weather.failure_boost(day, bank, method)
        amount_stress = 0.10 * (amount_minor > 8_000_00)
        merchant_offset = merch["baseline_success"] - 0.87
        # A small responsiveness term: engaged customers keep working instruments
        # and funded accounts, so their observed success rate is a legitimate
        # proxy for how they will behave after a failure.
        p_fail = sigmoid(
            -2.55
            + 7.5 * (base + boost + amount_stress)
            - 6.0 * merchant_offset
            - 0.65 * (cust["responsiveness"] - 0.5)
        )
        failed = rng.random() < p_fail

        attempt_seq += 1
        payment_id = f"pay_gen_{attempt_seq:07d}"

        error_reason = None
        error_source = None
        if failed:
            weights_reason = dict(REASON_BASE_WEIGHTS)
            if boost > 0.2:
                weights_reason["temporary"] += 0.35
            if amount_minor > 20_000_00:
                weights_reason["insufficient_funds"] += 0.15
            if cust["contact_stale"]:
                weights_reason["instrument"] += 0.08
            chosen_group = rng.choices(
                list(weights_reason.keys()), weights=list(weights_reason.values())
            )[0]
            error_reason = rng.choice(FAILURE_REASONS[chosen_group])
            error_source = rng.choice(["bank", "gateway", "network"])

        attempts_rows.append([
            payment_id, cust["customer_id"], merch["merchant_id"], ts.isoformat(),
            amount_minor, method, bank,
            "failed" if failed else "captured",
            error_reason or "", error_source or "",
            1 if is_subscription_charge else 0,
        ])

        if not failed:
            prior_successes[cust["customer_id"]] += 1
            amounts = median_amounts.setdefault(cust["customer_id"], [])
            amounts.append(amount_minor)
            if len(amounts) > 40:
                del amounts[: len(amounts) - 40]
        prior_payments[cust["customer_id"]] += 1

        if failed:
            minutes_since = rng.uniform(2, 45)

            group = error_group(error_reason)
            responsiveness = cust["responsiveness"]

            # The do-nothing counterfactual has to move with the same observable
            # drivers as the actions, otherwise "incremental value" would be
            # learnable from a baseline that is really just a constant.
            natural_p = sigmoid(
                -1.95
                + 2.6 * responsiveness
                + (0.70 if group == "temporary" else -0.60 if group == "instrument" else -0.15)
                - (1.0 if cust["contact_stale"] else 0.0)
                + 0.5 * min(prior_successes[cust["customer_id"]], 10) / 10.0
                - 0.35 * min(prior_failed_instrument[cust["customer_id"]], 5) / 5.0
                # Big-ticket failures self-resolve less often: the customer has
                # to decide again, not just tap again.
                - 0.85 * min(1.0, math.log1p(amount_minor / 100_00) / math.log1p(400.0))
            )
            natural_p = max(0.02, min(0.93, natural_p))
            natural_recovered = rng.random() < natural_p
            if rng.random() < 0.05:
                natural_recovered = not natural_recovered

            opp_seq += 1
            opp_id = f"opp_gen_{opp_seq:07d}"
            created_ts = ts + timedelta(minutes=minutes_since)

            hour = created_ts.hour
            weekday = created_ts.weekday()

            opportunities_row = [
                opp_id, payment_id, created_ts.isoformat(), amount_minor, method, bank,
                error_reason or "", error_group(error_reason),
                1 if is_subscription_charge else 0,
                prior_payments[cust["customer_id"]],
                prior_successes[cust["customer_id"]],
                1 if method == "card" and prior_successes[cust["customer_id"]] > 2 else 0,
                prior_failed_instrument[cust["customer_id"]],
                round(minutes_since, 1),
                merch["baseline_success"], merch["volume"], hour, weekday,
                1 if cust["contact_stale"] else 0,
                1 if natural_recovered else 0,
            ]
            opps_rows.append(opportunities_row)

            intervened = (
                rng.random() < 0.35
                and not cust["contact_stale"]
                and (amount_minor > 1_500_00 or group == "temporary")
            )
            if intervened and rng.random() < 0.20:
                action = rng.choice(
                    ["send_payment_link", "send_reminder", "prompt_card_change", "wait_for_native_retry"]
                )
            elif intervened:
                if is_subscription_charge and group == "auth":
                    action = "prompt_card_change"
                elif method == "upi" or amount_minor > 5_000_00:
                    action = "send_payment_link"
                else:
                    action = "send_reminder"
            else:
                action = None

            if action is not None:
                seen = prior_payments[cust["customer_id"]]
                won = prior_successes[cust["customer_id"]]
                observed_rate = (won + 2.0) / (seen + 4.0)
                effect = intervention_effect(
                    action, group, method, responsiveness,
                    is_subscription_charge, cust, won,
                    amount_minor=amount_minor,
                    minutes_since=minutes_since,
                    hour=hour,
                    prior_success_rate=observed_rate,
                )
                success = rng.random() < effect
                if rng.random() < 0.05:
                    success = not success
                recovered = amount_minor if success else 0
                cost = {"send_payment_link": 1500, "send_reminder": 300,
                        "prompt_card_change": 2000, "wait_for_native_retry": 0}[action]
                interventions_rows.append([
                    opp_id, action, created_ts.isoformat(),
                    1 if success else 0, recovered, cost, "legacy_ops",
                ])
                if success:
                    natural_recovered = True
                opps_rows[-1][-1] = 1 if natural_recovered else 0

            if failed and rng.random() < 0.5:
                prior_failed_instrument[cust["customer_id"]] += 1

        if n % 20000 == 0 and n > 0:
            print(f"  generated {n}/{n_attempts} attempts")

    outdir.mkdir(parents=True, exist_ok=True)
    write_csv(outdir / "attempts.csv",
              ["payment_id", "customer_id", "merchant_id", "ts", "amount_minor", "method",
               "bank", "status", "error_reason", "error_source", "is_subscription"],
              attempts_rows)
    write_csv(outdir / "opportunities.csv",
              ["opp_id", "payment_id", "created_ts", "amount_minor", "method", "bank",
               "error_reason", "error_group", "is_subscription",
               "cust_prior_payments", "cust_prior_successes", "cust_prior_method_match",
               "cust_prior_failed_instrument", "minutes_since_failure",
               "merchant_baseline_success", "merchant_volume", "hour", "weekday",
               "contact_stale", "natural_recovered"],
              opps_rows)
    write_csv(outdir / "interventions.csv",
              ["opp_id", "action", "executed_ts", "success", "recovered_minor", "cost_minor",
               "decided_by"],
              interventions_rows)

    stats = {
        "seed": seed,
        "customers": n_customers,
        "attempts": len(attempts_rows),
        "failures": sum(1 for r in attempts_rows if r[7] == "failed"),
        "opportunities": len(opps_rows),
        "interventions": len(interventions_rows),
        "natural_recovery_rate_opps_natural_only": None,
        "intervention_success_rate": None,
    }
    intervened_ids = {r[0] for r in interventions_rows}
    natural_only = [r for r in opps_rows if r[0] not in intervened_ids]
    if natural_only:
        stats["natural_recovery_rate_opps_natural_only"] = round(
            sum(r[-1] for r in natural_only) / len(natural_only), 4
        )
    if interventions_rows:
        stats["intervention_success_rate"] = round(
            sum(r[3] for r in interventions_rows) / len(interventions_rows), 4
        )
    (outdir / "dataset_stats.json").write_text(json.dumps(stats, indent=2))
    print(json.dumps(stats, indent=2))


def intervention_effect(
    action, group, method, responsiveness, is_subscription, cust, prior_successes,
    *, amount_minor: int, minutes_since: float, hour: int, prior_success_rate: float,
) -> float:
    """Probability that `action` recovers this payment.

    The terms below are split deliberately between what a decision-time model
    can see and what it cannot.

    OBSERVABLE (these are what make the problem learnable): failure class,
    method, amount, how quickly we act, time of day, and the customer's prior
    success rate.  A model with those features can genuinely rank actions.

    LATENT: `responsiveness` is the customer's true propensity to act, which no
    merchant observes directly.  It is kept as an irreducible term so the
    problem stays honest -- a perfect AUC here would mean the generator leaked.
    It reaches the features only indirectly, through the prior success rate it
    induces over that customer's history.
    """
    # Latent trait, partially proxied by observable history.
    p = 0.10 + 0.55 * responsiveness + 0.35 * prior_success_rate

    # Recency: intent decays fast. Acting within a few minutes of the failure is
    # worth far more than acting an hour later, and this is the single lever the
    # control plane actually controls.
    p += 0.22 * math.exp(-minutes_since / 18.0)

    # Amount: large payments fail for affordability and deliberation reasons and
    # recover less often, whatever you send. This is what stops expected value
    # from being monotone in amount -- and therefore what a model has to learn
    # in order to beat simply chasing the biggest tickets.
    p -= 0.30 * min(1.0, math.log1p(amount_minor / 100_00) / math.log1p(400.0))

    # Contact hour: a link that lands at 3am is read at 9am, if at all.
    if hour < 7 or hour >= 23:
        p -= 0.14
    elif 10 <= hour <= 21:
        p += 0.06

    if action == "send_payment_link":
        if group == "temporary":
            p += 0.28
            if method == "upi":
                p += 0.10
        if group == "instrument":
            p -= 0.15
    elif action == "send_reminder":
        # A reminder only works on someone already inclined to pay: no new
        # payment path, just a nudge. Cheap, and worth it exactly when the
        # customer has a strong history and the amount is small.
        p += 0.02
        p += 0.20 * min(prior_successes, 8) / 8.0
        if amount_minor <= 1_500_00:
            p += 0.10
        if group in ("instrument", "auth"):
            p -= 0.22
    elif action == "prompt_card_change":
        p += 0.05
        if is_subscription and group == "auth":
            p += 0.38
        if group == "instrument":
            p += 0.20
        if cust["prefers_upi_bias"] > 0.15:
            p -= 0.10
    elif action == "wait_for_native_retry":
        if is_subscription and group == "temporary":
            p += 0.30
        if group == "instrument":
            p -= 0.20
    return max(0.02, min(0.96, p))


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    import csv

    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate synthetic recovery training data")
    parser.add_argument("--customers", type=int, default=8000)
    parser.add_argument("--attempts", type=int, default=120000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--outdir", type=str, default=str(REPO_ROOT / "data" / "synthetic"))
    args = parser.parse_args()
    print(f"Generating {args.attempts} attempts for {args.customers} customers (seed={args.seed})")
    generate(Path(args.outdir), args.customers, args.attempts, args.seed)
    print(f"Wrote dataset to {args.outdir}")


if __name__ == "__main__":
    main()
