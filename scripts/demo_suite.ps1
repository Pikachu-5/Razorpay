param(
    [ValidateSet("all", "upi_hdfc_timeout", "card_icici_timeout", "netbanking_sbi_timeout", "wallet_axis_timeout")]
    [string]$Scenario = "all",
    [string]$ApiBase = "http://localhost:8000"
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot

function Wait-ForSimulationCompletion {
    param([int]$TimeoutSeconds = 180)

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        Start-Sleep -Seconds 2
        $status = Invoke-RestMethod "$ApiBase/api/simulation/status"
        if (-not $status.active) { return $status }
    } while ((Get-Date) -lt $deadline)

    throw "Simulation did not finish within $TimeoutSeconds seconds. Stop it at $ApiBase/api/simulation/stop before retrying."
}

try { Invoke-RestMethod "$ApiBase/readyz" | Out-Null }
catch { throw "Backend is not ready. Start Docker Desktop, run docker compose up -d postgres, then scripts\dev_server.ps1." }

Write-Host "Seeding deterministic 24-hour healthy baseline..."
& (Join-Path $repoRoot ".venv\Scripts\python.exe") (Join-Path $PSScriptRoot "seed_demo_baseline.py")

$scenarios = @(
    @{ name="upi_hdfc_timeout"; method="upi"; bank="HDFC"; label="HDFC UPI timeout degradation" },
    @{ name="card_icici_timeout"; method="card"; bank="ICICI"; label="ICICI card timeout degradation" },
    @{ name="netbanking_sbi_timeout"; method="netbanking"; bank="SBI"; label="SBI netbanking timeout degradation" },
    @{ name="wallet_axis_timeout"; method="wallet"; bank="AXIS"; label="AXIS wallet timeout degradation" }
)

foreach ($item in $scenarios) {
    if ($Scenario -ne "all" -and $Scenario -ne $item.name) { continue }
    Write-Host "Starting SYNTHETIC scenario: $($item.name)"
    $body = @{ method=$item.method; bank=$item.bank; failure_rate=0.70; payments_per_minute=60; amount_min_minor=100000; amount_max_minor=1500000; duration_seconds=30; label=$item.label } | ConvertTo-Json
    $started = Invoke-RestMethod -Method Post "$ApiBase/api/simulation/start" -ContentType "application/json" -Body $body
    if (-not $started.started) {
        throw "Could not start $($item.name): $($started.reason). Stop the active simulation before retrying."
    }
    $finalStatus = Wait-ForSimulationCompletion
    Write-Host "  Synthetic traffic: $($finalStatus.generated_payments) payments, $($finalStatus.generated_failures) failures"
    $incidents = Invoke-RestMethod "$ApiBase/api/incidents?limit=10"
    $match = $incidents | Where-Object { $_.method -eq $item.method -and $_.bank -eq $item.bank } | Select-Object -First 1
    if ($match) { Write-Host "  Incident $($match.id): $($match.status), risk Rs $([math]::Round($match.revenue_at_risk_minor / 100, 0))" }
    else { Write-Warning "  No incident detected. Confirm baseline seed completed and policy/detector settings are unchanged." }
}

Write-Host "Demo suite complete. Open the Incidents and Governance dashboard tabs for the audit trail."
