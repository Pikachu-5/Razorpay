from __future__ import annotations

from dataclasses import dataclass

from app.ml.actions import INTERVENTION_COST_MINOR, eligible_actions
from app.ml.predictor import Prediction, get_prediction_service


@dataclass(frozen=True)
class RevenueRanking:
    ranked: list[dict]
    best_action: str | None
    best_expected_minor: int
    model_version: str | None
    degraded: bool
    natural_recovery_probability: float = 0.0


def rank_actions(ctx: dict, amount_minor: int) -> RevenueRanking:
    service = get_prediction_service()
    prediction: Prediction = service.predict(ctx)
    baseline = prediction.natural_recovery_probability

    scored: list[dict] = []
    for action, probability in prediction.probabilities.items():
        cost = INTERVENTION_COST_MINOR.get(action, 0)
        # Rank on INCREMENTAL recovery, not gross recovery.
        #
        # `probability` is P(recovery | we take this action).  Some of that
        # recovery would have happened anyway -- the customer retries, the bank
        # comes back up, the standing instruction succeeds on its own.  Paying
        # to contact someone is only justified by the part that would NOT have
        # happened, so the expected value is measured against the do-nothing
        # counterfactual rather than against zero.
        #
        # Ranking on gross recovery instead systematically overstates the case
        # for acting -- on this dataset by roughly 2.5x -- and it puts the
        # decision engine at odds with the treatment/control experiment that
        # grades it, which measures exactly this difference.
        # Round before multiplying by money, and report exactly the number that
        # was used: float subtraction noise (0.70 - 0.60 = 0.09999999999999998)
        # is invisible in a probability and a rupee off in an audit record.
        incremental = round(max(0.0, probability - baseline), 4)
        expected = incremental * amount_minor - cost
        scored.append({
            "action": action,
            "probability": round(probability, 4),
            "baseline_probability": round(baseline, 4),
            "incremental_probability": incremental,
            "cost_minor": cost,
            "gross_recovery_minor": int(probability * amount_minor),
            "expected_recovery_minor": int(expected),
            "evidence_source": prediction.action_sources.get(action, prediction.source),
        })

    # `do_nothing` is the reference point the other actions are measured
    # against, so its incremental value is zero by construction.
    if "do_nothing" in eligible_actions(bool(ctx.get("is_subscription"))):
        scored.append({
            "action": "do_nothing",
            "probability": None,
            "baseline_probability": round(baseline, 4),
            "incremental_probability": 0.0,
            "cost_minor": 0,
            "gross_recovery_minor": int(baseline * amount_minor),
            "expected_recovery_minor": 0,
            "evidence_source": prediction.baseline_source,
        })

    scored.sort(key=lambda item: item["expected_recovery_minor"], reverse=True)
    best = scored[0] if scored else {"action": "do_nothing", "expected_recovery_minor": 0}

    return RevenueRanking(
        ranked=scored,
        best_action=best["action"],
        best_expected_minor=int(best["expected_recovery_minor"]),
        model_version=prediction.model_version,
        degraded=prediction.degraded,
        natural_recovery_probability=baseline,
    )
