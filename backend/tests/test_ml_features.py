
from app.ml.actions import ACTIONS, eligible_actions
from app.ml.features import FEATURE_NAMES, build_features, error_group, feature_vector


def sample_ctx(**overrides) -> dict:
    ctx = {
        "amount_minor": 420000,
        "method": "upi",
        "bank": "HDFC",
        "error_reason": "timeout",
        "is_subscription": False,
        "customer_prior_payments": 12,
        "customer_prior_successes": 9,
        "customer_prior_success_with_method": True,
        "prior_failed_same_instrument": 1,
        "minutes_since_failure": 7.5,
        "merchant_baseline_success": 0.88,
        "merchant_monthly_volume": 42000,
        "occurred_hour": 14,
        "occurred_weekday": 2,
    }
    ctx.update(overrides)
    return ctx


def test_features_complete_and_deterministic():
    a = build_features(sample_ctx())
    b = build_features(sample_ctx())
    assert set(a.keys()) == set(FEATURE_NAMES)
    assert a == b


def test_feature_vector_order_stable():
    vec = feature_vector(sample_ctx())
    feats = build_features(sample_ctx())
    assert vec == [feats[name] for name in FEATURE_NAMES]
    assert all(isinstance(v, float) for v in vec)


def test_error_grouping():
    assert error_group("timeout") == "temporary"
    assert error_group("network_error") == "temporary"
    assert error_group("authentication_required") == "auth"
    assert error_group("insufficient_funds") == "insufficient_funds"
    assert error_group("card_expired") == "instrument"
    assert error_group(None) == "unknown"
    assert error_group("mystery_reason") == "unknown"


def test_eligibility_rules():
    one_off = eligible_actions(False)
    sub = eligible_actions(True)

    assert set(one_off) < set(ACTIONS)
    assert "prompt_card_change" not in one_off
    assert "wait_for_native_retry" not in one_off
    assert "send_payment_link" in one_off
    assert "do_nothing" in one_off

    assert set(sub) == set(ACTIONS)
