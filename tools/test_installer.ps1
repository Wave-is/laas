# Install/repair/uninstall in an isolated directory. Refuses an existing installation.
param([string]$Installer = '')
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
if (-not $Installer) {
    $candidates = @(Get-ChildItem (Join-Path $root 'dist/release') -Filter '*-Setup-x64.exe')
    if ($candidates.Count -ne 1) { throw 'Expected exactly one release installer.' }
    $Installer = $candidates[0].FullName
}
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\{95B6AE4D-9C5A-4A23-BD31-B076AF7D2358}_is1'
if (Test-Path $key) { throw 'An installed Station exists. Do not run the isolated installer test over it.' }
$testRoot = Join-Path $root 'runtime/installer-test'
$appDir = Join-Path $testRoot 'application'
$dataDir = Join-Path $testRoot 'data'
New-Item -ItemType Directory -Path $testRoot,$dataDir -Force | Out-Null
$marker = Join-Path $dataDir 'keep.txt'
Set-Content $marker 'User data must survive uninstall.'
$report = @{}
function Run-Setup([string]$language, [string]$log) {
    $args = "/CURRENTUSER /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /NOICONS /TASKS=`"`" /LANG=$language /DIR=`"$appDir`" /LOG=`"$testRoot\$log.log`""
    $p = Start-Process -FilePath $Installer -ArgumentList $args -WindowStyle Hidden -Wait -PassThru
    if ($p.ExitCode -ne 0) { throw "Setup failed: $($p.ExitCode)" }
}
Run-Setup 'en' 'install-en'
$exe = Join-Path $appDir 'LocalAgentAIStation.exe'
if (-not (Test-Path $exe) -or -not (Test-Path $key)) { throw 'Installation or registration missing.' }
$report.install = 'passed'
$report.registryVersion = (Get-ItemProperty $key).DisplayVersion
$before = (Get-FileHash $exe).Hash
Run-Setup 'ru' 'repair-ru'
Run-Setup 'uk' 'repair-uk'
if ((Get-FileHash $exe).Hash -ne $before) { throw 'Repair changed the executable.' }
$report.repairThreeLanguages = 'passed'
$doctor = Join-Path $testRoot 'doctor.json'
$p = Start-Process -FilePath $exe -ArgumentList "--doctor --data-dir `"$dataDir`" --output `"$doctor`"" -WindowStyle Hidden -Wait -PassThru
if ($p.ExitCode -ne 0 -or -not (Test-Path $doctor)) { throw 'Installed executable diagnostics failed.' }
$report.doctor = 'passed'
$startupPath = Join-Path ([Environment]::GetFolderPath('Startup')) 'Local Agent AI Station.lnk'
if (Test-Path -LiteralPath $startupPath) { throw 'Existing Startup shortcut: refusing to overwrite it in a test.' }
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($startupPath)
$shortcut.TargetPath = $exe
$shortcut.Arguments = "--data-dir `"$dataDir`" --startup"
$shortcut.Save()
$guiStarted = Get-Date
$gui = Start-Process -FilePath $exe -ArgumentList "--no-tray --no-startup --data-dir `"$dataDir`"" -WindowStyle Hidden -PassThru
try {
    $ready = $false
    for ($i = 0; $i -lt 40; $i++) {
        try {
            $mutex = [Threading.Mutex]::OpenExisting('Local\LocalAgentAIStation.SetupGuard')
            $mutex.Dispose(); $ready = $true; break
        } catch [Threading.WaitHandleCannotBeOpenedException] { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Installed GUI did not acquire the setup guard.' }
    $blocked = Start-Process -FilePath $Installer -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /DIR=`"$appDir`" /LOG=`"$testRoot\running-app.log`"" -WindowStyle Hidden -Wait -PassThru
    if ($blocked.ExitCode -eq 0) { throw 'Installer accepted an update while Station was running.' }
    $report.runningAppGuard = 'passed'
} finally {
    # Only this newly launched, isolated executable is eligible for window close.
    $owned = @(Get-Process | Where-Object { $_.Path -eq $exe -and $_.StartTime -ge $guiStarted.AddSeconds(-1) })
    foreach ($p in $owned) { if ($p.MainWindowHandle -ne 0) { $null = $p.CloseMainWindow() } }
    foreach ($p in $owned) {
        if (-not $p.WaitForExit(15000)) { throw 'Test GUI did not exit normally. Close it before continuing.' }
    }
}
$uninstall = Join-Path $appDir 'unins000.exe'
$p = Start-Process -FilePath $uninstall -ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /LOG=`"$testRoot\uninstall.log`"" -WindowStyle Hidden -Wait -PassThru
if ($p.ExitCode -ne 0 -or (Test-Path $exe) -or (Test-Path $key)) { throw 'Uninstall failed.' }
if (-not (Test-Path $marker)) { throw 'User data removed.' }
if (Test-Path -LiteralPath $startupPath) { throw 'Owned Startup shortcut was not removed.' }
$report.uninstall = 'passed'
$report.startupCleanup = 'passed'
$report.dataPreservation = 'passed'
$report | ConvertTo-Json | Set-Content (Join-Path $testRoot 'report.json') -Encoding UTF8
$report | ConvertTo-Json
