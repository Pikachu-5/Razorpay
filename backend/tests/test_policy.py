from datetime import datetime, timedelta, timezone

from app.policy.engine import PolicyInput, evaluate_policy, select_action_under_policy


def base_input(**overrides) -> PolicyInput:
    values = dict(
        proposed_action="send_payment_link",
        amount_minor=420000,
        contact_attempts=0,
        last_contact_at=None,
        predicted_probability=0.78,
        expected_recovery_minor=300000,
        is_subscription=False,
    )
    values.update(overrides)
    return PolicyInput(**values)


def test_do_nothing_always_allowed():
    decision = evaluate_policy(base_input(proposed_action="do_nothing", predicted_probability=None,
                                          expected_recovery_minor=0))
    assert decision.allowed is True


def test_amount_cap_blocks():
    decision = evaluate_policy(base_input(amount_minor=99_000_000))
    amount_rule = next(r for r in decision.rules if r.rule == "amount_cap")
    assert amount_rule.passed is False
    assert decision.allowed is False


def test_contact_budget_blocks_fourth_attempt():
    decision = evaluate_policy(base_input(contact_attempts=3))
    assert next(r for r in decision.rules if r.rule == "contact_budget").passed is False


def test_cooldown_blocks_recent_contact():
    recent = datetime.now(timezone.utc) - timedelta(minutes=10)
    decision = evaluate_policy(base_input(last_contact_at=recent))
    assert next(r for r in decision.rules if r.rule == "cooldown").passed is False


def test_cooldown_allows_after_window():
    old = datetime.now(timezone.utc) - timedelta(hours=3)
    decision = evaluate_policy(base_input(last_contact_at=old))
    assert next(r for r in decision.rules if r.rule == "cooldown").passed is True


def test_confidence_floor_blocks_low_probability():
    decision = evaluate_policy(base_input(predicted_probability=0.20))
    assert next(r for r in decision.rules if r.rule == "confidence_floor").passed is False


def test_ev_vs_cost_blocks_negative_expected_value():
    decision = evaluate_policy(
        base_input(amount_minor=10000, predicted_probability=0.9, expected_recovery_minor=400)
    )
    ev_rule = next(r for r in decision.rules if r.rule == "expected_value_vs_cost")
    assert ev_rule.passed is False
    assert decision.allowed is False


def test_net_expected_value_does_not_charge_intervention_cost_twice():
    decision = evaluate_policy(
        base_input(amount_minor=10000, predicted_probability=0.9, expected_recovery_minor=900)
    )
    assert next(r for r in decision.rules if r.rule == "expected_value_vs_cost").passed is True


def test_kill_switch_blocks_everything(monkeypatch):
    from app.core.config import get_settings as real_get_settings

    class StubSettings:
        policy_kill_switch = True
        policy_max_amount_minor = 2_500_000
        policy_max_contact_attempts = 3
        policy_cooldown_minutes = 60
        policy_confidence_floor = 0.35
        policy_min_ev_margin_minor = 500

    import app.policy.engine as engine_module

    monkeypatch.setattr(engine_module, "get_settings", lambda: StubSettings())
    try:
        real_get_settings.cache_clear()
        decision = evaluate_policy(base_input())
        assert decision.allowed is False
        assert decision.rules[0].rule == "kill_switch"
        assert decision.rules[0].passed is False
    finally:
        real_get_settings.cache_clear()


def test_selection_falls_through_blocked_to_next_best():
    ranked = [
        {"action": "prompt_card_change", "probability": None, "cost_minor": 0,
         "expected_recovery_minor": 500000},
        {"action": "send_payment_link", "probability": 0.8, "cost_minor": 1500,
         "expected_recovery_minor": 400000},
        {"action": "do_nothing", "probability": None, "cost_minor": 0,
         "expected_recovery_minor": 0},
    ]
    decision, chosen = select_action_under_policy(
        ranked, amount_minor=420000, contact_attempts=0, last_contact_at=None
    )
    assert chosen["action"] == "send_payment_link"
    assert decision.allowed is True


def test_selection_returns_do_nothing_when_all_blocked():
    ranked = [
        {"action": "send_payment_link", "probability": 0.1, "cost_minor": 1500,
         "expected_recovery_minor": 2000},
        {"action": "do_nothing", "probability": None, "cost_minor": 0,
         "expected_recovery_minor": 0},
    ]
    decision, chosen = select_action_under_policy(
        ranked, amount_minor=420000, contact_attempts=0, last_contact_at=None
    )
    assert chosen["action"] == "do_nothing"
    assert decision.allowed is True


def test_escalates_to_human_when_only_the_value_cap_blocks():
    """High-value money must reach a person, not be dropped as `do_nothing`."""
    ranked = [
        {"action": "send_payment_link", "probability": 0.82,
         "expected_recovery_minor": 4_000_000},
        {"action": "do_nothing", "probability": None, "expected_recovery_minor": 0},
    ]
    decision, chosen = select_action_under_policy(
        ranked, amount_minor=99_000_000, contact_attempts=0, last_contact_at=None
    )
    assert chosen["action"] == "escalate_to_human"
    assert decision.allowed is True
    assert decision.action == "escalate_to_human"


def test_escalation_is_not_measured_against_the_value_cap():
    decision = evaluate_policy(
        base_input(
            proposed_action="escalate_to_human",
            amount_minor=99_000_000,
            predicted_probability=None,
            expected_recovery_minor=0,
        )
    )
    assert decision.allowed is True
    assert not any(r.rule == "amount_cap" for r in decision.rules)


def test_exhausted_contact_budget_closes_rather_than_escalating():
    """Escalation is for value we cannot decide, not for value we already chased."""
    ranked = [
        {"action": "send_payment_link", "probability": 0.82,
         "expected_recovery_minor": 400_000},
        {"action": "do_nothing", "probability": None, "expected_recovery_minor": 0},
    ]
    decision, chosen = select_action_under_policy(
        ranked, amount_minor=420_000, contact_attempts=9, last_contact_at=None
    )
    assert chosen["action"] == "do_nothing"


def test_kill_switch_suppresses_escalation_too(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "policy_kill_switch", True)
    ranked = [
        {"action": "send_payment_link", "probability": 0.82,
         "expected_recovery_minor": 4_000_000},
    ]
    _, chosen = select_action_under_policy(
        ranked, amount_minor=99_000_000, contact_attempts=0, last_contact_at=None
    )
    assert chosen["action"] == "do_nothing"
