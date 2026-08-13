$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$frontendRoot = Join-Path $projectRoot "frontend"
$logPath = Join-Path $projectRoot "FrameFlow-startup.log"
Start-Transcript -Path $logPath -Append | Out-Null

function Require-Command([string]$name, [string]$installHint) {
    if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
        Write-Host "Missing $name. $installHint" -ForegroundColor Red
        throw "Missing dependency: $name"
    }
}

Write-Host ""
Write-Host "  FrameFlow Customer Launcher" -ForegroundColor Green
Write-Host "  Preparing the studio. Please wait..." -ForegroundColor DarkGray
Write-Host ""

Require-Command "python" "Install Python 3.12+ and enable Add Python to PATH."
Require-Command "ffmpeg" "Install FFmpeg and add it to PATH."

if (-not (Test-Path (Join-Path $projectRoot "backend\.env"))) {
    Copy-Item (Join-Path $projectRoot "backend\.env.example") (Join-Path $projectRoot "backend\.env")
}

if (-not (Test-Path (Join-Path $projectRoot ".customer-ready"))) {
    Write-Host "First launch: installing backend dependencies..." -ForegroundColor Cyan
    python -m pip install -r (Join-Path $projectRoot "backend\requirements.txt")
    if (-not (Test-Path (Join-Path $frontendRoot "dist\index.html"))) {
        Require-Command "npm" "The packaged frontend is missing. Install Node.js 20+."
        Write-Host "Building frontend..." -ForegroundColor Cyan
        Push-Location $frontendRoot
        try { npm install; npm run build } finally { Pop-Location }
    }
    New-Item -ItemType File -Path (Join-Path $projectRoot ".customer-ready") -Force | Out-Null
}
elseif (-not (Test-Path (Join-Path $frontendRoot "dist\index.html"))) {
    Push-Location $frontendRoot
    try { npm run build } finally { Pop-Location }
}

$existing = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $existing) {
    Start-Process -FilePath "python" -ArgumentList @(
        "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", "8000"
    ) -WorkingDirectory $projectRoot -WindowStyle Hidden | Out-Null
}

$ready = $false
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2
        $projects = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/projects" -TimeoutSec 3
        if ($response.status -eq "ok" -and $null -ne $projects) { $ready = $true; break }
    }
    catch { Start-Sleep -Milliseconds 500 }
}
if (-not $ready) { throw "Backend failed to start. Check Python and port 8000." }

Start-Process "http://127.0.0.1:8000"
Write-Host "FrameFlow is open. You may close this window." -ForegroundColor Green
Write-Host "If the browser did not open, visit http://127.0.0.1:8000" -ForegroundColor DarkGray
Start-Sleep -Seconds 3
Stop-Transcript | Out-Null
