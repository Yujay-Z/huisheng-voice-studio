param([ValidateSet('app','index')][string]$Engine = 'index')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$candidates = @(
    (Join-Path $projectRoot '.venv\Scripts\python.exe'),
    (Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe')
)
$bootstrapPython = $null
foreach ($candidate in $candidates) {
    if (Test-Path -LiteralPath $candidate) { $bootstrapPython = $candidate; break }
}
if (-not $bootstrapPython) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if ($pythonCommand -and $pythonCommand.Source -notlike '*WindowsApps*') {
        $bootstrapPython = $pythonCommand.Source
    }
}
if (-not $bootstrapPython) {
    Write-Host 'Python was not found. Install Python 3.11 from https://www.python.org/downloads/windows/ and retry.'
    exit 1
}
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'logs') -Force | Out-Null
$logPath = Join-Path $projectRoot ('logs\install-' + $Engine + '.log')
Write-Host ('Installing project-local environment and models: ' + $Engine)
Write-Host 'This downloads Python, packages, and model files into this project.'
Write-Host 'System Python and PATH will not be modified.'
Write-Host ('Log file: ' + $logPath)
$installerExit = 1
Start-Transcript -LiteralPath $logPath -Append
try {
    & $bootstrapPython -u (Join-Path $projectRoot 'setup_local.py') --engine $Engine
    $installerExit = $LASTEXITCODE
} finally {
    Stop-Transcript
}
exit $installerExit
