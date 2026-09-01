"""Export privacy-safe, decision-time feature rows from verified real/Test Mode outcomes."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.database.models import DecisionAudit, Opportunity  # noqa: E402
from app.database.session import session_factory  # noqa: E402
from app.ml.features import FEATURE_NAMES, build_features  # noqa: E402

VALID_OUTCOMES = {"verified_recovered", "recovered_natural", "no_response", "expired", "cancelled"}
TRAINABLE_ACTIONS = {"send_payment_link", "send_reminder", "prompt_card_change", "wait_for_native_retry"}


async def export(output_dir: Path, include_test_mode: bool) -> dict:
    async with session_factory() as session:
        rows = (await session.execute(
            select(DecisionAudit, Opportunity)
            .join(Opportunity, DecisionAudit.opportunity_id == Opportunity.id)
            .where(Opportunity.is_synthetic.is_(False))
            .where(DecisionAudit.feature_snapshot.is_not(None))
            .where(DecisionAudit.verified_outcome.in_(VALID_OUTCOMES))
            .order_by(DecisionAudit.created_at.asc())
        )).all()

    exported = []
    source_counts: dict[str, int] = {}
    for audit, opportunity in rows:
        if opportunity.source == "razorpay_test" and not include_test_mode:
            continue
        action = audit.executed_action
        if action not in TRAINABLE_ACTIONS:
            continue
        features = build_features(dict(audit.feature_snapshot or {}))
        exported.append({
            **features,
            "action": action,
            "label": int(audit.verified_outcome in {"verified_recovered", "recovered_natural"}),
            "amount_minor": opportunity.amount_minor,
            "created_ts": audit.created_at.isoformat(),
            "opp_id": str(opportunity.id),
            "assignment_probability": opportunity.assignment_probability,
            "source": opportunity.source,
        })
        source_counts[opportunity.source] = source_counts.get(opportunity.source, 0) + 1

    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "verified_rows.csv"
    columns = FEATURE_NAMES + [
        "action", "label", "amount_minor", "created_ts", "opp_id",
        "assignment_probability", "source",
    ]
    frame = pd.DataFrame(exported, columns=columns)
    frame.to_csv(path, index=False)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "rows": len(frame),
        "source_counts": source_counts,
        "includes_test_mode": include_test_mode,
        "contains_pii": False,
        "sha256": digest,
        "schema": columns,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=REPO_ROOT / "data" / "verified")
    parser.add_argument("--include-test-mode", action="store_true")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(export(args.output_dir, args.include_test_mode)), indent=2))


if __name__ == "__main__":
    main()
