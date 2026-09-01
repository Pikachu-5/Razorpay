param(
    [int]$Port = 8000,
    [int]$MinRoutes = 16
)

$ErrorActionPreference = "Stop"

# Reclaim the port, but only from a previous run of THIS server. The original
# version force-killed whatever happened to own the port, which on a reviewer's
# machine could be any unrelated service they cared about.
$existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
foreach ($connection in $existing) {
    $owner = Get-Process -Id $connection.OwningProcess -ErrorAction SilentlyContinue
    if (-not $owner) { continue }

    $commandLine = ""
    try {
        $commandLine = (Get-CimInstance Win32_Process -Filter "ProcessId = $($owner.Id)" -ErrorAction Stop).CommandLine
    } catch { }

    if ($owner.ProcessName -match "^(python|pythonw|uvicorn)$" -and $commandLine -match "app\.main:app") {
        Stop-Process -Id $owner.Id -Force -ErrorAction SilentlyContinue
        Start-Sleep -Seconds 2
        Write-Host "Stopped previous control-plane API on port $Port (pid $($owner.Id))"
    }
    else {
        throw ("Port {0} is held by '{1}' (pid {2}), which is not this project's API. " -f $Port, $owner.ProcessName, $owner.Id) +
              "Stop it yourself or start on another port: scripts\dev_server.ps1 -Port 8001"
    }
}

$repoRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$envFile = Join-Path $repoRoot ".env"
Get-Content $envFile | ForEach-Object {
    if ($_ -match "^RAZORPAY_WEBHOOK_SECRET=(.+)$") {
        $env:RAZORPAY_WEBHOOK_SECRET = $Matches[1].Trim()
    }
}

$python = Join-Path $repoRoot ".venv\Scripts\python.exe"
$backendDir = Join-Path $repoRoot "backend"
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python environment not found at $python. Create .venv and install backend\requirements-dev.txt first."
}

Write-Host "Applying database migrations..."
Push-Location $repoRoot
try {
    & $python -m alembic -c (Join-Path $repoRoot "alembic.ini") upgrade head
    if ($LASTEXITCODE -ne 0) { throw "Database migration failed" }
}
finally {
    Pop-Location
}

Start-Process -FilePath $python -ArgumentList "-m", "uvicorn", "app.main:app", "--port", "$Port" -WorkingDirectory $backendDir -WindowStyle Hidden

Start-Sleep -Seconds 6
$openapi = Invoke-RestMethod "http://localhost:$Port/openapi.json"
$routes = @($openapi.paths.PSObject.Properties.Name)
Write-Host "Live routes: $($routes.Count)"
if ($routes.Count -lt $MinRoutes) {
    throw "Route count $($routes.Count) is below expected $MinRoutes - stale or failed boot"
}
foreach ($r in $routes) { Write-Host "  $r" }
