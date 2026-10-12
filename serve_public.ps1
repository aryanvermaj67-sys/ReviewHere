# Runs ReviewHere in public mode, for serving through a Cloudflare Tunnel.
#
#   .\serve_public.ps1
#
# Differences from run.py:
#   * production mode  - secure cookies, no auto-reload
#   * a stable secret key, kept in .secret_key so logins survive a restart
#   * links and written tags use https://reviewhere.in, not localhost
#   * proxy headers trusted, so rate limits see the real visitor

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# ---- the secret key that signs login cookies -------------------------------
# Generated once and reused. Delete .secret_key to roll it (logs everyone out).
$keyFile = Join-Path $PSScriptRoot ".secret_key"
if (-not (Test-Path $keyFile)) {
    $generated = & ".\.venv\Scripts\python.exe" -c "import secrets; print(secrets.token_urlsafe(48))"
    Set-Content -Path $keyFile -Value $generated -Encoding utf8 -NoNewline
    Write-Host "Generated a new secret key in .secret_key" -ForegroundColor Yellow
}
$env:NFC_SECRET_KEY = (Get-Content $keyFile -Raw).Trim()

# ---- public settings --------------------------------------------------------
$env:NFC_ENV = "production"
if (-not $env:NFC_PUBLIC_BASE_URL) { $env:NFC_PUBLIC_BASE_URL = "https://reviewhere.in" }

Write-Host ""
Write-Host "  ReviewHere - public mode" -ForegroundColor Green
Write-Host "  Public address : $env:NFC_PUBLIC_BASE_URL"
Write-Host "  Local address  : http://127.0.0.1:8000"
Write-Host ""
Write-Host "  Keep this window open. Closing it takes the site offline." -ForegroundColor Yellow
Write-Host "  The Cloudflare Tunnel must be running too, in its own window."
Write-Host ""

& ".\.venv\Scripts\python.exe" -m uvicorn app.main:app `
    --host 127.0.0.1 --port 8000 `
    --proxy-headers --forwarded-allow-ips "127.0.0.1"
