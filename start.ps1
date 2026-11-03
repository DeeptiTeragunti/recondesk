# Launches ReconDesk on 127.0.0.1 only.
# First run creates .venv and installs the pinned dependencies from requirements.lock.txt (this needs network access).
# No telemetry is enabled. No external model service is called.
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment in .venv ..."
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "Could not create .venv. Install Python 3.11 or newer and retry." }
}

& $venvPython -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ required'"
if ($LASTEXITCODE -ne 0) { throw "The virtual environment uses an older Python. Delete .venv and retry." }

& $venvPython -m pip install --quiet -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw "Dependency installation failed." }

Write-Host "Starting ReconDesk at http://127.0.0.1:8501 (Ctrl+C to stop)"
& $venvPython -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501
