# Thin wrapper: run scripts/verify.py with the backend venv's Python.
# Same flags as verify.py, e.g.  .\scripts\verify.ps1 --build --ui
# (If script execution is disabled: powershell -ExecutionPolicy Bypass -File scripts\verify.ps1)
$root = Split-Path -Parent $PSScriptRoot
$py = Join-Path $root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { $py = 'python' }
& $py (Join-Path $PSScriptRoot 'verify.py') @args
exit $LASTEXITCODE
