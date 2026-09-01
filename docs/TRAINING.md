# Training the Recovery Models — Handoff

You train the models; the runtime consumes your artifacts. Nothing in the live
system depends on a model existing — until you drop artifacts in, the API
reports `model_source: fallback` and uses documented rule-based probabilities.

**Version pins matter**: pickled scikit-learn models must be trained with the
same sklearn/numpy/joblib versions as the runtime. The training requirements
file (`training/requirements-training.txt`) installs exactly the runtime's pins.

---

## Option A — Train locally (recommended, simplest)

```powershell
# from repo root — reuse the backend venv (deps already pinned there)
.\.venv\Scripts\python training\generate_dataset.py
.\.venv\Scripts\python training\train_recovery_model.py --model gbm
```

That's it. Two commands:

1. **Generator** writes `data/synthetic/{attempts,opportunities,interventions}.csv`
   + `dataset_stats.json` (~120k attempts, 180 days, defaults are good).
2. **Trainer** builds per-action supervised datasets, trains + calibrates one
   classifier per action, evaluates on a held-out *temporal* test split,
   runs an economics simulation, and writes:
   - `models/artifacts/recovery_model_vN.pkl`
   - `models/artifacts/model_card_vN.json` (metrics live here)

Training an artifact does **not** put it on live traffic. The registry serves
only the artifact named in `models/artifacts/PROMOTED.json`; with no pointer the
runtime reports `model_source: fallback` and every decision is made by the
deterministic heuristic. Promote deliberately (below), then restart the backend
or call `POST /api/ml/promote`.

Actions the gate quarantines are *not* switched off. They fall back to the
heuristic for that action only, so the expected-value ranking keeps its full
action space — otherwise a model that only cleared the gate for
`send_payment_link` would make a ₹300 opportunity un-answerable by a ₹3
reminder.

To build a training input from verified real outcomes, export decision-time
features (PII is omitted):

```powershell
.\.venv\Scripts\python training\export_verified_dataset.py
```

The trainer prefers `data/verified/verified_rows.csv` when present and records
its provenance in the artifact and model card. Synthetic artifacts are suitable
for demos and shadow evaluation only.

### Promoting a model

The registry serves the artifact named in `models/artifacts/PROMOTED.json` and
nothing else. There is deliberately no "highest version wins" fallback: dropping
a `.pkl` into the directory used to be enough to put an ungated model on live
traffic, which walked straight past the gate this system is built around.

To promote after reviewing the model card:

```powershell
.\.venv\Scripts\python training\promote_model.py --challenger v2 --target-tier shadow
```

The gate checks primary-action AUC, calibration gap, and net incremental
economics. A rejected candidate exits non-zero and writes no pointer. `--force`
overrides it and records `"forced": true` in the pointer alongside every rule
result, passed or failed. Use `--target-tier production` only for an artifact
trained on verified live data.

`PROMOTED.json` is not checked in: a model earns traffic by passing the gate on
the machine it will run on, not by inheriting a pointer from the repository.

## Option B — Train on Colab

1. Upload these three files to Colab:
   - `training/generate_dataset.py`
   - `training/train_recovery_model.py`
   - `backend/app/ml/` as `app/ml/` (features.py + actions.py — the trainer imports them)
2. `!pip install -q pandas==2.3.1 numpy==2.5.2 scikit-learn==1.6.1 joblib==1.5.3`
3. Run both scripts (they take argparse flags: `--outdir`, `--artifacts-dir`,
   `--data-dir`). On Colab use local paths, e.g.
   `--artifacts-dir /content/artifacts`.
4. Download `recovery_model_vN.pkl` + `model_card_vN.json` into
   `models/artifacts/` locally.

## What good results look like (sanity ranges)

From the designed signal structure, on the default dataset expect roughly:

| Metric | Healthy range | Suspicious |
|---|---|---|
| AUC per action (test) | 0.62 – 0.85 | > 0.95 → leakage bug |
| Calibration (mean_predicted vs positive_rate) | within ~0.05 | big gap → recalibrate |
| Net incremental lift vs value-ranked baseline | positive | negative → the model is worse than just ranking by amount |

If AUCs look too perfect, treat it as a generator/trainer bug and stop.

### What the benchmark is actually testing

An earlier version of the generator drove intervention success mostly from a
latent `responsiveness` trait that no decision-time feature could observe. The
models learned almost nothing (AUC ~0.61 on one action, ~0.50 on the rest) and
scored **negative** net incremental lift: worse than simply ranking by amount.

That was the correct verdict on that dataset, and the fix was not to weaken the
baseline. It was to give the generator structure a model can legitimately see:

- **Recency.** Intent decays; acting within minutes of the failure is worth far
  more than acting an hour later. This is the lever the control plane controls.
- **Amount.** Large payments recover less often whatever you send. This is what
  stops expected value from being monotone in amount — and therefore what makes
  it *possible* to beat chasing the biggest tickets.
- **Contact hour.** A link that lands at 3am is read at 9am, if at all.
- **Observable history.** Engaged customers transact more and fail less, so
  prior-payment count and success rate became real proxies for the latent trait
  instead of decoys.

`responsiveness` is still latent and irreducible, which is why AUCs land in the
0.62–0.74 band rather than at 0.95. A perfect score here would mean leakage.

### Why the benchmark is shaped the way it is

Every arm intervenes on the *same* number of opportunities, drawn from the same
universe, scored on realised outcomes. The score is **net incremental**:
realised recovery, minus the recovery expected with no intervention at all,
minus what the actions cost. Gross recovery is not used, because a policy that
targets whatever was going to recover anyway maximises it while creating
nothing.

## How the data is structured (why models can learn real things)

- Customers have latent responsiveness, tiers, preferred methods, stale contacts.
- Bank/method outages create incident windows (failure reasons cluster on timeouts).
- Natural recovery depends on failure class, customer history, contact reachability.
- Historical "legacy ops" interventions are biased (they chased high-value and
  timeout cases) but ~20% of actions were random overrides — so the model can
  disentangle action effects from case difficulty. This is documented
  observational bias; the live control/treatment experiment (Phase 7) is what
  gives unbiased incremental-₹ attribution.
- Labels carry ~5% noise; no single feature determines the outcome.
