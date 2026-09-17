# Removes only the Station 3 helper registration. Does not touch GPU modes or user data.
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this uninstaller as Administrator.'
}
$stationService = Get-Service -Name 'LocalAgentGpuModeHelper' -ErrorAction SilentlyContinue
if ($stationService) {
    if ($stationService.Status -ne 'Stopped') { Stop-Service -Name 'LocalAgentGpuModeHelper' }
    & sc.exe delete 'LocalAgentGpuModeHelper'
    if ($LASTEXITCODE -ne 0) { throw 'Service removal failed.' }
}
Write-Output 'LocalAgentGpuModeHelper is unregistered. GPU modes and user data are unchanged.'
