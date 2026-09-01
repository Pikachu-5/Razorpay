import json

import pytest

from app.ml.predictor import (
    HeuristicFallback,
    ModelRegistry,
    PredictionService,
    TrainedModelPredictor,
)


def _promote(directory, artifact_name: str) -> None:
    """Mark an artifact as promoted; nothing loads without this pointer."""
    (directory / "PROMOTED.json").write_text(json.dumps({"artifact": artifact_name}))


def sample_ctx(**overrides) -> dict:
    ctx = {
        "amount_minor": 800000,
        "method": "upi",
        "bank": "HDFC",
        "error_reason": "timeout",
        "is_subscription": False,
        "customer_prior_payments": 10,
        "customer_prior_successes": 8,
        "customer_prior_success_with_method": True,
        "prior_failed_same_instrument": 0,
        "minutes_since_failure": 5,
        "merchant_baseline_success": 0.88,
        "merchant_monthly_volume": 1000,
        "occurred_hour": 12,
        "occurred_weekday": 3,
    }
    ctx.update(overrides)
    return ctx


def test_fallback_returns_valid_probabilities_for_eligible_actions():
    result = HeuristicFallback().predict(sample_ctx())
    assert result.source == "fallback"
    assert result.degraded is True
    assert set(result.probabilities) == {"send_payment_link", "send_reminder"}
    assert all(0.0 <= p <= 1.0 for p in result.probabilities.values())


def test_fallback_subscription_includes_native_actions():
    result = HeuristicFallback().predict(sample_ctx(is_subscription=True))
    assert "prompt_card_change" in result.probabilities
    assert "wait_for_native_retry" in result.probabilities


def test_service_degrades_to_fallback_when_no_artifacts(tmp_path):
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx())
    assert prediction.source == "fallback"
    status = service.status()
    assert status["model_source"] == "fallback"


class _StubModel:
    def predict_proba(self, X):
        return [[0.1, 0.83] for _ in X]


def test_service_uses_trained_artifact_when_present(tmp_path):
    artifact = {
        "version": "v42",
        "feature_names": __import__("app.ml.features", fromlist=["FEATURE_NAMES"]).FEATURE_NAMES,
        "models": {
            "send_payment_link": _StubModel(),
            "send_reminder": _StubModel(),
            "prompt_card_change": _StubModel(),
            "wait_for_native_retry": _StubModel(),
        },
        "action_quality": {
            action: {"enabled": True} for action in (
                "send_payment_link", "send_reminder", "prompt_card_change", "wait_for_native_retry"
            )
        },
        "data_provenance": "shadow_live",
    }
    import joblib

    joblib.dump(artifact, tmp_path / "recovery_model_v42.pkl")
    (tmp_path / "PROMOTED.json").write_text(json.dumps({"artifact": "recovery_model_v42.pkl"}))

    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx())

    assert prediction.source == "trained_guarded"
    assert prediction.degraded is False
    assert prediction.model_version == "v42"
    assert prediction.probabilities["send_payment_link"] == pytest.approx(0.83)
    assert "prompt_card_change" not in prediction.probabilities

    status = service.status()
    assert status["model_source"] == "trained_guarded"
    assert status["promoted_pointer"] is True


def test_registry_promoted_pointer_wins_over_newer_file(tmp_path):
    import joblib

    old = {"version": "v1", "models": {}, "feature_names": []}
    new = {"version": "v2", "models": {}, "feature_names": []}
    joblib.dump(old, tmp_path / "recovery_model_v1.pkl")
    joblib.dump(new, tmp_path / "recovery_model_v2.pkl")
    (tmp_path / "PROMOTED.json").write_text(json.dumps({"artifact": "recovery_model_v1.pkl"}))

    registry = ModelRegistry(directory=tmp_path)
    loaded = registry.load()
    assert loaded["version"] == "v1"


def test_trained_predictor_rejects_feature_drift(tmp_path):
    bad = {
        "version": "v9",
        "feature_names": ["wrong", "features"],
        "models": {"send_payment_link": _StubModel()},
        "action_quality": {"send_payment_link": {"enabled": True}},
    }
    with pytest.raises(ValueError):
        TrainedModelPredictor(bad).action_probabilities(sample_ctx())


def test_corrupt_artifact_falls_back(tmp_path):
    (tmp_path / "recovery_model_v1.pkl").write_bytes(b"not a pickle")
    _promote(tmp_path, "recovery_model_v1.pkl")
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx())
    assert prediction.source == "fallback"


class _ConstantModel:
    """A stand-in trained model that always returns the same probability."""

    def __init__(self, probability: float) -> None:
        self._probability = probability

    def predict_proba(self, rows):
        return [[1.0 - self._probability, self._probability] for _ in rows]


def _artifact_with_one_enabled_action(**extra):
    from app.ml.features import FEATURE_NAMES

    artifact = {
        "version": "v9",
        "feature_names": list(FEATURE_NAMES),
        "models": {
            "send_payment_link": _ConstantModel(0.71),
            "send_reminder": _ConstantModel(0.99),
        },
        "action_quality": {
            "send_payment_link": {"enabled": True},
            # Quarantined: the offline gate did not trust this model.
            "send_reminder": {"enabled": False, "reasons": ["AUC < 0.55"]},
        },
    }
    artifact.update(extra)
    return artifact


def test_quarantined_action_falls_back_to_heuristic_instead_of_disappearing(tmp_path):
    """A distrusted model must not delete the action from the action space.

    Otherwise the cheapest action can never be chosen and expected-value
    ranking degenerates into "send a payment link, or do nothing".
    """
    import joblib

    joblib.dump(_artifact_with_one_enabled_action(), tmp_path / "recovery_model_v9.pkl")
    _promote(tmp_path, "recovery_model_v9.pkl")
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx())

    assert set(prediction.probabilities) == {"send_payment_link", "send_reminder"}
    assert prediction.action_sources["send_payment_link"] == "trained_guarded"
    assert prediction.action_sources["send_reminder"] == "heuristic"
    # The quarantined model's own 0.99 must not leak through.
    assert prediction.probabilities["send_reminder"] != 0.99
    assert prediction.probabilities["send_payment_link"] == 0.71
    assert prediction.degraded is True
    assert prediction.model_version == "v9"


def test_artifact_supplies_its_own_natural_recovery_baseline(tmp_path):
    import joblib

    from app.ml.baseline import DEFAULT_NATURAL_RECOVERY_BASELINE

    table = {"temporary": 0.11, "unknown": 0.22}
    joblib.dump(
        _artifact_with_one_enabled_action(natural_recovery_baseline=table),
        tmp_path / "recovery_model_v9.pkl",
    )
    _promote(tmp_path, "recovery_model_v9.pkl")
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx(error_reason="timeout"))

    assert prediction.natural_recovery_probability == 0.11
    assert prediction.baseline_source == "artifact:v9"
    assert (
        DEFAULT_NATURAL_RECOVERY_BASELINE["temporary"]
        != prediction.natural_recovery_probability
    )


def test_malformed_artifact_baseline_is_rejected_whole(tmp_path):
    """A half-valid table would silently distort every expected-value figure."""
    import joblib

    from app.ml.baseline import DEFAULT_NATURAL_RECOVERY_BASELINE

    joblib.dump(
        _artifact_with_one_enabled_action(
            natural_recovery_baseline={"temporary": 4.2, "unknown": 0.3}
        ),
        tmp_path / "recovery_model_v9.pkl",
    )
    _promote(tmp_path, "recovery_model_v9.pkl")
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    prediction = service.predict(sample_ctx(error_reason="timeout"))

    assert prediction.natural_recovery_probability == (
        DEFAULT_NATURAL_RECOVERY_BASELINE["temporary"]
    )
    assert prediction.baseline_source == "default_table"


def test_unpromoted_artifact_never_goes_live(tmp_path):
    """Dropping a .pkl in the directory must not bypass the promotion gate."""
    import joblib

    joblib.dump(_artifact_with_one_enabled_action(), tmp_path / "recovery_model_v9.pkl")
    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())

    prediction = service.predict(sample_ctx())
    assert prediction.source == "fallback"
    assert prediction.model_version == HeuristicFallback.version
    assert service.status()["model_source"] == "fallback"


def test_promotion_pointer_cannot_escape_the_artifacts_directory(tmp_path):
    import joblib

    joblib.dump(_artifact_with_one_enabled_action(), tmp_path / "recovery_model_v9.pkl")
    _promote(tmp_path, "../../../etc/recovery_model_v9.pkl")

    service = PredictionService(ModelRegistry(directory=tmp_path), HeuristicFallback())
    assert service.predict(sample_ctx()).source == "fallback"
