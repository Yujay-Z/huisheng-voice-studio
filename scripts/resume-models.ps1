param([ValidateSet('auto','huggingface','modelscope')][string]$Source = 'modelscope', [switch]$BigVGANOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$enginePython = Join-Path $projectRoot 'engines\index\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $enginePython)) {
    Write-Host 'IndexTTS environment was not found. Run the IndexTTS installer first.'
    exit 1
}
New-Item -ItemType Directory -Path (Join-Path $projectRoot 'logs') -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $projectRoot '.cache\temp') -Force | Out-Null
$env:TEMP = Join-Path $projectRoot '.cache\temp'
$env:TMP = $env:TEMP
Write-Host 'Resume model downloads only. Existing environments will not be reinstalled.'
Write-Host ('Download source: ' + $Source)
Write-Host 'Files and caches will be saved inside this project.'
$downloadExit = 1
Start-Transcript -LiteralPath (Join-Path $projectRoot 'logs\download-models.log') -Append
try {
    if ($BigVGANOnly) {
        & $enginePython -u (Join-Path $projectRoot 'download_models.py') --bigvgan-only
    } else {
        & $enginePython -u (Join-Path $projectRoot 'download_models.py') --source $Source
    }
    $downloadExit = $LASTEXITCODE
} finally {
    Stop-Transcript
}
exit $downloadExit
