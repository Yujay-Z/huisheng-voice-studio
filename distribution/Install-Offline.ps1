param(
    [switch]$Worker,
    [string]$Target,
    [string]$Bundle = $PSScriptRoot,
    [string]$StatusFile,
    [switch]$SkipRuntime,
    [switch]$SkipShortcut,
    [switch]$SkipCheck
)
$ErrorActionPreference = 'Stop'
$Bundle = [IO.Path]::GetFullPath($Bundle)
$manifestPath = Join-Path $Bundle 'offline-manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$checkedDirectories = [Collections.Generic.HashSet[string]]::new([StringComparer]::OrdinalIgnoreCase)

function File-Sha256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $input = [IO.File]::OpenRead($Path)
    try { return ([BitConverter]::ToString($algorithm.ComputeHash($input))).Replace('-','').ToLowerInvariant() }
    finally { $input.Dispose(); $algorithm.Dispose() }
}

function Write-State([string]$State, [string]$Message, [double]$Percent) {
    if (-not $StatusFile) { return }
    $value = @{ state=$State; message=$Message; percent=[Math]::Min(100,[Math]::Max(0,$Percent)); target=$Target }
    $temporary = $StatusFile + '.tmp'
    [IO.File]::WriteAllText($temporary, ($value | ConvertTo-Json -Compress), $utf8)
    for ($attempt=0; $attempt -lt 10; $attempt++) {
        try { Move-Item -LiteralPath $temporary -Destination $StatusFile -Force; return }
        catch { if($attempt -eq 9){throw}; Start-Sleep -Milliseconds 25 }
    }
}

function Safe-Path([string]$Relative) {
    if ([IO.Path]::IsPathRooted($Relative)) { throw '安装包包含绝对路径。' }
    $full = [IO.Path]::GetFullPath((Join-Path $Target $Relative))
    $prefix = $Target.TrimEnd('\') + '\'
    if (-not $full.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw '安装路径越界。' }
    $cursor = [IO.Path]::GetDirectoryName($full)
    while ($cursor -and $cursor.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) {
        if ($checkedDirectories.Contains($cursor)) { break }
        if (Test-Path -LiteralPath $cursor) {
            if ((Get-Item -LiteralPath $cursor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '安装目录中存在不允许的链接。' }
            $checkedDirectories.Add($cursor) | Out-Null
        }
        $cursor = [IO.Path]::GetDirectoryName($cursor)
    }
    if ((Test-Path -LiteralPath $full) -and ((Get-Item -LiteralPath $full -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw '安装文件不能是链接。' }
    return $full
}

if ($Worker) {
    try {
        if (-not $Target) { throw '未指定安装目录。' }
        $Target = [IO.Path]::GetFullPath($Target).TrimEnd('\')
        if (-not [Environment]::Is64BitOperatingSystem) { throw '需要 Windows 64 位系统。' }
        $recordPath = Join-Path $Target '.offline-install-progress.json'
        $record = $null
        if (Test-Path -LiteralPath $recordPath) {
            $record = Get-Content -LiteralPath $recordPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($record.package_id -ne $manifest.package_id) { throw '此目录属于另一套安装包，请选择新的空目录。' }
        } elseif ((Test-Path -LiteralPath $Target) -and @(Get-ChildItem -LiteralPath $Target -Force).Count -gt 0) {
            throw '为保护已有文件，请选择空目录；不能覆盖现有项目。'
        }
        [IO.Directory]::CreateDirectory($Target) | Out-Null
        if ((Get-Item -LiteralPath $Target -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw '请选择实际目录，不要使用目录链接。' }
        if (-not $record) {
            $drive = New-Object IO.DriveInfo([IO.Path]::GetPathRoot($Target))
            if ($drive.AvailableFreeSpace -lt ([long]$manifest.installed_bytes + 2GB)) { throw '安装磁盘剩余空间不足。请预留程序体积外至少 2 GB 工作空间。' }
            $record = [pscustomobject]@{ package_id=$manifest.package_id; completed_volumes=0; written_bytes=0; state='extracting' }
            [IO.File]::WriteAllText($recordPath,($record | ConvertTo-Json),$utf8)
        }
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $chunks = @{}
        foreach ($chunk in $manifest.chunks) { $chunks[$chunk.entry] = $chunk }
        $written = [long]$record.written_bytes
        $buffer = New-Object byte[] (1MB)
        $lastReport = [datetime]::UtcNow
        for ($part=[int]$record.completed_volumes; $part -lt $manifest.volumes.Count; $part++) {
            $volume = $manifest.volumes[$part]
            if ([IO.Path]::GetFileName($volume.name) -ne $volume.name) { throw '分卷文件名无效。' }
            $archivePath = Join-Path $Bundle $volume.name
            if (-not (Test-Path -LiteralPath $archivePath)) { throw ('缺少分卷：' + $volume.name) }
            Write-State 'running' ('校验分卷 ' + ($part+1) + '/' + $manifest.volumes.Count) (90*$written/[Math]::Max(1,[long]$manifest.installed_bytes))
            if ((Get-Item -LiteralPath $archivePath).Length -ne [long]$volume.bytes) { throw ('分卷大小不符：' + $volume.name) }
            $hash = File-Sha256 $archivePath
            if ($hash -ne $volume.sha256) { throw ('分卷 SHA-256 校验失败：' + $volume.name) }
            $archive = [IO.Compression.ZipFile]::OpenRead($archivePath)
            try {
                foreach ($entry in $archive.Entries) {
                    if ($entry.FullName.StartsWith('payload/')) {
                        $relative = $entry.FullName.Substring(8)
                        $offset = [long]0
                    } elseif ($chunks.ContainsKey($entry.FullName)) {
                        $chunk = $chunks[$entry.FullName]
                        $relative = $chunk.path
                        $offset = [long]$chunk.offset
                        if ($entry.Length -ne [long]$chunk.bytes) { throw '权重分块大小错误。' }
                    } else { throw ('未知安装条目：' + $entry.FullName) }
                    $file = Safe-Path $relative
                    [IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($file)) | Out-Null
                    $input = $entry.Open()
                    $output = $null
                    try {
                        if ($offset -eq 0) {
                            $output = [IO.File]::Open($file,[IO.FileMode]::Create,[IO.FileAccess]::Write,[IO.FileShare]::None)
                        } else {
                            $output = [IO.File]::Open($file,[IO.FileMode]::Open,[IO.FileAccess]::Write,[IO.FileShare]::None)
                            if ($output.Length -lt $offset) { throw ('前一权重分块不完整：' + $relative) }
                            $output.SetLength($offset)
                            $output.Position = $offset
                        }
                        while (($count = $input.Read($buffer,0,$buffer.Length)) -gt 0) {
                            $output.Write($buffer,0,$count)
                            $written += $count
                            if (([datetime]::UtcNow-$lastReport).TotalMilliseconds -ge 250) {
                                Write-State 'running' ('解压分卷 ' + ($part+1) + '/' + $manifest.volumes.Count + ' · ' + $relative) (90*$written/[Math]::Max(1,[long]$manifest.installed_bytes))
                                $lastReport = [datetime]::UtcNow
                            }
                        }
                    } finally {
                        $input.Dispose()
                        if ($output) { $output.Dispose() }
                    }
                }
            } finally { $archive.Dispose() }
            $record.completed_volumes = $part+1
            $record.written_bytes = $written
            [IO.File]::WriteAllText($recordPath,($record | ConvertTo-Json),$utf8)
        }
        if ([long]$record.written_bytes -ne [long]$manifest.installed_bytes) { throw '安装文件总大小与清单不一致。' }
        $record.state = 'extracted'
        [IO.File]::WriteAllText($recordPath,($record | ConvertTo-Json),$utf8)
        if (-not $SkipRuntime) {
            $runtime = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64' -ErrorAction SilentlyContinue
            if (-not $runtime -or $runtime.Installed -ne 1 -or ([version]($runtime.Version.TrimStart('v'))) -lt [version]'14.40') {
                Write-State 'running' '安装包内的微软运行库，若 Windows 提示，请允许系统安装。' 91
                $redist = Safe-Path '_prerequisites/VC_redist.x64.exe'
                $process = Start-Process -FilePath $redist -ArgumentList '/install','/quiet','/norestart' -Verb RunAs -WindowStyle Hidden -PassThru -Wait
                if ($process.ExitCode -notin @(0,1638,3010)) { throw ('微软运行库安装未完成，退出码：' + $process.ExitCode) }
            }
        }
        if (-not $SkipCheck) {
            Write-State 'running' '离线检查 Python、音频组件、本地识别和 NVIDIA 显卡…' 94
            [IO.Directory]::CreateDirectory((Join-Path $Target 'logs')) | Out-Null
            $env:PYTHONUTF8 = '1'
            $env:PYTHONIOENCODING = 'utf-8'
            $checkOutput = & (Join-Path $Target 'runtime/app/python.exe') (Join-Path $Target 'distribution/check_install.py') --json 2>&1
            $checkCode = $LASTEXITCODE
            [IO.File]::WriteAllText((Join-Path $Target 'logs/offline-install-check.log'),($checkOutput -join "`n"),$utf8)
            if ($checkCode -ne 0) { throw ('环境检查未通过：' + ($checkOutput -join "`n")) }
        }
        if (-not $SkipShortcut) {
            Write-State 'running' '创建离线版桌面快捷方式…' 99
            & (Join-Path $Target 'scripts/create-shortcut.ps1') -ShortcutName '音色工作室（离线版）' | Out-Null
        }
        $record.state = 'installed'
        [IO.File]::WriteAllText($recordPath,($record | ConvertTo-Json),$utf8)
        Write-State 'done' '安装完成，可以从桌面一键启动。首次启动仍需把模型加载到显卡。' 100
        exit 0
    } catch {
        Write-State 'error' $_.Exception.Message 0
        [Console]::Error.WriteLine($_.Exception.ToString())
        exit 1
    }
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object Windows.Forms.Form
$form.Text = '回声 · 音色工作室离线安装'
$form.Size = New-Object Drawing.Size(690,450)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.BackColor = [Drawing.Color]::FromArgb(248,246,241)
$form.Font = New-Object Drawing.Font('Microsoft YaHei',10)
$title = New-Object Windows.Forms.Label
$title.Text = '回声 · 离线安装'
$title.Font = New-Object Drawing.Font('Microsoft YaHei',17)
$title.SetBounds(26,24,600,40)
$description = New-Object Windows.Forms.Label
$description.Text = ('全部模型与运行环境已包含，无需联网下载。版本 ' + $manifest.version + "`nWindows 10/11 64 位 · NVIDIA BF16 显卡 · 建议 8 GB 以上显存")
$description.SetBounds(28,76,620,55)
$pathBox = New-Object Windows.Forms.TextBox
$pathBox.Text = Join-Path $env:LOCALAPPDATA 'HuishengVoiceStudio'
$pathBox.SetBounds(28,147,520,28)
$browse = New-Object Windows.Forms.Button
$browse.Text = '选择目录'
$browse.SetBounds(558,145,90,31)
$browse.Add_Click({
    $dialog = New-Object Windows.Forms.FolderBrowserDialog
    $dialog.Description = '选择空安装目录，或同一安装包先前的安装目录'
    if ($dialog.ShowDialog() -eq 'OK') { $pathBox.Text = $dialog.SelectedPath }
    $dialog.Dispose()
})
$accept = New-Object Windows.Forms.CheckBox
$accept.Text = '我已阅读并接受所附程序、模型和第三方组件的许可条款'
$accept.SetBounds(28,195,570,28)
$licenses = New-Object Windows.Forms.LinkLabel
$licenses.Text = '查看许可'
$licenses.SetBounds(570,198,80,25)
$licenses.Add_Click({
    $window = New-Object Windows.Forms.Form
    $window.Text = '第三方许可'
    $window.Size = New-Object Drawing.Size(760,600)
    $window.StartPosition = 'CenterParent'
    $text = New-Object Windows.Forms.TextBox
    $text.Multiline = $true; $text.ReadOnly = $true; $text.ScrollBars = 'Both'; $text.Dock = 'Fill'
    $parts = @('THIRD_PARTY_NOTICES.md','IndexTTS-LICENSE.txt','README-OFFLINE.md') | ForEach-Object { $p=Join-Path $Bundle $_; if(Test-Path -LiteralPath $p){[IO.File]::ReadAllText($p)} }
    $text.Text = $parts -join "`r`n`r`n"
    $window.Controls.Add($text)
    $window.ShowDialog($form) | Out-Null
    $window.Dispose()
})
$progress = New-Object Windows.Forms.ProgressBar
$progress.SetBounds(28,241,620,17)
$statusLabel = New-Object Windows.Forms.Label
$statusLabel.Text = ('预计解压 ' + [Math]::Round([long]$manifest.installed_bytes/1GB,1) + ' GB。请把全部分卷保存在安装程序旁。')
$statusLabel.SetBounds(28,273,620,60)
$install = New-Object Windows.Forms.Button
$install.Text = '安装'
$install.SetBounds(520,350,128,36)
$install.Enabled = $false
$accept.Add_CheckedChanged({ if (-not $script:installProcess) { $install.Enabled=$accept.Checked } })
$script:installProcess = $null
$script:statusPath = Join-Path ([IO.Path]::GetTempPath()) ('huisheng-install-' + [guid]::NewGuid().ToString('N') + '.json')
$timer = New-Object Windows.Forms.Timer
$timer.Interval = 250
$timer.Add_Tick({
    if (Test-Path -LiteralPath $script:statusPath) {
        try {
            $state = Get-Content -LiteralPath $script:statusPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $statusLabel.Text=$state.message
            $progress.Value=[Math]::Min(100,[Math]::Max(0,[int]$state.percent))
            if ($state.state -in @('done','error')) {
                $timer.Stop()
                $script:installProcess=$null
                $install.Enabled=$true
                if ($state.state -eq 'done') { $install.Text='启动软件'; $install.Tag='launch' }
                else { $install.Text='重试安装'; $browse.Enabled=$true; $pathBox.Enabled=$true }
            }
        } catch { }
    }
    if ($script:installProcess -and $script:installProcess.HasExited) {
        $timer.Stop();$script:installProcess=$null
        $statusLabel.Text='安装进程已退出。请检查分卷完整性、目标目录和 Windows 安全提示，然后重试。'
        $install.Enabled=$true;$browse.Enabled=$true;$pathBox.Enabled=$true
    }
})
$install.Add_Click({
    if ($install.Tag -eq 'launch') {
        Start-Process -FilePath (Join-Path $pathBox.Text '音色工作室.exe') -WorkingDirectory $pathBox.Text -WindowStyle Hidden
        $form.Close();return
    }
    $targetPath=$pathBox.Text.Trim()
    if (-not $targetPath) { return }
    $install.Enabled=$false;$browse.Enabled=$false;$pathBox.Enabled=$false
    $statusLabel.Text='开始离线安装…'
    if(Test-Path -LiteralPath $script:statusPath){Remove-Item -LiteralPath $script:statusPath}
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -Worker -Bundle "' + $Bundle + '" -Target "' + $targetPath + '" -StatusFile "' + $script:statusPath + '"'
    $script:installProcess=Start-Process -FilePath (Join-Path $PSHOME 'powershell.exe') -ArgumentList $arguments -WindowStyle Hidden -PassThru
    $timer.Start()
})
$form.Add_FormClosing({
    param($sender,$eventArgs)
    if ($script:installProcess -and -not $script:installProcess.HasExited) {
        $eventArgs.Cancel=$true
        $statusLabel.Text='安装仍在执行，请等待完成后关闭。'
    }
})
$form.Controls.AddRange(@($title,$description,$pathBox,$browse,$accept,$licenses,$progress,$statusLabel,$install))
[Windows.Forms.Application]::Run($form)
$timer.Dispose()
$form.Dispose()
