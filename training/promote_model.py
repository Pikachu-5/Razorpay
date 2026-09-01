"""Automated ML Model Promotion Gate and Comparator.

Compares a challenger model against champion / baseline on held-out test splits,
checks safety and calibration guardrails, and atomically promotes valid models.

Usage:
  python training/promote_model.py --challenger v2
  python training/promote_model.py --challenger v2 --force
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROMOTED_POINTER = "PROMOTED.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Automated Model Promotion Gate")
    parser.add_argument(
        "--challenger",
        type=str,
        required=True,
        help="Challenger model version (e.g., v2) or artifact filename (recovery_model_v2.pkl)",
    )
    parser.add_argument("--target-tier", choices=("demo", "shadow", "production"), default="shadow")
    parser.add_argument(
        "--champion",
        type=str,
        default=None,
        help="Champion model version (defaults to current promoted model or v1)",
    )
    parser.add_argument(
        "--artifacts-dir",
        type=Path,
        default=Path("models/artifacts"),
        help="Path to models artifacts directory",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force promotion even if non-critical thresholds are not met",
    )
    return parser.parse_args()


def load_model_card(artifacts_dir: Path, version_or_file: str) -> dict:
    version = version_or_file.replace("recovery_model_", "").replace(".pkl", "")
    card_path = artifacts_dir / f"model_card_{version}.json"
    if not card_path.exists():
        raise FileNotFoundError(f"Model card not found at {card_path}")
    return json.loads(card_path.read_text(encoding="utf-8"))


def main():
    # Windows terminals default to cp1252. Make output encoding-safe before the
    # first print rather than halfway down the happy path -- the rejection
    # branch printed a non-ASCII character and crashed before reaching it.
    for stream in (sys.stdout, sys.stderr):
        encoding = getattr(stream, "encoding", "") or ""
        if encoding.lower() not in ("utf-8", "utf8"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

    args = parse_args()
    artifacts_dir: Path = args.artifacts_dir.resolve()
    if not artifacts_dir.exists():
        print(f"ERROR: Artifacts directory does not exist: {artifacts_dir}", file=sys.stderr)
        sys.exit(1)

    challenger_ver = args.challenger.replace("recovery_model_", "").replace(".pkl", "")
    challenger_card = load_model_card(artifacts_dir, challenger_ver)
    provenance = challenger_card.get("data_provenance")
    if args.target_tier == "production" and provenance != "live":
        print("ERROR: production promotion requires data_provenance=live", file=sys.stderr)
        sys.exit(1)

    # Determine champion
    champion_ver = args.champion
    if not champion_ver:
        promoted_file = artifacts_dir / PROMOTED_POINTER
        if promoted_file.exists():
            try:
                promoted_data = json.loads(promoted_file.read_text(encoding="utf-8"))
                champion_ver = promoted_data.get("version") or promoted_data.get("artifact", "").replace("recovery_model_", "").replace(".pkl", "")
            except Exception:
                pass
    if not champion_ver and challenger_ver != "v1":
        champion_ver = "v1"

    champion_card = None
    if champion_ver and champion_ver != challenger_ver:
        try:
            champion_card = load_model_card(artifacts_dir, champion_ver)
        except Exception:
            champion_card = None

    print("\n=======================================================")
    print("  MODEL PROMOTION COMPARATOR")
    print(f"  Challenger: {challenger_ver} vs Champion: {champion_ver or 'None (Baseline)'}")
    print("=======================================================\n")

    # Metrics Table Comparison
    challenger_test = challenger_card.get("per_action", {}).get("test", {})
    challenger_quality = challenger_card.get("action_quality", {})
    champion_test = (champion_card or {}).get("per_action", {}).get("test", {})

    print(f"{'Action':<25} | {'Champion AUC':<14} | {'Challenger AUC':<14} | {'Calib Gap':<10} | {'Status'}")
    print("-" * 75)

    all_actions = sorted(set(list(challenger_test.keys()) + list(champion_test.keys())))
    passed_all = True
    rules = []

    for action in all_actions:
        chal_m = challenger_test.get(action, {})
        champ_m = champion_test.get(action, {})

        chal_auc = chal_m.get("roc_auc", 0.0)
        champ_auc = champ_m.get("roc_auc", 0.0)
        cal_gap = abs(chal_m.get("mean_predicted", 0) - chal_m.get("positive_rate", 0))

        status_str = "OK"
        if challenger_quality.get(action, {}).get("enabled") is not True:
            status_str = "QUARANTINED"
        elif int(chal_m.get("n", 0)) < 100:
            status_str = "WARN (Small Test Set)"
            passed_all = False
        elif chal_auc < 0.55:
            status_str = "WARN (Low AUC)"
            passed_all = False
        elif cal_gap > 0.12:
            status_str = "WARN (Calib)"
            passed_all = False

        champ_auc_str = f"{champ_auc:.3f}" if champ_m else "N/A"
        print(f"{action:<25} | {champ_auc_str:<14} | {chal_auc:<14.3f} | {cal_gap * 100:<9.1f}% | {status_str}")

    # Economics Lift Comparison
    chal_lift = challenger_card.get("economics_test", {}).get("lift_pct", 0.0)
    champ_lift = (champion_card or {}).get("economics_test", {}).get("lift_pct", 0.0)

    print("\n-------------------------------------------------------")
    print(
        f"Net incremental lift vs strongest no-model policy: "
        f"Challenger: {chal_lift:+.1f}% | Champion: {champ_lift:+.1f}%"
    )
    print("-------------------------------------------------------")

    # Record the rule whichever way it goes: a PROMOTED.json listing only the
    # rules that passed is not an audit trail.
    economics_passed = chal_lift >= 0
    rules.append({
        "rule": "economics_lift",
        "passed": economics_passed,
        "detail": f"{chal_lift:+.1f}% net incremental lift vs value-ranked baseline",
    })
    if not economics_passed:
        print("FAIL: challenger does not beat ranking by amount with no model at all.")
        passed_all = False

    # Promotion Decision
    if not passed_all and not args.force:
        print(f"\n[REJECTED] Challenger {challenger_ver} did not satisfy all promotion guardrails.")
        print("Use --force if you wish to override and promote regardless.")
        sys.exit(1)

    # Write PROMOTED.json
    promoted_payload = {
        "artifact": f"recovery_model_{challenger_ver}.pkl",
        "version": challenger_ver,
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "promoted_by": "cli_promotion_comparator",
        "lift_pct": chal_lift,
        "rules_evaluated": rules,
        "forced": bool(args.force),
        "target_tier": args.target_tier,
        "data_provenance": provenance,
    }
    promoted_path = artifacts_dir / PROMOTED_POINTER
    fd, temporary_name = tempfile.mkstemp(prefix=".PROMOTED-", suffix=".tmp", dir=artifacts_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temporary_file:
            json.dump(promoted_payload, temporary_file, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, promoted_path)
    except Exception:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
        raise

    print(f"\n[PROMOTED] recovery_model_{challenger_ver}.pkl is now the active model artifact!")
    print(f"Updated {promoted_path}")



if __name__ == "__main__":
    main()
