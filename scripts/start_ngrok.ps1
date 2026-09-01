param(
    [int]$Port = 8000
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ngrok -ErrorAction SilentlyContinue)) {
    Write-Error "ngrok not found. Install with: winget install ngrok.ngrok"
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $repoRoot ".env"
$token = $env:NGROK_AUTHTOKEN
if (-not $token -and (Test-Path $envFile)) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match "^NGROK_AUTHTOKEN=(.+)$") { $token = $Matches[1].Trim() }
    }
}
if (-not $token) { Write-Error "Set NGROK_AUTHTOKEN env var or add NGROK_AUTHTOKEN=... to .env" }

& ngrok config add-authtoken $token | Out-Null

$existing = Get-NetTCPConnection -LocalPort 4040 -State Listen -ErrorAction SilentlyContinue
if (-not $existing) {
    Start-Process ngrok -ArgumentList "http", "$Port", "--log", "stdout", "--log-format", "json" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

$tunnel = $null
$deadline = (Get-Date).AddSeconds(30)
while ((Get-Date) -lt $deadline) {
    try {
        $api = Invoke-RestMethod "http://127.0.0.1:4040/api/tunnels"
        $tunnel = $api.tunnels | Where-Object { $_.proto -eq "https" } | Select-Object -First 1
        if ($tunnel) { break }
    } catch {}
    Start-Sleep -Seconds 2
}
if (-not $tunnel) { Write-Error "Could not read public URL from ngrok api (http://127.0.0.1:4040)" }

$publicUrl = $tunnel.public_url
Write-Host ""
Write-Host "Public URL:   $publicUrl"
Write-Host "Webhook URL:  $publicUrl/webhooks/razorpay"
Write-Host ""
Write-Host "Register this URL in Razorpay Dashboard -> Settings -> Webhooks (Test Mode)."
Write-Host "Then set PUBLIC_BASE_URL=$publicUrl in .env"
