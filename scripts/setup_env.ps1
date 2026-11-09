# Build the project environment from scratch on Windows (Python 3.13).
#   scripts\setup_env.ps1
# Prerequisites: winget install astral-sh.uv OpenJS.NodeJS (uv installs Python 3.13 itself)
$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot; $Venv = Join-Path $Root '.venv'; $Py = Join-Path $Venv 'Scripts\python.exe'
function Say($m) { Write-Host "`n==> $m" }
Say "virtual environment: $Venv"
Say 'dependencies from uv.lock (the runtime and the database layer, no test tooling)'
$env:UV_PROJECT_ENVIRONMENT = $Venv
uv sync --locked --no-dev --group db --python 3.13 --project $Root
$env:PYTHONPATH = Join-Path $Root 'src'
Say 'checking'
& $Py -m app doctor
if (Get-Command npm -ErrorAction SilentlyContinue) { Push-Location (Join-Path $Root 'web'); npm install --silent; npm run build; Pop-Location; Write-Host 'web app built' }
else { Write-Host 'npm not found: winget install OpenJS.NodeJS, then cd web; npm install; npm run build' }
