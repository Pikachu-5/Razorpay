# Revenue Recovery Control Plane

**Live: [pikachu-5.github.io/razorpay](https://pikachu-5.github.io/razorpay/)**

An observe-first Razorpay revenue-operations prototype that detects payment failures, identifies recoverable revenue, selects policy-gated interventions, and records every decision and outcome with explicit data provenance. Backend runs on Azure App Service + Postgres; frontend deploys to GitHub Pages, both via GitHub Actions on every push to `main`.

![The Razorpay Recover landing page](docs/img/01-landing.png)

## What it looks like

The landing page states the thesis, then the body pages through how it works,
the failure mix, what it is worth, and the evidence. **See it recover a payment**
opens the operator console — which opens with a short guided tour on your first
visit, pinning a small card next to whichever KPI, panel, or control it's
explaining rather than covering the screen. Reopen it anytime from the **?**
icon next to the logo.

| | |
| --- | --- |
| ![Failure mix](docs/img/01c-failure-mix.png) **Failure mix** — every class, its share of failed value, and how often it comes back untouched. Read live from this install. | ![Evidence](docs/img/01e-evidence.png) **Evidence** — the withheld holdout against the treated group, with the p-value and sample size attached. |

![The recovery queue](docs/img/03-recovery-queue.png)

The console opens on the live feed and the recovery queue: what needs a person,
what is closing, and what is merely waiting.

![The decision audit trace for one opportunity](docs/img/07-audit-trace.png)

Every decision keeps its full chain: the diagnosis and its evidence, each
candidate action with its probability, cost and expected value, the policy rules
that passed or failed, and what actually executed.

![Offline economics benchmark](docs/img/04-economics-benchmark.png)

The economics benchmark is the one worth reading closely. All three arms
intervene on the same number of opportunities and are scored on realised
outcomes, minus the recovery expected without any intervention, minus what the
actions cost. Beating random selection proves nothing — expected value is
dominated by ticket size, so sorting by amount already clears that bar. The
number that matters is the one against the value-ranked policy.

Regenerate these after a UI change with `npm run screenshots` in `frontend/`
— against a local dev server (needs it running and a seeded baseline), or
directly against the live install with `npm run screenshots -- <url>`.

## Where the numbers come from

Two kinds of figure appear, and they are never mixed:

- **Claims about this system** — recovery rates, lift, the failure mix, the EV
  gate, contact caps — are read live from the running install. Nothing on the
  page is a hardcoded screenshot of a good day.
- **Claims about the market** — the failure-rate slider's default, the
  published merchant failure mix — come from cited public sources, linked in
  the page footer. Indian merchants run 92–96% blended payment success, so 4–8%
  of attempts fail; the calculator defaults to the 6% midpoint.

## What is implemented

- Signed Razorpay webhook ingestion with fail-closed HMAC verification and deduplication.
- Failure-to-opportunity pipeline, customer/payment history, intervention verification, and full audit trail.
- Diagnosis, per-action recovery predictions, expected-value ranking, and a deterministic safety policy the model cannot overrule.
- Expected value is measured against the do-nothing counterfactual: actions are ranked on the recovery they *add* over the rate at which these failures resolve on their own, net of what the action costs.
- Order-centric recovery deduplication plus Razorpay Order, Payment Link, downtime, subscription, invoice, refund, and dispute lifecycles.
- Webhook-first state with bounded Razorpay API reconciliation for duplicate, delayed, missed, or out-of-order delivery.
- Observe-only by default (`SHADOW_MODE=true`): customer-facing actions are recorded without sending links or notifications until explicitly armed live — from the landing page's CTA, or from the **Execution** row in the console sidebar itself, both behind a preflight confirm.
- Segment-level (method × bank) incident detection, simulation, response budgets, and dashboard visibility.
- Deterministic 80/20 treatment/control holdout for counterfactual recovery measurement.
- Synthetic data isolation from operational KPIs and experiments, plus a verified decision-time feature export for later real-data training.
- Per-action ML quarantine with heuristic fallback, model cards, gated promotion, and a responsive evidence-first React operations console.
- An operator preflight on every consequential action: batch incident response, model promotion, and manual re-decide each show scope, execution mode, and what cannot be undone before they run.

## Deployment

The live install runs the backend (FastAPI + Postgres) on Azure App Service
and Azure Database for PostgreSQL, and the frontend on GitHub Pages. Both
deploy from [`.github/workflows/ci.yml`](.github/workflows/ci.yml) on every
push to `main`, gated behind the backend and frontend test jobs passing
first. Azure authentication uses OIDC federated credentials, not a stored
secret. A second workflow, [`postgres-schedule.yml`](.github/workflows/postgres-schedule.yml),
stops and starts the database on a cost-saving nightly schedule outside
any period the install needs to stay reachable around the clock — mirrored
client-side in [`maintenanceWindow.ts`](frontend/src/utils/maintenanceWindow.ts)
so the frontend shows an honest "resting" message instead of failed requests
while it's down.

## Start locally

```powershell
docker compose up -d postgres
.\.venv\Scripts\python -m alembic upgrade head
powershell -File scripts\dev_server.ps1

cd frontend
npm run dev
```

Open http://localhost:5173. The backend API documentation is at http://localhost:8000/docs.

Set Razorpay **Test Mode** credentials and a webhook secret in `.env` before using live test-mode payment links. See [the webhook runbook](docs/runbook-webhooks.md).

The checked-in `.env.example` keeps `SHADOW_MODE=true`. Only set it to `false`
after credentials, webhook delivery, reconciliation, and policy limits have been
verified in the intended test account. `RAZORPAY_MODE=live` identifies live data;
it does not bypass shadow mode or policy controls.

The Postgres container is exposed on host port **55432** so it does not collide
with a workstation's existing PostgreSQL service.

## Control-plane authentication

The dashboard is intentionally frictionless only while `APP_ENV=dev` (or
`test`/`local`) and no control-plane key is configured. For any other
environment, set `CONTROL_PLANE_API_KEY` and send it on every state-changing
operator request in `X-Control-Plane-Key`. Simulation start/stop, anomaly scan,
incident response, manual re-decide, and model promotion are protected. Set a
separate `CONTROL_PLANE_ADMIN_API_KEY` to restrict `force: true` model promotion
to an administrator. Razorpay webhooks remain authenticated by their signature,
not this header.

The autonomous monitor uses Postgres-backed webhook claims, durable monitor
events, and transaction-scoped advisory locks so multiple API replicas do not
double-process webhooks or scheduled detector/sweeper work. For a higher-volume
production deployment, run the checked-in Alembic migrations and size the
database/event retention policy for the merchant's traffic.

## Verify

```powershell
.\.venv\Scripts\python -m ruff check backend training scripts

cd backend
..\.venv\Scripts\python -m pytest -q

cd ..\frontend
npm run build
npm run lint
npm test
```

The backend suite needs PostgreSQL because it provisions `recovery_test` on
localhost:55432. It writes only to that database and to a temporary artifacts
directory, never to the checked-in `models/artifacts`.

## Demonstration

A fresh install has no history. Seed a deterministic baseline first, otherwise
the first thing a reviewer sees is an empty dashboard:

```powershell
# 24 hours of healthy traffic — makes the live feed and incidents meaningful
.\.venv\Scripts\python scripts\seed_demo_baseline.py

# ~2 minutes; enough volume for the holdout experiment to clear its sample gate
.\.venv\Scripts\python scripts\seed_demo_baseline.py --days 30
```

Seeded history is flagged `is_synthetic`. The live feed opens *including* it
by default, exactly like the Evidence tab and the landing page's failure mix
already do, so a seeded install never opens on a wall of zeros — flip **Show
real payments only** any time to see just genuine Razorpay activity instead.
Anything built from synthetic data is labelled a demonstration of the
measurement machinery, never evidence of real lift.

Then use [RUNBOOK.md](RUNBOOK.md) for the complete judge/demo flow. It includes a
safe preflight, four failure scenarios, expected observable results, and Test
Mode cautions.

## Important boundaries

- This is a prototype, not a production deployment. Production-tier model promotion requires verified live-data provenance.
- The promoted model is trained on **synthetic** data and is labelled `demo_only`. It clears the offline gate (+17.8% net incremental against the strongest no-model policy), but offline replay is not evidence of live lift — the treatment/control holdout is. See [docs/TRAINING.md](docs/TRAINING.md).
- The gate is real and it bites: `prompt_card_change` and `wait_for_native_retry` failed it and are quarantined. Quarantined actions fall back to the heuristic rather than being switched off, so the action space stays intact.
- A trained artifact only reaches live traffic through `models/artifacts/PROMOTED.json`. Placing a `.pkl` in the registry does nothing on its own.
- With shadow mode disabled, automated payment-link actions can create objects in the configured Razorpay account. Keep shadow mode enabled when demonstrating detection only.
- Synthetic demo traffic is labelled and excluded from operational KPIs, causal experiments, and verified training exports.
- Experiment metrics are signed: a negative incremental-revenue value is an important result, not an error.
