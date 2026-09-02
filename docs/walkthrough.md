# Walkthrough — Phase 7: Live A/B Experimentation Engine & Automated Promotion Gate

> **This is a historical build log for one phase, not current-state documentation.**
> The figures below are the run output from the day the phase landed. For what
> the installed model actually scores now, read `models/artifacts/PROMOTED.json`
> and the model card beside it, or the Evidence tab in the console. The README's
> numbers are the current ones.

We have completed **Phase 7: Live A/B Experimentation Engine & Automated Promotion Gate**, delivering counterfactual holdout evaluation, causal incremental revenue attribution ($+\Delta ₹$), and an automated model promotion comparator.

---

## What Was Built in Phase 7

### 1. Counterfactual A/B Experimentation & Holdout Engine
- **Deterministic 80/20 Assignment**:
  - `treatment` (80%): Runs the full multi-agent decision chain and executes optimal policy-gated interventions (`send_payment_link`, `send_reminder`, etc.).
  - `control` (20%): Runs diagnosis and ML predictions for counterfactual tracking, but policy enforces a strict holdout (`do_nothing`, status `closed_not_viable` with reason `control group holdout`). No active contact or payment links are dispatched.
- **Pure Counterfactual Measurement**: Measures baseline natural recovery rate ($CR_{\text{control}}$) without confounding intervention effects.

### 2. Causal Incremental Lift & Statistical Significance API (`/api/metrics/experiment`)
- Calculates:
  - Conversion rates for Treatment ($CR_{\text{treatment}}$) vs Control ($CR_{\text{control}}$)
  - Causal Lift Percentage: $\text{Lift} = \frac{CR_{\text{treatment}} - CR_{\text{control}}}{CR_{\text{control}}} \times 100$
  - Incremental Revenue Recovered: $\Delta ₹ = (CR_{\text{treatment}} - CR_{\text{control}}) \times N_{\text{treatment}} \times \bar{A}_{\text{treatment}}$
  - Two-proportion hypothesis test: $z$-score and two-tailed $p$-value ($p < 0.05$ significance check).

### 3. Automated Model Promotion Gate
- **CLI Comparator (`training/promote_model.py`)**:
  - Compares candidate model card (Challenger) against Champion on held-out test splits.
  - Validates primary action ROC-AUC ($\ge 0.50$), probability calibration gap ($\le 12\%$), and economics net revenue lift ($\ge 0\%$).
  - Atomically writes `models/artifacts/PROMOTED.json`.
- **Backend API Endpoints (`/api/ml/comparison` & `/api/ml/promote`)**:
  - `GET /api/ml/comparison`: Returns champion and challenger model card performance diffs.
  - `POST /api/ml/promote`: Evaluates guardrails and atomically promotes candidate models to production runtime.

### 4. Frontend Governance & Experimentation Dashboard (`GovernanceTab.tsx`)
- **Live A/B Experimentation Card**: Displays real-time incremental revenue recovered ($₹$), treatment vs control conversion rates, and statistical confidence ($z$-score and $p$-value).
- **Automated Model Promotion Console**: Dropdown selector to inspect candidate model cards, side-by-side metric comparison, and one-click "Promote to Production" button.

---

## Verification Results

### Promotion Comparator CLI Verification
```
=======================================================
  MODEL PROMOTION COMPARATOR
  Challenger: v2 vs Champion: v2
=======================================================

Action                    | Champion AUC   | Challenger AUC | Calib Gap  | Status
---------------------------------------------------------------------------
prompt_card_change        | N/A            | 0.434          | 3.1      % | OK
send_payment_link         | N/A            | 0.611          | 1.6      % | OK
send_reminder             | N/A            | 0.520          | 5.3      % | OK
wait_for_native_retry     | N/A            | 0.550          | 1.0      % | OK

-------------------------------------------------------
Economics Lift vs Baseline: Challenger: +193.5% | Champion: +0.0%
-------------------------------------------------------

[PROMOTED] recovery_model_v2.pkl is now the active model artifact!
Updated C:\Codes\Razorpay\models\artifacts\PROMOTED.json
```

**On that `+193.5%`:** it is the figure this phase produced against a weak
baseline, and it did not survive scrutiny. The benchmark was later rebuilt to
score every arm on realised outcomes *minus the recovery expected with no
intervention at all*, and to compare against the strongest no-model policy
(rank by amount) rather than a weak one — because expected value is dominated
by ticket size, so beating random proves nothing. Under that benchmark the same
artifact scores **+17.8%**, which is the number the README and `PROMOTED.json`
carry. The per-action gate also tightened: `prompt_card_change` and
`wait_for_native_retry` are now quarantined and fall back to the heuristic,
where the table above shows all four passing.

### Frontend Production Build
```
> frontend@0.0.0 build
> tsc -b && vite build

vite v8.2.2 building client environment for production...
transforming...
✓ 27 modules transformed.
rendering chunks...
dist/index.html                   0.45 kB │ gzip:  0.29 kB
dist/assets/index-BE4QSay3.css   23.95 kB │ gzip:  4.88 kB
dist/assets/index-DsQyOsXV.js   254.93 kB │ gzip: 74.77 kB
✓ built in 189ms
```

---

## Next Steps: Phase 8

In **Phase 8**, we build:
1. Canonical multi-scenario automated demo suite (`scripts/demo_suite.ps1`) covering the 4 core payment failure scenarios.
2. Complete judge demonstration runbook (`RUNBOOK.md` / `README.md`) with end-to-end instructions and architecture diagrams.
