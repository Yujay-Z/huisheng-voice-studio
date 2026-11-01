$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$appScript = Join-Path $projectRoot 'app.py'
# Match a complete argument for this project's app.py, never every Python process.
$scriptPattern = '(?i)(?:^|[\s"])' + [regex]::Escape($appScript) + '(?:[\s"]|$)'
try {
    $processes = Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'pythonw.exe'"
    $matchingProcesses = @($processes | Where-Object { $_.CommandLine -and $_.CommandLine -match $scriptPattern })
    foreach ($studioProcess in $matchingProcesses) {
        Write-Host ('Stopping this project studio server, PID: ' + $studioProcess.ProcessId)
        Stop-Process -Id $studioProcess.ProcessId -ErrorAction Stop
        $stoppedProcess = Get-Process -Id $studioProcess.ProcessId -ErrorAction SilentlyContinue
        if ($stoppedProcess) { $stoppedProcess.WaitForExit(5000) | Out-Null }
    }
    Write-Host 'Project server stopped. Model files and audio files were preserved.'
    exit 0
} catch {
    Write-Host ('Cannot stop the project server automatically: ' + $_.Exception.Message)
    Write-Host 'Close the old studio terminal window manually, then run the normal launcher.'
    exit 1
}
