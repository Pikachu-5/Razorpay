param(
    [string]$TargetUrl = "http://localhost:8000/webhooks/razorpay",
    [switch]$NoDuplicate,
    [string]$Method = "upi",
    [int]$AmountMinor = 420000,
    [switch]$LoyalCustomer
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
if (-not (Test-Path $envFile)) { Write-Error ".env not found at $envFile" }

$secret = $null
Get-Content $envFile | ForEach-Object {
    if ($_ -match "^RAZORPAY_WEBHOOK_SECRET=(.+)$") { $secret = $Matches[1].Trim() }
}
if (-not $secret) {
    Write-Error "RAZORPAY_WEBHOOK_SECRET empty in .env - set it first (must match dashboard webhook secret)"
}

$paymentId = "pay_smoke_" + (Get-Random -Maximum 999999)
$email = if ($LoyalCustomer) { "loyal.customer@example.com" } else { "demo.customer@example.com" }
$bodyObj = @{
    entity     = "event"
    account_id = "acc_LOCALSMOKE"
    event      = "payment.failed"
    contains   = @("payment")
    payload    = @{
        payment = @{
            entity = @{
                id                = $paymentId
                entity            = "payment"
                amount            = $AmountMinor
                currency          = "INR"
                status            = "failed"
                method            = $Method
                bank              = $(if ($Method -eq "upi") { "HDFC" } else { "ICICI" })
                error_code        = "BAD_REQUEST_ERROR"
                error_description = "Payment timed out"
                error_source      = "network"
                error_step        = "payment_authorization"
                error_reason      = "timeout"
                email             = $email
                contact           = "+919999888777"
                created_at        = [int][double]::Parse((Get-Date -UFormat %s))
            }
        }
    }
    created_at = [int][double]::Parse((Get-Date -UFormat %s))
}
$bodyString = $bodyObj | ConvertTo-Json -Depth 10 -Compress
$rawBytes = [System.Text.Encoding]::UTF8.GetBytes($bodyString)

$hmac = [System.Security.Cryptography.HMACSHA256]::new([System.Text.Encoding]::UTF8.GetBytes($secret))
$signature = (($hmac.ComputeHash($rawBytes)) | ForEach-Object { $_.ToString("x2") }) -join ""

$headers = @{
    "X-Razorpay-Signature"  = $signature
    "x-razorpay-event-id"   = "evt_smoke_" + (New-Guid).ToString("N").Substring(0, 12)
    "Content-Type"          = "application/json"
}

Write-Host "Sending signed payment.failed webhook for $paymentId ..."
$r1 = Invoke-RestMethod -Uri $TargetUrl -Method Post -Body $bodyString -Headers $headers
Write-Host "  first delivery : $($r1 | ConvertTo-Json -Compress)"

if (-not $NoDuplicate) {
    Write-Host "Re-sending identical delivery (same event id) to verify dedupe ..."
    $r2 = Invoke-RestMethod -Uri $TargetUrl -Method Post -Body $bodyString -Headers $headers
    Write-Host "  redelivery     : $($r2 | ConvertTo-Json -Compress)"
}
