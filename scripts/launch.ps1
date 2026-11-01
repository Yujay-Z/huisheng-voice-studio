$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$appPython = Join-Path $projectRoot 'runtime\app\python.exe'
if (-not (Test-Path -LiteralPath $appPython)) {
    $appPython = Join-Path $projectRoot '.venv\Scripts\python.exe'
}
if (-not (Test-Path -LiteralPath $appPython)) {
    $appPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
}
if (-not (Test-Path -LiteralPath $appPython)) {
    Write-Host 'Please run the model installer first.'
    exit 1
}
Write-Host 'Starting local studio. Keep this window open while using the app.'
Write-Host 'Local address: http://127.0.0.1:7865'
& $appPython -u (Join-Path $projectRoot 'app.py')
exit $LASTEXITCODE
