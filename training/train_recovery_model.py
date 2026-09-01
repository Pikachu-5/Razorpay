from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))


def _portable_path(path: Path) -> str:
    """Render a path relative to the repo root, with forward slashes.

    Model cards are checked in, so an absolute training-host path makes the
    artifact non-reproducible on any other machine.
    """
    try:
        relative = path.resolve().relative_to(REPO_ROOT)
    except ValueError:
        return path.as_posix()
    return relative.as_posix()

from app.ml.actions import INTERVENTION_COST_MINOR  # noqa: E402
from app.ml.baseline import (  # noqa: E402
    BASELINE_TABLE_KEY,
    DEFAULT_NATURAL_RECOVERY_BASELINE,
    sanitize_baseline_table,
)
from app.ml.features import FEATURE_NAMES, build_features  # noqa: E402

MIN_POSITIVES = 60
MIN_TEST_ROWS = 100
MIN_TEST_AUC = 0.55
MAX_CALIBRATION_GAP = 0.12

# Pseudo-action marking a decision-time observation of "we did not intervene".
# These rows train nothing; they measure the counterfactual every expected-value
# calculation is scored against.
BASELINE_ACTION = "do_nothing"
MIN_BASELINE_ROWS_PER_GROUP = 50

ERROR_GROUPS = ("temporary", "auth", "insufficient_funds", "instrument", "unknown")


def error_group_from_features(row) -> str:
    """Recover the failure class from the one-hot feature columns."""
    for group in ERROR_GROUPS:
        if float(row.get(f"error_{group}", 0.0)) == 1.0:
            return group
    return "unknown"


def natural_recovery_baseline(rows: pd.DataFrame) -> tuple[dict[str, float], str]:
    """Measure P(recovery | no intervention) per failure class from the data.

    Groups with too few observations inherit the pooled rate rather than a noisy
    one, and a dataset with no baseline rows at all falls back to the checked-in
    table so training never silently invents a counterfactual.
    """
    baseline_rows = rows[rows.action == BASELINE_ACTION]
    if len(baseline_rows) < MIN_BASELINE_ROWS_PER_GROUP:
        return dict(DEFAULT_NATURAL_RECOVERY_BASELINE), "default_table"

    pooled = float(baseline_rows.label.mean())
    table: dict[str, float] = {"unknown": round(pooled, 4)}
    groups = baseline_rows.apply(error_group_from_features, axis=1)
    for group, subset in baseline_rows.groupby(groups):
        table[str(group)] = (
            round(float(subset.label.mean()), 4)
            if len(subset) >= MIN_BASELINE_ROWS_PER_GROUP
            else round(pooled, 4)
        )
    for group in ERROR_GROUPS:
        table.setdefault(group, round(pooled, 4))
    return table, f"measured:{len(baseline_rows)}_rows"


def load_contexts(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    attempts = pd.read_csv(data_dir / "attempts.csv")
    opps = pd.read_csv(data_dir / "opportunities.csv")
    interventions = pd.read_csv(data_dir / "interventions.csv")
    return attempts, opps, interventions


def build_supervised_rows(
    opps: pd.DataFrame,
    interventions: pd.DataFrame,
    seed: int = 13,
) -> pd.DataFrame:
    """
    Label semantics matter:

    - Active actions (link/reminder/card-change): trained ONLY on opportunities
      where that action was actually executed. Label = did the customer pay
      afterwards. This yields P(recovery | action executed), which is what the
      expected-value math needs. Selection bias (ops chose which cases to work)
      is acknowledged and corrected for by the live control/treatment
      experiment, not by mixing unpushed cases in as negatives.
    - wait_for_native_retry: trained on NON-intervened subscription
      opportunities with label = natural recovery, because that is literally
      the action's meaning (let Razorpay's native retry run).
    """
    del seed
    opps = opps.copy()
    opps["created_ts"] = pd.to_datetime(opps["created_ts"])
    interventions_by_opp = {
        opp_id: group for opp_id, group in interventions.groupby("opp_id")
    }

    rows: list[dict] = []
    intervened_opps: set[str] = set()

    for opp in opps.itertuples(index=False):
        ivs = interventions_by_opp.get(opp.opp_id)
        if ivs is None or len(ivs) == 0:
            continue
        intervened_opps.add(opp.opp_id)
        ctx = context_from_row(opp)
        for iv in ivs.itertuples(index=False):
            rows.append(row_for(ctx, opp, iv.action, int(iv.success)))

    for opp in opps.itertuples(index=False):
        if opp.opp_id in intervened_opps:
            continue
        ctx = context_from_row(opp)
        if opp.is_subscription:
            rows.append(row_for(ctx, opp, "wait_for_native_retry", int(opp.natural_recovered)))
        # Every non-intervened opportunity is also an observation of the
        # do-nothing counterfactual. These rows train no model; they measure
        # the baseline that expected value is calculated against.
        rows.append(row_for(ctx, opp, BASELINE_ACTION, int(opp.natural_recovered)))

    return pd.DataFrame(rows)


def context_from_row(opp) -> dict:
    return {
        "amount_minor": float(opp.amount_minor),
        "method": str(opp.method),
        "bank": str(opp.bank),
        "error_reason": str(opp.error_reason),
        "is_subscription": bool(opp.is_subscription),
        "customer_prior_payments": int(opp.cust_prior_payments),
        "customer_prior_successes": int(opp.cust_prior_successes),
        "customer_prior_success_with_method": bool(opp.cust_prior_method_match),
        "prior_failed_same_instrument": int(opp.cust_prior_failed_instrument),
        "minutes_since_failure": float(opp.minutes_since_failure),
        "merchant_baseline_success": float(opp.merchant_baseline_success),
        "merchant_monthly_volume": int(opp.merchant_volume),
        "occurred_hour": int(opp.hour),
        "occurred_weekday": int(opp.weekday),
    }


def row_for(ctx: dict, opp, action: str, label: int) -> dict:
    feats = build_features(ctx)
    feats.update({
        "action": action,
        "label": int(label),
        "amount_minor": ctx["amount_minor"],
        "created_ts": opp.created_ts,
        "opp_id": opp.opp_id,
    })
    return feats


def temporal_split(df: pd.DataFrame, fractions=(0.6, 0.2, 0.2)):
    df = df.sort_values("created_ts").reset_index(drop=True)
    n = len(df)
    a = int(n * fractions[0])
    b = int(n * (fractions[0] + fractions[1]))
    return df.iloc[:a], df.iloc[a:b], df.iloc[b:]


def make_model(model_type: str, seed: int):
    if model_type == "logreg":
        base = LogisticRegression(max_iter=2000, C=1.0, random_state=seed)
        return CalibratedClassifierCV(base, method="sigmoid", cv=3)
    base = GradientBoostingClassifier(
        n_estimators=300, max_depth=4, learning_rate=0.08,
        subsample=0.9, random_state=seed,
    )
    return CalibratedClassifierCV(base, method="sigmoid", cv=3)


def train_per_action(train_df: pd.DataFrame, model_type: str, seed: int) -> dict:
    models: dict[str, object] = {}
    # `do_nothing` rows are the counterfactual baseline, not an action to score.
    trainable = [a for a in sorted(train_df.action.unique()) if a != BASELINE_ACTION]
    for offset, action in enumerate(trainable):
        sub = train_df[train_df.action == action]
        positives = int(sub.label.sum())
        if positives < MIN_POSITIVES or sub.label.nunique() < 2:
            print(f"  [{action}] skipped ({len(sub)} rows, {positives} positives)")
            continue
        # Derive the per-action seed from a stable ordinal, never from hash():
        # Python randomizes string hashing per process, so `seed + hash(action)`
        # silently produced a different model on every run despite --seed.
        model = make_model(model_type, seed + offset)
        model.fit(sub[FEATURE_NAMES].values.astype(float), sub.label.values)
        models[action] = model
        print(f"  [{action}] trained on {len(sub)} rows ({positives} positives)")
    return models


def evaluate(models: dict, val_df: pd.DataFrame, test_df: pd.DataFrame) -> dict:
    report: dict = {"per_action": {}}
    for name, split in (("validation", val_df), ("test", test_df)):
        per_action = {}
        for action, model in models.items():
            sub = split[split.action == action]
            if len(sub) == 0 or sub.label.nunique() < 2:
                continue
            probs = model.predict_proba(sub[FEATURE_NAMES].values.astype(float))[:, 1]
            per_action[action] = {
                "n": int(len(sub)),
                "positive_rate": round(float(sub.label.mean()), 4),
                "roc_auc": round(float(roc_auc_score(sub.label, probs)), 4),
                "log_loss": round(float(log_loss(sub.label, probs, labels=[0, 1])), 4),
                "brier": round(float(brier_score_loss(sub.label, probs)), 4),
                "mean_predicted": round(float(probs.mean()), 4),
            }
        report["per_action"][name] = per_action
    return report


def economics_simulation(
    models: dict,
    test_df: pd.DataFrame,
    baseline_table: dict[str, float],
    budget_fraction: float = 0.35,
    random_draws: int = 25,
    seed: int = 11,
) -> dict:
    """Compare selection policies at an identical budget, on an identical universe.

    Offline replay can only grade an action whose outcome was actually observed
    for that opportunity -- the counterfactual is unknowable.  So the universe
    is restricted to opportunities where the "always send a link" baseline is
    defined, every arm gets the same k = budget_fraction * N picks, and every
    arm is scored on realized labels.

    The arms exist to separate two very different claims:

      * `value_ranked_link` picks the k largest opportunities and links them.
        It uses no model at all.  Since expected value is dominated by amount,
        a model that merely ranks by size cannot beat this.
      * `random_link` picks k at random, averaged over several draws.  This is
        the no-targeting floor.

    The previous version of this function compared the top-k *by value* against
    the mean recovery rate applied to an average-value k.  That is not a
    comparison of policies: it measured the value skew of the portfolio and
    reported it as model lift.  The headline number below is therefore stated
    against the strongest no-model policy, not the weakest one.
    """
    rng = np.random.default_rng(seed)

    observed: dict[str, dict[str, tuple[int, int]]] = {}
    amounts: dict[str, float] = {}
    for row in test_df.itertuples(index=False):
        observed.setdefault(row.opp_id, {})[row.action] = (
            int(row.label), int(row.amount_minor)
        )
        amounts[row.opp_id] = float(row.amount_minor)

    universe = [
        opp_id for opp_id, actions in observed.items() if "send_payment_link" in actions
    ]
    if not universe:
        return {
            "opportunities_scored": 0,
            "note": "no opportunity in the test split has an observed link outcome",
        }

    feature_rows = test_df.drop_duplicates(subset="opp_id").set_index("opp_id")[FEATURE_NAMES]

    def realized(opp_id: str, action: str) -> tuple[int, bool]:
        """Recovered minor units under `action`, and whether it was observed."""
        if action == BASELINE_ACTION:
            return 0, True
        entry = observed[opp_id].get(action)
        if entry is None:
            return 0, False
        label, amount = entry
        return label * amount, True

    def natural_for(opp_id: str) -> float:
        row = feature_rows.loc[opp_id]
        return baseline_table.get(
            error_group_from_features(row), baseline_table.get("unknown", 0.0)
        )

    choices: dict[str, dict] = {}
    unobservable = 0
    for opp_id in universe:
        vector = feature_rows.loc[opp_id].values.astype(float).reshape(1, -1)
        natural = natural_for(opp_id)

        best_action, best_ev = BASELINE_ACTION, 0.0
        for action, model in models.items():
            if action not in observed[opp_id]:
                # Unobserved counterfactual: the model may well prefer this in
                # production, but this dataset cannot grade the choice.
                continue
            p = float(model.predict_proba(vector)[:, 1][0])
            # Match production exactly: incremental over the do-nothing
            # baseline, net of the action's cost.
            incremental = round(max(0.0, p - natural), 4)
            ev = incremental * amounts[opp_id] - INTERVENTION_COST_MINOR.get(action, 0)
            if ev > best_ev:
                best_ev, best_action = ev, action

        recovered, was_observed = realized(opp_id, best_action)
        if not was_observed:
            unobservable += 1
        choices[opp_id] = {
            "ev": best_ev,
            "recovered": recovered,
            "cost": INTERVENTION_COST_MINOR.get(best_action, 0),
        }

    k = max(1, int(len(universe) * budget_fraction))
    link_cost = INTERVENTION_COST_MINOR["send_payment_link"]
    link_recovered = {opp_id: realized(opp_id, "send_payment_link")[0] for opp_id in universe}

    def arm(selected: list[str], recovered_by: dict[str, int], cost_by, selection: str) -> dict:
        """Score one selection policy.

        Gross recovered revenue is the wrong yardstick here and it is worth
        being explicit about why: a policy that targets whatever was going to
        recover anyway scores brilliantly on gross and creates nothing.  The
        production ranker optimises *incremental* recovery, so the benchmark
        that grades it has to do the same, or it rewards the opposite
        behaviour.  Expected natural recovery over the selected set is
        subtracted to estimate what the arm actually added.
        """
        recovered = sum(recovered_by[opp_id] for opp_id in selected)
        cost = sum(cost_by(opp_id) for opp_id in selected)
        expected_natural = sum(natural_for(opp_id) * amounts[opp_id] for opp_id in selected)
        incremental = recovered - expected_natural
        return {
            "recovered_minor": int(recovered),
            "expected_natural_recovery_minor": int(expected_natural),
            "incremental_recovery_minor": int(incremental),
            "intervention_cost_minor": int(cost),
            "net_incremental_minor": int(incremental - cost),
            "selection": selection,
        }

    by_ev = sorted(universe, key=lambda opp_id: choices[opp_id]["ev"], reverse=True)[:k]
    by_value = sorted(universe, key=lambda opp_id: amounts[opp_id], reverse=True)[:k]

    model_arm = arm(
        by_ev,
        {opp_id: choices[opp_id]["recovered"] for opp_id in universe},
        lambda opp_id: choices[opp_id]["cost"],
        "rank by incremental expected value, net of cost",
    )
    value_arm = arm(
        by_value, link_recovered, lambda _opp_id: link_cost,
        "rank by amount, always send a link (uses no model)",
    )

    random_arms = [
        arm(
            [universe[i] for i in rng.choice(len(universe), size=k, replace=False)],
            link_recovered, lambda _opp_id: link_cost,
            f"uniform random, always send a link (mean of {random_draws} draws)",
        )
        for _ in range(random_draws)
    ]
    random_arm = {
        key: (
            int(np.mean([a[key] for a in random_arms]))
            if key != "selection" else random_arms[0][key]
        )
        for key in random_arms[0]
    }

    def lift(arm_value: float, reference: float) -> float:
        if reference == 0:
            return 0.0
        return round(100.0 * (arm_value - reference) / abs(reference), 2)

    model_net = model_arm["net_incremental_minor"]
    value_net = value_arm["net_incremental_minor"]
    random_net = random_arm["net_incremental_minor"]

    return {
        "opportunities_scored": len(universe),
        "universe": len(universe),
        "budget_fraction": budget_fraction,
        "budget_k": k,
        "objective": "net incremental recovery (realized minus expected natural, minus cost)",
        "arms": {
            "model_policy": model_arm,
            "value_ranked_link": value_arm,
            "random_link": random_arm,
        },
        # Stated against the strongest no-model policy, never the weakest.
        "lift_pct": lift(model_net, value_net),
        "lift_vs_value_ranked_pct": lift(model_net, value_net),
        "lift_vs_random_pct": lift(model_net, random_net),
        "gross_recovered_minor": {
            "model_policy": model_arm["recovered_minor"],
            "value_ranked_link": value_arm["recovered_minor"],
            "random_link": random_arm["recovered_minor"],
        },
        "unobservable_choices": unobservable,
        "caveats": [
            "Offline replay: only actions with an observed outcome for that "
            "opportunity can be graded, so the model is restricted to those.",
            "Incremental recovery uses an ESTIMATED natural-recovery baseline, "
            "not an observed counterfactual.",
            "The training population was intervened on and is not a random "
            "sample; the live treatment/control holdout is the unbiased "
            "estimate of lift.",
        ],
    }


def next_version(artifacts_dir: Path) -> str:
    existing = list(artifacts_dir.glob("recovery_model_v*.pkl"))
    max_v = 0
    for path in existing:
        digits = "".join(ch for ch in path.stem if ch.isdigit())
        if digits:
            max_v = max(max_v, int(digits))
    return f"v{max_v + 1}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Train per-action recovery models")
    parser.add_argument("--data-dir", type=str, default=str(REPO_ROOT / "data" / "synthetic"))
    parser.add_argument("--artifacts-dir", type=str, default=str(REPO_ROOT / "models" / "artifacts"))
    parser.add_argument("--model", choices=["logreg", "gbm"], default="gbm")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument(
        "--data-provenance", choices=("synthetic", "razorpay_test", "shadow_live", "live"),
        default=None, help="Required evidence label; inferred as synthetic only for data/synthetic",
    )
    args = parser.parse_args()

    t0 = time.time()
    data_dir, artifacts_dir = Path(args.data_dir), Path(args.artifacts_dir)
    print(f"Loading data from {data_dir}")
    verified_rows_path = data_dir / "verified_rows.csv"
    if verified_rows_path.exists():
        rows = pd.read_csv(verified_rows_path)
        rows["created_ts"] = pd.to_datetime(rows["created_ts"])
        print(f"Loading privacy-safe verified rows from {verified_rows_path}")
    else:
        _, opps, interventions = load_contexts(data_dir)
        print(f"{len(opps)} opportunities, {len(interventions)} interventions")
        print("Building supervised rows...")
        rows = build_supervised_rows(opps, interventions)
    print(f"{len(rows)} supervised rows across actions: {rows.action.value_counts().to_dict()}")

    # Split the scorable actions and the do-nothing observations separately.
    # Mixing them would let the (much larger) baseline population dominate the
    # temporal split and starve every per-action quality gate of test rows.
    action_rows = rows[rows.action != BASELINE_ACTION].reset_index(drop=True)
    baseline_rows = rows[rows.action == BASELINE_ACTION].reset_index(drop=True)

    train_df, val_df, test_df = temporal_split(action_rows)
    print(f"split: train={len(train_df)} val={len(val_df)} test={len(test_df)}")

    # Fit the counterfactual on the training period only. A baseline measured
    # over the test window would leak future information into the economics
    # replay that is supposed to grade the policy.
    if len(baseline_rows) and len(train_df):
        cutoff = train_df.created_ts.max()
        in_period = baseline_rows[baseline_rows.created_ts <= cutoff]
    else:
        in_period = baseline_rows
    baseline_table, baseline_source = natural_recovery_baseline(in_period)
    print(f"natural-recovery baseline ({baseline_source}): {baseline_table}")

    print(f"Training {args.model} per-action models...")
    models = train_per_action(train_df, args.model, args.seed)
    if not models:
        print("No trainable actions — aborting.")
        sys.exit(1)

    report = evaluate(models, val_df, test_df)
    econ = economics_simulation(models, test_df, baseline_table)
    report["economics_test"] = econ
    report["natural_recovery_baseline"] = baseline_table
    report["natural_recovery_baseline_source"] = baseline_source
    report["natural_recovery_baseline_rows"] = int(len(in_period))
    provenance = args.data_provenance or ("synthetic" if "synthetic" in data_dir.parts else None)
    if provenance is None:
        print("--data-provenance is required for non-synthetic datasets")
        sys.exit(1)
    action_quality = {}
    for action in sorted(models):
        metrics = report.get("per_action", {}).get("test", {}).get(action, {})
        gap = abs(metrics.get("mean_predicted", 0) - metrics.get("positive_rate", 0))
        reasons = []
        if int(metrics.get("n", 0)) < MIN_TEST_ROWS:
            reasons.append(f"test rows < {MIN_TEST_ROWS}")
        if float(metrics.get("roc_auc", 0)) < MIN_TEST_AUC:
            reasons.append(f"AUC < {MIN_TEST_AUC}")
        if gap > MAX_CALIBRATION_GAP:
            reasons.append(f"calibration gap > {MAX_CALIBRATION_GAP}")
        action_quality[action] = {"enabled": not reasons, "reasons": reasons, "metrics": metrics}

    version = next_version(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    artifact_path = artifacts_dir / f"recovery_model_{version}.pkl"
    card_path = artifacts_dir / f"model_card_{version}.json"

    joblib.dump({
        "version": version,
        "models": models,
        "feature_names": FEATURE_NAMES,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "model_type": args.model,
        "data_provenance": provenance,
        "action_quality": action_quality,
        # Ships with the model so a model trained on real Razorpay data carries
        # its own counterfactual instead of borrowing the synthetic one.
        BASELINE_TABLE_KEY: sanitize_baseline_table(baseline_table) or dict(
            DEFAULT_NATURAL_RECOVERY_BASELINE
        ),
        "natural_recovery_baseline_source": baseline_source,
    }, artifact_path)

    card = {
        "version": version,
        "artifact": artifact_path.name,
        "model_type": args.model,
        "trained_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        # Record a repo-relative path so the model card stays portable across
        # machines.  Absolute paths from the training host leak into the
        # checked-in artifact and are meaningless anywhere else.
        "data_dir": _portable_path(data_dir),
        "data_provenance": provenance,
        "deployment_tier": "demo_only" if provenance == "synthetic" else "shadow_candidate",
        "action_quality": action_quality,
        "rows_train_val_test": [len(train_df), len(val_df), len(test_df)],
        **report,
    }
    card_path.write_text(json.dumps(card, indent=2))

    print(json.dumps(report, indent=2)[:3000])
    print(f"\nArtifact : {artifact_path}")
    print(f"Model card: {card_path}")
    print(f"Done in {time.time() - t0:.1f}s")


if __name__ == "__main__":
    main()
