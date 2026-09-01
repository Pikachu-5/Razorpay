# Webhook Runbook (Razorpay Test Mode)

## Prerequisites

- Razorpay Test Mode keys in `.env` (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`)
- ngrok installed: `winget install ngrok.ngrok`
- `NGROK_AUTHTOKEN` set in environment or `.env`

## 1. Start the stack

```powershell
docker compose up -d postgres
cd backend
..\.venv\Scripts\python -m alembic upgrade head
..\.venv\Scripts\python -m uvicorn app.main:app --port 8000
```

## 2. Open the tunnel

```powershell
powershell -File scripts\start_ngrok.ps1
```

Note the printed **Webhook URL**: `https://<subdomain>.ngrok-free.app/webhooks/razorpay`

> If Razorpay's dashboard rejects your tunnel domain at registration, fall back to
> `zrok` or a Cloudflare quick tunnel — some tunneling domains are blacklisted.

## 3. Choose the webhook secret

Generate once and keep it in `.env` as `RAZORPAY_WEBHOOK_SECRET=<value>`:

```powershell
$bytes = New-Object byte[] 24; [Security.Cryptography.RandomNumberGenerator]::Fill($bytes); [Convert]::ToBase64String($bytes)
```

You will paste the same value into the dashboard in step 4.

## 4. Register the webhook in Razorpay

1. Dashboard → make sure the **Test Mode** toggle is ON.
2. Settings → Webhooks → **Add New Webhook**.
   (Test mode asks for OTP — use `754081`, per Razorpay docs.)
3. Paste the webhook URL from step 2.
4. Paste the secret from step 3.
5. Subscribe to these events:
   - `payment.failed`
   - `payment.captured`
   - `payment.authorized`
   - `payment_link.paid`
   - `subscription.charged`
   - `subscription.charged.failed`
   - `subscription.pending`
   - `subscription.halted`
   - `subscription.completed`

Razorpay only accepts webhook URLs on ports 80/443 — the https tunnel satisfies this.

## 5. Smoke test locally (no Razorpay needed)

With the API running:

```powershell
powershell -File scripts\send_test_webhook.ps1
```

Expected output: first delivery → `accepted`, identical redelivery → `duplicate`.
Then check `GET http://localhost:8000/api/events/recent`.

## 6. Verify real connectivity

```powershell
.venv\Scripts\python scripts\verify_razorpay_connection.py
```

Authenticates against the Razorpay API with your test keys and lists recent payments.

## Security notes

- The ingestion endpoint is **fail-closed**: with no webhook secret configured it returns 503 and stores nothing.
- Signatures verified over the raw request body via HMAC-SHA256 (`X-Razorpay-Signature`), constant-time compare.
- Duplicates rejected via `x-razorpay-event-id` header; content-hash fallback covers deliveries without the header.
