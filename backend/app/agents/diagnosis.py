from __future__ import annotations

from dataclasses import dataclass, field

from app.ml.features import error_group


@dataclass(frozen=True)
class Diagnosis:
    classification: str
    summary: str
    confidence: float
    evidence: list[str] = field(default_factory=list)


TEMPORARY = "temporary"
AUTH = "auth"
INSUFFICIENT = "insufficient_funds"
INSTRUMENT = "instrument"
UNKNOWN = "unknown"

_CLASSIFICATIONS = {
    TEMPORARY: (
        "transient_failure",
        0.90,
        "Failure class is temporary (timeout/network/gateway); instrument likely still valid",
    ),
    AUTH: ("instrument_needs_update", 0.85, "Authentication failure; customer action required"),
    INSUFFICIENT: (
        "funds_gap",
        0.80,
        "Decline consistent with insufficient funds; often resolves within days",
    ),
    INSTRUMENT: (
        "dead_instrument",
        0.88,
        "Instrument reported expired/blocked/invalid; recovery requires a different instrument",
    ),
    UNKNOWN: ("unclassified_failure", 0.40, "No recognized failure reason provided by rails"),
}


def diagnose(
    *,
    error_reason: str | None,
    is_subscription: bool,
    prior_failed_same_instrument: int,
    method: str | None,
    bank: str | None,
) -> Diagnosis:
    group = error_group(error_reason)
    classification, base_confidence, summary = _CLASSIFICATIONS[group]
    evidence: list[str] = []

    reason_label = error_reason or "no reason code"
    evidence.append(f"error_reason={reason_label}")
    if bank:
        evidence.append(f"bank={bank}")
    if method:
        evidence.append(f"method={method}")

    confidence = base_confidence
    if group == INSTRUMENT and prior_failed_same_instrument >= 2:
        confidence = min(0.97, confidence + 0.05)
        evidence.append(f"{prior_failed_same_instrument} prior failures on same instrument")
    if is_subscription:
        confidence = min(0.97, confidence)
        evidence.append("active subscription: native Razorpay retry cycle also running")
    if group == UNKNOWN:
        evidence.append("missing/unknown error metadata limits certainty")

    return Diagnosis(
        classification=classification,
        summary=summary,
        confidence=round(confidence, 2),
        evidence=evidence,
    )
