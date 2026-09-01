from __future__ import annotations

ACTIONS = [
    "send_payment_link",
    "send_reminder",
    "prompt_card_change",
    "wait_for_native_retry",
    "escalate_to_human",
    "do_nothing",
]

# Actions a model is allowed to score.  Everything else is chosen by the
# deterministic policy layer, never by a prediction.
PREDICTED_ACTIONS = [
    "send_payment_link",
    "send_reminder",
    "prompt_card_change",
    "wait_for_native_retry",
]

# Reachable only through `policy.engine`: `do_nothing` when no action clears the
# guardrails, `escalate_to_human` when the guardrails refuse *because the amount
# is large* -- money worth a person's attention should not be silently dropped.
POLICY_ONLY_ACTIONS = ["escalate_to_human", "do_nothing"]

# Actions that put a message in front of the customer.  `escalate_to_human`
# deliberately is not one: it queues internal review, so it must not consume the
# customer contact budget or trip the contact cooldown.
CUSTOMER_CONTACT_ACTIONS = frozenset(
    {"send_payment_link", "send_reminder", "prompt_card_change"}
)

INTERVENTION_COST_MINOR = {
    "send_payment_link": 1500,
    "send_reminder": 300,
    "prompt_card_change": 2000,
    "wait_for_native_retry": 0,
    "escalate_to_human": 5000,
    "do_nothing": 0,
}


def eligible_actions(is_subscription: bool) -> list[str]:
    if is_subscription:
        return list(ACTIONS)
    return [a for a in ACTIONS if a not in ("prompt_card_change", "wait_for_native_retry")]


def predictable_actions(is_subscription: bool) -> list[str]:
    """The scorable subset of `eligible_actions` for this opportunity."""
    eligible = set(eligible_actions(is_subscription))
    return [action for action in PREDICTED_ACTIONS if action in eligible]
