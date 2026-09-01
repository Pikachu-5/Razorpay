import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from app.api.auth import OperatorPrincipal, require_admin, require_operator
from app.ml.features import FEATURE_NAMES
from app.ml.predictor import (
    PROMOTED_POINTER,
    artifacts_dir,
    get_prediction_service,
    reset_prediction_service,
)

router = APIRouter(prefix="/api/ml", tags=["ml"])
ARTIFACT_NAME = re.compile(r"^recovery_model_v[0-9A-Za-z._-]+\.pkl$")


def _version_key(path: Path) -> tuple:
    return tuple(int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.stem))


def _write_promoted_pointer(directory: Path, payload: dict[str, Any]) -> None:
    """Replace the promotion pointer atomically, never exposing partial JSON."""
    fd, temporary_name = tempfile.mkstemp(prefix=".PROMOTED-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            json.dump(payload, temporary_file, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, directory / PROMOTED_POINTER)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise


@router.get("/status")
async def ml_status() -> dict:
    return get_prediction_service().status()


@router.get("/model-card")
async def model_card() -> dict[str, Any]:
    service = get_prediction_service()
    status = service.status()
    version = status.get("model_version")
    directory = artifacts_dir()

    if version and version != "heuristic-v1":
        card_path = directory / f"model_card_{version}.json"
        if card_path.exists():
            try:
                return json.loads(card_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    return {
        "version": version or "heuristic-v1",
        "model_type": "deterministic_heuristic_fallback",
        "per_action": {"validation": {}, "test": {}},
        "economics_test": None,
        "note": "No trained model card is installed. Runtime is using the documented heuristic fallback.",
    }

class PromotionRequest(BaseModel):
    artifact: str | None = Field(default=None, description="Artifact filename, e.g. recovery_model_v2.pkl")
    version: str | None = Field(default=None, description="Version string, e.g. v2")
    force: bool = Field(default=False, description="Bypass threshold gates")
    target_tier: str = Field(default="shadow", pattern="^(demo|shadow|production)$")


@router.get("/comparison")
async def model_comparison() -> dict[str, Any]:
    directory = artifacts_dir()
    if not directory.exists():
        return {"artifacts": [], "champion": None, "challengers": []}

    cards = sorted(directory.glob("model_card_v*.json"), key=_version_key)
    loaded_cards = []
    for c in cards:
        try:
            loaded_cards.append(json.loads(c.read_text(encoding="utf-8")))
        except Exception:
            pass

    service = get_prediction_service()
    status = service.status()
    current_version = status.get("model_version")

    champion = next((c for c in loaded_cards if c.get("version") == current_version), None)
    challengers = [c for c in loaded_cards if c != champion]

    return {
        "active_version": current_version,
        "champion": champion,
        "challengers": challengers,
        "all_cards": loaded_cards,
    }


@router.post("/promote")
async def promote_model(
    req: PromotionRequest,
    operator: OperatorPrincipal = Depends(require_operator),
    x_control_plane_key: str | None = Header(default=None),
) -> dict[str, Any]:
    if req.force:
        # Re-run the dependency explicitly only for the dangerous override;
        # normal promotions remain available to an operator.
        await require_admin(x_control_plane_key)
    directory = artifacts_dir()
    if not directory.exists():
        raise HTTPException(status_code=404, detail="Artifacts directory not found")

    target_artifact = req.artifact
    if not target_artifact and req.version:
        target_artifact = f"recovery_model_{req.version}.pkl"

    if not target_artifact:
        # Default to latest artifact
        artifacts = sorted(directory.glob("recovery_model_v*.pkl"), key=_version_key)
        if not artifacts:
            raise HTTPException(status_code=404, detail="No recovery model artifacts found to promote")
        target_artifact = artifacts[-1].name

    if Path(target_artifact).name != target_artifact or not ARTIFACT_NAME.fullmatch(target_artifact):
        raise HTTPException(status_code=400, detail="Artifact must be a recovery_model_v*.pkl filename")

    artifact_path = (directory / target_artifact).resolve()
    if artifact_path.parent != directory.resolve():
        raise HTTPException(status_code=400, detail="Artifact must stay inside the model registry")
    if not artifact_path.exists():
        raise HTTPException(status_code=404, detail=f"Artifact {target_artifact} does not exist")

    # Extract version
    version = req.version or target_artifact.replace("recovery_model_", "").replace(".pkl", "")
    card_path = directory / f"model_card_{version}.json"
    card_data = None
    if card_path.exists():
        try:
            card_data = json.loads(card_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    if card_data is None:
        raise HTTPException(
            status_code=400,
            detail="A valid model card is required before an artifact can be promoted",
        )
    if str(card_data.get("version")) != version:
        raise HTTPException(status_code=400, detail="Artifact version does not match its model card")
    try:
        artifact_data = joblib.load(artifact_path)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Artifact could not be loaded: {type(exc).__name__}") from None
    if not isinstance(artifact_data, dict) or not artifact_data.get("models"):
        raise HTTPException(status_code=400, detail="Artifact does not contain trained action models")
    if str(artifact_data.get("version")) != version:
        raise HTTPException(status_code=400, detail="Loaded artifact version does not match request")
    if artifact_data.get("feature_names") != FEATURE_NAMES:
        raise HTTPException(status_code=400, detail="Artifact feature schema does not match the runtime")
    quality = artifact_data.get("action_quality") or {}
    if not any(item.get("enabled") is True for item in quality.values()):
        raise HTTPException(status_code=400, detail="Artifact has no action model that passed quality gates")
    provenance = artifact_data.get("data_provenance")
    if req.target_tier == "production" and provenance != "live":
        raise HTTPException(
            status_code=400,
            detail="Production promotion requires an artifact trained from explicitly labeled live data",
        )

    # Evaluate promotion rules
    rules = []
    passed_all = True

    if card_data:
        test_metrics = card_data.get("per_action", {}).get("test", {})
        if not test_metrics:
            raise HTTPException(status_code=400, detail="Model card has no held-out test metrics")
        link_auc = test_metrics.get("send_payment_link", {}).get("roc_auc", 0.0)
        link_auc_pass = quality.get("send_payment_link", {}).get("enabled") is True and link_auc >= 0.55
        rules.append({
            "rule": "primary_action_auc",
            "passed": link_auc_pass,
            "detail": f"send_payment_link enabled with AUC={link_auc:.3f} >= 0.55",
        })
        if not link_auc_pass:
            passed_all = False

        # Calibration check
        max_gap = 0.0
        enabled_actions = {action for action, item in quality.items() if item.get("enabled") is True}
        for act, m in test_metrics.items():
            if act not in enabled_actions:
                continue
            gap = abs(m.get("mean_predicted", 0) - m.get("positive_rate", 0))
            if gap > max_gap:
                max_gap = gap
        cal_pass = max_gap <= 0.12
        rules.append({
            "rule": "probability_calibration",
            "passed": cal_pass,
            "detail": f"Max calibration gap={max_gap * 100:.1f}% <= 12%",
        })
        if not cal_pass:
            passed_all = False

        # Economics check: net incremental recovery against the strongest
        # policy that uses no model at all (rank by amount, always send a
        # link). Beating a random or average-value baseline proves nothing --
        # expected value is dominated by amount, so sorting by size alone
        # already clears those.
        econ = card_data.get("economics_test", {})
        econ_lift = econ.get("lift_pct", 0.0)
        econ_pass = econ_lift >= 0.0
        rules.append({
            "rule": "economics_lift",
            "passed": econ_pass,
            "detail": (
                f"Net incremental lift {econ_lift:+.1f}% vs the value-ranked "
                f"no-model baseline (needs >= 0%)"
            ),
        })
        if not econ_pass:
            passed_all = False

    if not passed_all and not req.force:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Candidate model failed promotion guardrails. Use force=true to override.",
                "rules": rules,
            },
        )

    # Write PROMOTED.json atomically
    pointer_data = {
        "artifact": target_artifact,
        "version": version,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "promoted_by": "automated_comparator",
        "rules_evaluated": rules,
        "target_tier": req.target_tier,
        "data_provenance": provenance,
    }
    _write_promoted_pointer(directory, pointer_data)

    # Reset cache so runtime immediately serves new model
    reset_prediction_service()

    return {
        "promoted": True,
        "artifact": target_artifact,
        "version": version,
        "rules": rules,
        "status": get_prediction_service().status(),
    }
