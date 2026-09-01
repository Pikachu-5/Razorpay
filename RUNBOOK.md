# Demonstration Runbook

This runbook demonstrates the Revenue Recovery Control Plane end to end using local Postgres and Razorpay Test Mode.

## Safety first

The simulator can cause policy-approved payment-link calls to the configured Razorpay **Test Mode** account. Do not use production keys. To demonstrate detection only, set `POLICY_KILL_SWITCH=true` in `.env`, then restart the backend. This records the incident and policy blocks without creating links.

## Preflight

1. Start Docker Desktop, then run `docker compose up -d postgres`.
2. Confirm `.env` has the Test Mode keys and `RAZORPAY_WEBHOOK_SECRET` if webhook delivery is being demonstrated.
3. Start the API with `powershell -File scripts\dev_server.ps1` and the dashboard with `cd frontend; npm run dev`.
4. Confirm `http://localhost:8000/readyz` reports database `up`.
5. Run `cd backend; ..\.venv\Scripts\python -m pytest -q`.

## Canonical demo

The suite seeds a 24-hour healthy baseline, then runs four short, clearly labelled synthetic incidents. It does not modify schema or delete existing data.

```powershell
powershell -File scripts\demo_suite.ps1
```

By default, the suite pauses between scenarios and prints the incident summary. Run a single scenario with `-Scenario upi_hdfc_timeout`. The scenarios are:

| Scenario | Segment | Intended demonstration |
| --- | --- | --- |
| `upi_hdfc_timeout` | HDFC × UPI | High-rate bank/network timeout detection |
| `card_icici_timeout` | ICICI × card | Method and bank segmentation |
| `netbanking_sbi_timeout` | SBI × netbanking | Independent incident creation |
| `wallet_axis_timeout` | AXIS × wallet | Fourth supported payment method |

Expected behavior: each incident has a method-and-bank-specific title, reports its revenue at risk, and only considers open opportunities from that exact segment. The dashboard’s Incidents and Overview tabs should update as events arrive.

## Webhook and recovery proof

1. Start a public tunnel using `powershell -File scripts\start_ngrok.ps1`.
2. Register the tunnel URL and webhook secret in Razorpay Test Mode as described in [docs/runbook-webhooks.md](docs/runbook-webhooks.md).
3. Send a signed local event with `powershell -File scripts\send_test_webhook.ps1`.
4. For a created Test Mode payment link, complete payment through Razorpay’s Test Mode flow. The `payment_link.paid` webhook should transition the opportunity to `recovered_intervention` and attribute recovered amount in the audit view.

## Governance proof

The Evidence tab needs non-synthetic volume before it will claim anything. Seed
it first, or it will correctly report `insufficient_sample_do_not_claim_lift`:

```powershell
.\.venv\Scripts\python scripts\seed_demo_baseline.py --days 30
```

It then shows treatment/control conversion rates, signed incremental revenue,
z-score and p-value. Describe it as causal only for the randomized holdout
population; do not claim significance until the UI reports it.

### The promotion gate

No model reaches live traffic without passing it, and it is not decorative.

```powershell
Invoke-RestMethod http://localhost:8000/api/ml/comparison
.\.venv\Scripts\python training\promote_model.py --challenger v2 --target-tier shadow
```

Two things to point at in the output:

1. **Two of the four actions are quarantined.** `prompt_card_change` failed on
   sample size, `wait_for_native_retry` on AUC. They are not switched off — the
   heuristic serves them, so expected-value ranking keeps its full action space.
   Without that, a model that only cleared the gate for payment links would make
   a small opportunity un-answerable by a cheap reminder.

2. **The economics number is stated against the strongest no-model policy:**
   +17.8% net incremental against ranking by amount, not against random. Beating
   random proves nothing — expected value is dominated by ticket size, so sorting
   already clears that bar. This is the comparison most demos never compute.

Worth saying out loud: on an earlier version of the training data this same gate
scored **−16%** and refused to promote anything. That is the behaviour to have
confidence in — it is willing to reject its own model.

To show a rejection live, point at the `rules_evaluated` block inside
`models\artifacts\PROMOTED.json`, which records every rule with its verdict.
The pointer is written atomically, so a reader never observes partial JSON.
