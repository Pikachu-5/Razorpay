from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.ml.actions import ACTIONS, INTERVENTION_COST_MINOR


@dataclass(frozen=True)
class PolicyInput:
    proposed_action: str
    amount_minor: int
    contact_attempts: int
    last_contact_at: datetime | None
    predicted_probability: float | None
    expected_recovery_minor: int
    is_subscription: bool


@dataclass(frozen=True)
class RuleResult:
    rule: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    action: str
    rules: list[RuleResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "action": self.action,
            "rules": [
                {"rule": r.rule, "passed": r.passed, "detail": r.detail} for r in self.rules
            ],
        }


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def evaluate_policy(inp: PolicyInput) -> PolicyDecision:
    settings = get_settings()
    rules: list[RuleResult] = []

    rules.append(
        RuleResult(
            rule="kill_switch",
            passed=not settings.policy_kill_switch,
            detail="emergency kill switch engaged" if settings.policy_kill_switch else "disengaged",
        )
    )
    rules.append(
        RuleResult(
            rule="action_allowlist",
            passed=inp.proposed_action in ACTIONS,
            detail=f"{inp.proposed_action} {'in' if inp.proposed_action in ACTIONS else 'NOT in'} supported action space",
        )
    )

    if inp.proposed_action == "do_nothing":
        return PolicyDecision(allowed=True, action=inp.proposed_action, rules=rules)

    if inp.proposed_action == "escalate_to_human":
        # Queueing a case for a person is not a customer-facing side effect, so
        # it is not measured against the value cap, the contact budget, or the
        # confidence floor -- those exist to govern automated outreach. The kill
        # switch and the action allowlist above still apply.
        rules.append(
            RuleResult(
                rule="human_review",
                passed=True,
                detail="internal review queue; no customer contact and no Razorpay call",
            )
        )
        return PolicyDecision(
            allowed=all(r.passed for r in rules), action=inp.proposed_action, rules=rules
        )

    amount_cap = settings.policy_max_amount_minor
    rules.append(
        RuleResult(
            rule="amount_cap",
            passed=inp.amount_minor <= amount_cap,
            detail=f"₹{inp.amount_minor / 100:,.0f} vs cap ₹{amount_cap / 100:,.0f}",
        )
    )

    budget = settings.policy_max_contact_attempts
    within_budget = inp.contact_attempts < budget or inp.proposed_action in (
        "wait_for_native_retry",
        "do_nothing",
    )
    rules.append(
        RuleResult(
            rule="contact_budget",
            passed=within_budget,
            detail=f"{inp.contact_attempts}/{budget} attempts used"
            + ("" if within_budget else " (budget exhausted)"),
        )
    )

    cooldown_ok = True
    cooldown_detail = "no prior contact"
    if inp.last_contact_at is not None:
        elapsed_min = (_utcnow() - inp.last_contact_at).total_seconds() / 60.0
        cooldown_ok = (
            elapsed_min >= settings.policy_cooldown_minutes
            or inp.proposed_action in ("wait_for_native_retry", "do_nothing")
        )
        cooldown_detail = f"last contact {elapsed_min:.0f} min ago (cooldown {settings.policy_cooldown_minutes} min)"
    rules.append(RuleResult(rule="cooldown", passed=cooldown_ok, detail=cooldown_detail))

    confidence_floor = settings.policy_confidence_floor
    probability = inp.predicted_probability
    confidence_ok = probability is None and inp.proposed_action == "wait_for_native_retry" or (
        probability is not None and probability >= confidence_floor
    )
    prob_label = f"{probability:.2f}" if probability is not None else "n/a"
    rules.append(
        RuleResult(
            rule="confidence_floor",
            passed=bool(confidence_ok),
            detail=f"p={prob_label} vs floor {confidence_floor:.2f}",
        )
    )

    margin = settings.policy_min_ev_margin_minor
    # expected_recovery_minor is already net of intervention cost in the
    # revenue ranker, so the policy compares the net value only to the margin.
    ev_ok = inp.expected_recovery_minor >= margin or inp.proposed_action == "wait_for_native_retry"
    rules.append(
        RuleResult(
            rule="expected_value_vs_cost",
            passed=ev_ok,
            detail=f"Net EV ₹{inp.expected_recovery_minor / 100:,.0f} vs minimum margin ₹{margin / 100:,.0f}",
        )
    )

    allowed = all(r.passed for r in rules)
    return PolicyDecision(allowed=allowed, action=inp.proposed_action, rules=rules)


def select_action_under_policy(
    ranked_actions: list[dict],
    *,
    amount_minor: int,
    contact_attempts: int,
    last_contact_at: datetime | None,
) -> tuple[PolicyDecision, dict | None]:
    blocked_by_amount_cap = False
    for candidate in ranked_actions:
        decision = evaluate_policy(
            PolicyInput(
                proposed_action=candidate["action"],
                amount_minor=amount_minor,
                contact_attempts=contact_attempts,
                last_contact_at=last_contact_at,
                predicted_probability=candidate.get("probability"),
                expected_recovery_minor=candidate["expected_recovery_minor"],
                is_subscription=False,
            )
        )
        if candidate["action"] == "do_nothing":
            # `do_nothing` sorts last and always passes, so reaching it means
            # nothing above it cleared the guardrails.  Fall through to the
            # escalation check rather than closing a large opportunity here.
            break
        if decision.allowed:
            return decision, candidate
        if any(r.rule == "amount_cap" and not r.passed for r in decision.rules):
            blocked_by_amount_cap = True

    # Nothing cleared the guardrails.  If the reason was the value cap, the
    # opportunity is not unattractive -- it is too expensive to decide
    # automatically, which is exactly the case a human should see.  Dropping it
    # to `do_nothing` would silently discard the largest recoverable amounts.
    fallback_action = "escalate_to_human" if blocked_by_amount_cap else "do_nothing"
    fallback = evaluate_policy(
        PolicyInput(
            proposed_action=fallback_action,
            amount_minor=amount_minor,
            contact_attempts=contact_attempts,
            last_contact_at=last_contact_at,
            predicted_probability=None,
            expected_recovery_minor=0,
            is_subscription=False,
        )
    )
    if not fallback.allowed:
        # The kill switch is engaged; escalation is unavailable too.
        fallback_action = "do_nothing"
        fallback = evaluate_policy(
            PolicyInput(
                proposed_action="do_nothing",
                amount_minor=amount_minor,
                contact_attempts=contact_attempts,
                last_contact_at=last_contact_at,
                predicted_probability=None,
                expected_recovery_minor=0,
                is_subscription=False,
            )
        )
    return fallback, {
        "action": fallback_action,
        "probability": None,
        "cost_minor": INTERVENTION_COST_MINOR.get(fallback_action, 0),
        "expected_recovery_minor": 0,
    }
