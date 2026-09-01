
from app.agents.diagnosis import diagnose
from app.ml.predictor import Prediction


def test_diagnosis_temporary():
    d = diagnose(error_reason="timeout", is_subscription=False,
                 prior_failed_same_instrument=0, method="upi", bank="HDFC")
    assert d.classification == "transient_failure"
    assert d.confidence >= 0.85
    assert any("error_reason=timeout" in e for e in d.evidence)


def test_diagnosis_instrument_strengthens_with_history():
    d = diagnose(error_reason="card_expired", is_subscription=False,
                 prior_failed_same_instrument=3, method="card", bank="ICICI")
    assert d.classification == "dead_instrument"
    assert any("3 prior failures" in e for e in d.evidence)
    assert d.confidence > 0.88


def test_diagnosis_auth_subscription_notes_native_retry():
    d = diagnose(error_reason="authentication_required", is_subscription=True,
                 prior_failed_same_instrument=0, method="card", bank=None)
    assert d.classification == "instrument_needs_update"
    assert any("native Razorpay retry" in e for e in d.evidence)


class _StubService:
    def __init__(self, probabilities, baseline: float = 0.0):
        self._probabilities = probabilities
        self._baseline = baseline

    def __init_baseline__(self):
        pass

    def predict(self, ctx):
        return Prediction(
            probabilities=self._probabilities,
            source="stub",
            model_version="stub-v1",
            degraded=False,
            action_sources={a: "stub" for a in self._probabilities},
            natural_recovery_probability=self._baseline,
        )


def test_revenue_ranking_orders_by_expected_value(monkeypatch):
    import app.agents.revenue as revenue

    monkeypatch.setattr(
        revenue,
        "get_prediction_service",
        lambda: _StubService({"send_payment_link": 0.40, "send_reminder": 0.90}),
    )
    ranking = revenue.rank_actions({"is_subscription": False}, amount_minor=100_000_00)

    assert ranking.best_action == "send_reminder"
    expected_reminder = int(0.90 * 100_000_00 - 300)
    assert ranking.ranked[0]["expected_recovery_minor"] == expected_reminder
    assert ranking.model_version == "stub-v1"
    actions = [r["action"] for r in ranking.ranked]
    assert "do_nothing" in actions


def test_expected_value_is_net_of_the_do_nothing_counterfactual(monkeypatch):
    """A 70% action against a 60% natural-recovery rate is worth 10%, not 70%."""
    import app.agents.revenue as revenue

    monkeypatch.setattr(
        revenue,
        "get_prediction_service",
        lambda: _StubService({"send_payment_link": 0.70}, baseline=0.60),
    )
    ranking = revenue.rank_actions({"is_subscription": False}, amount_minor=100_000_00)

    link = next(r for r in ranking.ranked if r["action"] == "send_payment_link")
    assert link["probability"] == 0.7
    assert link["incremental_probability"] == 0.1
    assert link["gross_recovery_minor"] == int(0.70 * 100_000_00)
    assert link["expected_recovery_minor"] == int(0.10 * 100_000_00 - 1500)
    assert ranking.natural_recovery_probability == 0.60


def test_action_no_better_than_doing_nothing_is_not_worth_its_cost(monkeypatch):
    """A high raw probability that only matches the baseline must not win."""
    import app.agents.revenue as revenue

    monkeypatch.setattr(
        revenue,
        "get_prediction_service",
        lambda: _StubService({"send_payment_link": 0.80}, baseline=0.80),
    )
    ranking = revenue.rank_actions({"is_subscription": False}, amount_minor=100_000_00)

    assert ranking.best_action == "do_nothing"
    link = next(r for r in ranking.ranked if r["action"] == "send_payment_link")
    assert link["incremental_probability"] == 0.0
    # Ranking on gross recovery would have valued this at 80 lakh.
    assert link["expected_recovery_minor"] == -1500
