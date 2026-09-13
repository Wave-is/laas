param([switch]$DesktopOnly, [string]$DataDirectory = $env:LOCAL_AGENT_STATION_HOME)
$ErrorActionPreference = 'Stop'
$stationRoot = $PSScriptRoot
$stationExe = Join-Path $stationRoot 'LocalAgentAIStation.exe'
$stationArgs = ''
if (-not (Test-Path -LiteralPath $stationExe)) {
    $stationExe = Join-Path $stationRoot 'dist\LocalAgentAIStation.exe'
}
if (-not (Test-Path -LiteralPath $stationExe)) {
    $stationExe = Join-Path $stationRoot '.venv\Scripts\pythonw.exe'
    if (-not (Test-Path -LiteralPath $stationExe)) { $stationExe = (Get-Command pythonw.exe -ErrorAction Stop).Source }
    $stationArgs = '"' + (Join-Path $stationRoot 'main.pyw') + '"'
}
if ($DataDirectory) {
    if ($DataDirectory.Contains('"')) { throw 'Invalid data directory.' }
    $stationArgs += ' --data-dir "' + [IO.Path]::GetFullPath($DataDirectory) + '"'
}
$stationShell = New-Object -ComObject WScript.Shell
$stationDesktop = $stationShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Local Agent AI Station.lnk'))
$stationDesktop.TargetPath = $stationExe
$stationDesktop.Arguments = $stationArgs
$stationDesktop.WorkingDirectory = $stationRoot
$stationDesktop.Description = 'Local Agent AI Station — модели, агенты и оборудование'
$stationIcon = Join-Path $stationRoot 'assets\brand\station.ico'
if (Test-Path -LiteralPath $stationIcon) { $stationDesktop.IconLocation = $stationIcon + ',0' }
elseif ($stationExe.EndsWith('LocalAgentAIStation.exe')) { $stationDesktop.IconLocation = $stationExe + ',0' }
$stationDesktop.Save()
if (-not $DesktopOnly) {
    $stationStartup = $stationShell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'Local Agent AI Station.lnk'))
    $stationStartup.TargetPath = $stationExe
    $stationStartup.Arguments = $stationArgs + ' --startup'
    $stationStartup.WorkingDirectory = $stationRoot
    $stationStartup.Description = 'Local Agent AI Station — Windows startup'
    $stationStartup.Save()
}
Write-Output 'Station shortcuts created. Components and window visibility follow the settings in Station.'
