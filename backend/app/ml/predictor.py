from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib

from app.ml.actions import predictable_actions
from app.ml.baseline import (
    BASELINE_TABLE_KEY,
    DEFAULT_NATURAL_RECOVERY_BASELINE,
    natural_recovery_probability,
    sanitize_baseline_table,
)
from app.ml.features import FEATURE_NAMES as FEATURE_NAMES_ORDER
from app.ml.features import error_group, feature_vector

MODELS_DIR_NAME = "models/artifacts"
PROMOTED_POINTER = "PROMOTED.json"


@dataclass(frozen=True)
class Prediction:
    probabilities: dict[str, float]
    source: str
    model_version: str | None
    degraded: bool
    # Where each individual probability came from. A quarantined action falls
    # back to the heuristic rather than disappearing, so a single prediction can
    # legitimately mix sources; the operator is shown which is which.
    action_sources: dict[str, str] = field(default_factory=dict)
    # P(recovery | no intervention) for this context, used to convert raw
    # recovery probabilities into *incremental* ones.
    natural_recovery_probability: float = DEFAULT_NATURAL_RECOVERY_BASELINE["unknown"]
    baseline_source: str = "default_table"


def artifacts_dir() -> Path:
    from app.core.config import get_settings

    configured = get_settings().model_artifacts_dir
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[3] / MODELS_DIR_NAME


class HeuristicFallback:
    version = "heuristic-v1"

    def action_probabilities(self, ctx: dict) -> dict[str, float]:
        group = error_group(ctx.get("error_reason"))
        n = float(ctx.get("customer_prior_payments") or 0)
        s = float(ctx.get("customer_prior_successes") or 0)
        prior_rate = (s / n) if n > 0 else 0.5

        base_temporary = {"send_payment_link": 0.55, "send_reminder": 0.35,
                          "prompt_card_change": 0.25, "wait_for_native_retry": 0.45}
        base_other = {"send_payment_link": 0.30, "send_reminder": 0.20,
                      "prompt_card_change": 0.15, "wait_for_native_retry": 0.15}
        table = base_temporary if group == "temporary" else base_other

        probs: dict[str, float] = {}
        for action in predictable_actions(bool(ctx.get("is_subscription"))):
            p = table[action] * (0.7 + 0.6 * min(prior_rate, 1.0))
            if group == "instrument":
                p *= 0.5
            probs[action] = max(0.02, min(0.95, p))
        return probs

    def predict(self, ctx: dict) -> Prediction:
        probs = self.action_probabilities(ctx)
        return Prediction(
            probabilities=probs,
            source="fallback",
            model_version=self.version,
            degraded=True,
            action_sources={action: "heuristic" for action in probs},
            natural_recovery_probability=natural_recovery_probability(ctx),
            baseline_source="default_table",
        )


class TrainedModelPredictor:
    def __init__(self, artifact: dict[str, Any]) -> None:
        quality = artifact.get("action_quality") or {}
        self._models: dict[str, Any] = {
            action: model for action, model in artifact["models"].items()
            if quality.get(action, {}).get("enabled") is True
        }
        self._all_model_count = len(artifact["models"])
        if not self._models:
            raise ValueError("artifact has no action model that passed quality gates")
        self._feature_names: list[str] = artifact["feature_names"]
        self.version = str(artifact.get("version", "unknown"))
        self._baseline_table = sanitize_baseline_table(artifact.get(BASELINE_TABLE_KEY))

    def action_probabilities(self, ctx: dict) -> dict[str, float]:
        if list(self._feature_names) != list(FEATURE_NAMES_ORDER):
            raise ValueError("artifact feature contract drift")
        vector = feature_vector(ctx)
        probs: dict[str, float] = {}
        for action in predictable_actions(bool(ctx.get("is_subscription"))):
            model = self._models.get(action)
            if model is None:
                continue
            raw = model.predict_proba([vector])[0][1]
            probs[action] = max(0.0, min(1.0, float(raw)))
        return probs

    def natural_recovery(self, ctx: dict) -> tuple[float, str]:
        if self._baseline_table is not None:
            return (
                natural_recovery_probability(ctx, self._baseline_table),
                f"artifact:{self.version}",
            )
        return natural_recovery_probability(ctx), "default_table"


class ModelRegistry:
    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or artifacts_dir()
        self._cached_artifact: dict[str, Any] | None = None
        self._cached_path: Path | None = None

    def _select_artifact_path(self) -> Path | None:
        """Resolve the promoted artifact, and only the promoted artifact.

        Promotion is the gate this whole system is built around: a model earns
        production traffic by passing accuracy, calibration and economics
        checks.  Falling back to "whatever .pkl has the highest version number"
        walked straight past that gate -- dropping a freshly trained, ungated
        artifact into the directory was enough to put it on live traffic.  With
        no valid pointer the service runs on the heuristic instead, which is
        the honest degraded state.
        """
        if not self._directory.exists():
            return None
        pointer = self._directory / PROMOTED_POINTER
        if not pointer.exists():
            return None
        try:
            name = json.loads(pointer.read_text()).get("artifact")
        except (json.JSONDecodeError, AttributeError, OSError):
            return None
        if not name or not isinstance(name, str):
            return None
        # Never let a pointer escape the artifacts directory.
        if not re.fullmatch(r"recovery_model_v[\w.]+\.pkl", name):
            return None
        candidate = self._directory / name
        return candidate if candidate.exists() else None

    def load(self) -> dict[str, Any] | None:
        path = self._select_artifact_path()
        if path is None:
            return None
        if path != self._cached_path or self._cached_artifact is None:
            try:
                self._cached_artifact = joblib.load(path)
                self._cached_path = path
            except Exception:
                self._cached_artifact = None
                self._cached_path = None
                return None
        return self._cached_artifact


_service: "PredictionService" | None = None
_registry = ModelRegistry()


class PredictionService:
    def __init__(self, registry: ModelRegistry, fallback: HeuristicFallback) -> None:
        self._registry = registry
        self._fallback = fallback

    def predict(self, ctx: dict) -> Prediction:
        """Score every eligible action, per action.

        A quarantined action is one the trained model is not trusted for -- it
        is not an action the business stopped supporting.  Dropping it from the
        result would collapse the action space to whatever happened to pass the
        offline gate (in practice: send a payment link, or do nothing), so a
        ₹300 opportunity could never be answered with a ₹3 reminder.  Actions
        without a trusted model therefore fall back to the heuristic, and every
        probability carries the source it came from.
        """
        try:
            artifact = self._registry.load()
        except Exception:
            artifact = None

        trained: TrainedModelPredictor | None = None
        trained_probs: dict[str, float] = {}
        if artifact and artifact.get("models"):
            try:
                trained = TrainedModelPredictor(artifact)
                trained_probs = trained.action_probabilities(ctx)
            except Exception:
                trained = None
                trained_probs = {}

        heuristic_probs = self._fallback.action_probabilities(ctx)

        probabilities: dict[str, float] = {}
        sources: dict[str, str] = {}
        for action in predictable_actions(bool(ctx.get("is_subscription"))):
            if action in trained_probs:
                probabilities[action] = trained_probs[action]
                sources[action] = "trained_guarded"
            elif action in heuristic_probs:
                probabilities[action] = heuristic_probs[action]
                sources[action] = "heuristic"

        if trained is None:
            return self._fallback.predict(ctx)

        baseline, baseline_source = trained.natural_recovery(ctx)
        return Prediction(
            probabilities=probabilities,
            source="trained_guarded" if trained_probs else "fallback",
            model_version=trained.version,
            # "Degraded" now means the artifact could not cover the whole
            # action space, not that the action vanished.
            degraded=any(source == "heuristic" for source in sources.values()),
            action_sources=sources,
            natural_recovery_probability=baseline,
            baseline_source=baseline_source,
        )

    def status(self) -> dict[str, Any]:
        try:
            artifact = self._registry.load()
        except Exception:
            artifact = None
        pointer = self._registry._directory / PROMOTED_POINTER
        promoted = False
        if pointer.exists() and artifact is not None:
            try:
                promoted = (
                    json.loads(pointer.read_text()).get("artifact")
                    == f"recovery_model_{artifact['version']}.pkl"
                )
            except Exception:
                promoted = False
        enabled = sorted(
            action for action, quality in (artifact or {}).get("action_quality", {}).items()
            if quality.get("enabled") is True
        )
        trained = sorted((artifact or {}).get("models", {}).keys())
        baseline_table = sanitize_baseline_table((artifact or {}).get(BASELINE_TABLE_KEY))
        return {
            "model_source": "trained_guarded" if artifact and artifact.get("action_quality") else "fallback",
            "model_version": str(artifact["version"]) if artifact else HeuristicFallback.version,
            "actions_trained": trained,
            "actions_enabled": enabled,
            # Quarantined actions are still decidable -- the heuristic covers
            # them -- so the console can say so instead of implying they are off.
            "actions_heuristic_fallback": sorted(set(trained) - set(enabled)),
            "natural_recovery_baseline": baseline_table or DEFAULT_NATURAL_RECOVERY_BASELINE,
            "natural_recovery_baseline_source": (
                f"artifact:{artifact['version']}" if baseline_table else "default_table"
            ),
            "data_provenance": (artifact or {}).get("data_provenance"),
            "promoted_pointer": promoted,
            "artifacts_dir": str(self._registry._directory),
        }


def get_prediction_service() -> PredictionService:
    global _service
    if _service is None:
        _service = PredictionService(_registry, HeuristicFallback())
    return _service


def reset_prediction_service() -> None:
    global _service
    _service = None
