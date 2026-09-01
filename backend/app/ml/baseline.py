from __future__ import annotations

from app.ml.features import error_group

# Probability that a failed payment is recovered *without* any intervention.
#
# This is the counterfactual the expected-value math needs.  A model that says
# "70% chance this customer pays after we send a link" is not describing 70%
# worth of recoverable revenue: some of those customers would have retried on
# their own.  Only the difference between the two is revenue the control plane
# can claim to have created, and only that difference should justify the cost
# of contacting someone.
#
# The table below is measured, not assumed: it is the natural-recovery rate of
# the NON-INTERVENED opportunities in the training dataset, grouped by failure
# class.  `training/train_recovery_model.py` recomputes it from whatever data
# it is given and ships the result inside the model artifact, so a model
# trained on real Razorpay data carries its own baseline.  These constants are
# the fallback used by the heuristic predictor and by any artifact predating
# the field.
#
# Caveat worth stating out loud: the non-intervened population is not a random
# sample -- operators chose which failures to work.  The live treatment/control
# holdout in `api/metrics.py` is the unbiased estimate; this table is the best
# available prior for a *decision* that has to be made before that evidence
# exists.
DEFAULT_NATURAL_RECOVERY_BASELINE: dict[str, float] = {
    "temporary": 0.5468,
    "auth": 0.3903,
    "insufficient_funds": 0.3845,
    "instrument": 0.2868,
    "unknown": 0.4316,
}

BASELINE_TABLE_KEY = "natural_recovery_baseline"


def natural_recovery_probability(
    ctx: dict, table: dict[str, float] | None = None
) -> float:
    """Estimated P(recovery | no intervention) for this failure."""
    lookup = table or DEFAULT_NATURAL_RECOVERY_BASELINE
    group = error_group(ctx.get("error_reason"))
    value = lookup.get(group)
    if value is None:
        value = lookup.get("unknown", DEFAULT_NATURAL_RECOVERY_BASELINE["unknown"])
    return max(0.0, min(1.0, float(value)))


def sanitize_baseline_table(raw: object) -> dict[str, float] | None:
    """Accept a baseline table from an untrusted artifact, or reject it whole.

    A malformed or partially-populated table would silently distort every
    expected-value calculation, so a table that is not a complete, in-range
    mapping is discarded in favour of the checked-in default.
    """
    if not isinstance(raw, dict) or not raw:
        return None
    cleaned: dict[str, float] = {}
    for group, value in raw.items():
        if not isinstance(group, str) or isinstance(value, bool):
            return None
        try:
            probability = float(value)
        except (TypeError, ValueError):
            return None
        if not 0.0 <= probability <= 1.0:
            return None
        cleaned[group] = probability
    if "unknown" not in cleaned:
        return None
    return cleaned
