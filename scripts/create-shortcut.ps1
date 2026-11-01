param([string]$Destination,[string]$ShortcutName)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$settings = Get-Content -LiteralPath (Join-Path $projectRoot 'config.json') -Encoding UTF8 -Raw | ConvertFrom-Json
if (-not $Destination) { $Destination = [Environment]::GetFolderPath('Desktop') }
$Destination = [System.IO.Path]::GetFullPath($Destination)
if (-not (Test-Path -LiteralPath $Destination -PathType Container)) {
    New-Item -ItemType Directory -Path $Destination -Force | Out-Null
}
$pythonWindowless = Join-Path $projectRoot 'runtime\app\pythonw.exe'
if (-not (Test-Path -LiteralPath $pythonWindowless)) {
    $pythonWindowless = Join-Path $projectRoot '.venv\Scripts\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonWindowless)) {
    $pythonWindowless = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\pythonw.exe'
}
if (-not (Test-Path -LiteralPath $pythonWindowless)) { throw 'pythonw.exe was not found.' }
$targetScript = Join-Path $projectRoot 'desktop_launcher.pyw'
if (-not (Test-Path -LiteralPath $targetScript)) { throw 'Desktop launcher was not found.' }
if (-not $ShortcutName) { $ShortcutName = $settings.app_name }
$shortcutPath = Join-Path $Destination ($ShortcutName + '.lnk')
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$launcher = Join-Path $projectRoot ($settings.app_name + '.exe')
$target = $pythonWindowless
$arguments = '"' + $targetScript + '"'
if (Test-Path -LiteralPath $launcher) { $target = $launcher; $arguments = '' }
if ($shortcut.TargetPath -and $shortcut.TargetPath -ne $target) {
    throw 'A different shortcut already uses this name. It was not overwritten.'
}
$shortcut.TargetPath = $target
$shortcut.Arguments = $arguments
$shortcut.WorkingDirectory = $projectRoot
$shortcut.Description = 'Local voice cloning studio'
$shortcut.IconLocation = (Join-Path $env:SystemRoot 'System32\mmsys.cpl') + ',0'
$shortcut.WindowStyle = 7
$shortcut.Save()
$saved = $shell.CreateShortcut($shortcutPath)
if ($saved.TargetPath -ne $target -or $saved.Arguments -ne $shortcut.Arguments) {
    throw 'Shortcut verification failed.'
}
Write-Output ('Shortcut created: ' + $shortcutPath)
Write-Output ('Target: ' + $saved.TargetPath)
Write-Output ('Arguments: ' + $saved.Arguments)
