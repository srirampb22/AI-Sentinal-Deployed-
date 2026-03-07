$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "[AI-Sentinal] Starting local server..."

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    & $venvPython "app.py"
    exit $LASTEXITCODE
}

Write-Host "[AI-Sentinal] No runtime env found." -ForegroundColor Yellow
Write-Host "Run SETUP_LOCAL.bat once, then run RUN_LOCAL.ps1."
exit 1
