from __future__ import annotations

import math

TEMPORARY_REASONS = {
    "timeout", "network_error", "gateway_error", "system_error", "processing_error",
}
AUTH_REASONS = {"authentication_required", "user_authentication_failed", "otp_invalid"}
INSUFFICIENT_REASONS = {"insufficient_funds", "card_insufficient_funds", "payment_declined_by_bank"}
INSTRUMENT_REASONS = {"card_expired", "invalid_card", "card_blocked", "invalid_vpa"}


def error_group(error_reason: str | None) -> str:
    if not error_reason:
        return "unknown"
    reason = error_reason.lower()
    if reason in TEMPORARY_REASONS:
        return "temporary"
    if reason in AUTH_REASONS:
        return "auth"
    if reason in INSUFFICIENT_REASONS:
        return "insufficient_funds"
    if reason in INSTRUMENT_REASONS:
        return "instrument"
    return "unknown"


KNOWN_BANKS = ("HDFC", "SBI", "ICICI", "AXIS", "KOTAK")
KNOWN_METHODS = ("upi", "card", "netbanking", "wallet")

FEATURE_NAMES = [
    "amount_log",
    "amount_ratio_to_customer_median",
    "is_high_value",
    "method_upi",
    "method_card",
    "method_netbanking",
    "method_wallet",
    "bank_hdfc",
    "bank_sbi",
    "bank_icici",
    "bank_axis",
    "bank_kotak",
    "bank_other",
    "error_temporary",
    "error_auth",
    "error_insufficient_funds",
    "error_instrument",
    "error_unknown",
    "customer_prior_payments_log",
    "customer_prior_success_rate_smoothed",
    "customer_prefers_method_match",
    "prior_failed_same_instrument_log",
    "minutes_since_failure_log",
    "merchant_baseline_success",
    "merchant_volume_log",
    "hour_frac",
    "is_weekend",
]


def _safe(v: float, default: float) -> float:
    return default if v is None or (isinstance(v, float) and math.isnan(v)) else float(v)


def build_features(ctx: dict) -> dict[str, float]:
    amount_minor = max(0.0, _safe(ctx.get("amount_minor"), 0.0))
    method = (ctx.get("method") or "other").lower()
    bank = ctx.get("bank")
    group = error_group(ctx.get("error_reason"))

    prior_payments = _safe(ctx.get("customer_prior_payments"), 0.0)
    prior_successes = _safe(ctx.get("customer_prior_successes"), 0.0)
    smoothed_rate = (prior_successes + 2.0) / (prior_payments + 4.0)

    median_amount = _safe(ctx.get("customer_median_amount_minor"), 0.0)
    ratio = amount_minor / median_amount if median_amount > 0 else 1.0

    minutes_since = max(0.0, _safe(ctx.get("minutes_since_failure"), 5.0))
    hour = int(_safe(ctx.get("occurred_hour"), 12))

    feats: dict[str, float] = {
        "amount_log": math.log1p(amount_minor),
        "amount_ratio_to_customer_median": min(ratio, 10.0),
        "is_high_value": 1.0 if amount_minor >= 1_000_000 else 0.0,
        "merchant_baseline_success": _safe(ctx.get("merchant_baseline_success"), 0.85),
        "merchant_volume_log": math.log1p(_safe(ctx.get("merchant_monthly_volume"), 0.0)),
        "customer_prior_payments_log": math.log1p(prior_payments),
        "customer_prior_success_rate_smoothed": smoothed_rate,
        "customer_prefers_method_match": 1.0 if ctx.get("customer_prior_success_with_method") else 0.0,
        "prior_failed_same_instrument_log": math.log1p(
            _safe(ctx.get("prior_failed_same_instrument"), 0.0)
        ),
        "minutes_since_failure_log": math.log1p(minutes_since),
        "hour_frac": (hour % 24) / 24.0,
        "is_weekend": 1.0 if int(_safe(ctx.get("occurred_weekday"), 2)) >= 5 else 0.0,
    }
    for m in KNOWN_METHODS:
        feats[f"method_{m}"] = 1.0 if method == m else 0.0
    for b in KNOWN_BANKS:
        feats[f"bank_{b.lower()}"] = 1.0 if bank == b else 0.0
    feats["bank_other"] = 1.0 if bank not in KNOWN_BANKS else 0.0
    for g in ("temporary", "auth", "insufficient_funds", "instrument", "unknown"):
        feats[f"error_{g}"] = 1.0 if group == g else 0.0

    assert set(feats.keys()) == set(FEATURE_NAMES), "feature contract drift"
    return {name: feats[name] for name in FEATURE_NAMES}


def feature_vector(ctx: dict) -> list[float]:
    feats = build_features(ctx)
    return [feats[name] for name in FEATURE_NAMES]
