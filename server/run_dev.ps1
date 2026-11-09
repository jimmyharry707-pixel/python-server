# Start the Baymax server for local development.
# Safe to run from either the repo root or the server/ folder.
$ErrorActionPreference = "Stop"

$serverDir = $PSScriptRoot
$repoRoot = Split-Path -Parent $serverDir
$uvicorn = Join-Path $serverDir ".venv\Scripts\uvicorn.exe"

if (-not (Test-Path $uvicorn)) {
    Write-Error "Server venv not found at $uvicorn`nCreate it first:`n  cd server`n  python -m venv .venv`n  .\.venv\Scripts\pip install -r requirements.txt"
}

if (-not (Test-Path (Join-Path $serverDir ".env"))) {
    Write-Warning ".env missing - copying from .env.example"
    Copy-Item (Join-Path $serverDir ".env.example") (Join-Path $serverDir ".env")
}

Set-Location $repoRoot
Write-Host "Starting Baymax server on http://127.0.0.1:8000 (cwd=$repoRoot)"
& $uvicorn server.main:app --reload --port 8000 --reload-dir $serverDir
