# Run elevated once (Station Setup does this). Daily operations use the named pipe as a regular user.
param([string]$AllowedUserSid, [string]$InstallPath)
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer as Administrator once to install the GPU helper.'
}
if (-not $AllowedUserSid) { $AllowedUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
if ($AllowedUserSid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'Expected a local user SID.' }
# Setup places the helper next to Station (tools\..\LocalAgentGpuModeHelper.exe); a source checkout keeps it here.
$sourcePath = @((Join-Path (Split-Path $PSScriptRoot -Parent) 'LocalAgentGpuModeHelper.exe'), (Join-Path $PSScriptRoot 'LocalAgentGpuModeHelper.exe')) |
    Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $sourcePath) { throw 'LocalAgentGpuModeHelper.exe not found. Build it with build_helper.ps1.' }
if (-not $InstallPath) { $InstallPath = Split-Path $sourcePath -Parent }
$InstallPath = [IO.Path]::GetFullPath($InstallPath)
if (-not $InstallPath.StartsWith([IO.Path]::GetFullPath($env:ProgramFiles), [StringComparison]::OrdinalIgnoreCase)) {
    # A SYSTEM service must never load its executable from a user-writable folder.
    throw 'The GPU helper service must be installed under Program Files.'
}
$targetPath = Join-Path $InstallPath 'LocalAgentGpuModeHelper.exe'
foreach ($stationPath in @($InstallPath, $targetPath)) {
    if ((Test-Path -LiteralPath $stationPath) -and ((Get-Item -LiteralPath $stationPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Service installation paths must not be links or reparse points.'
    }
}
New-Item -ItemType Directory -Path $InstallPath -Force | Out-Null
$serviceName = 'LocalAgentGpuModeHelper'
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService -and $existingService.Status -ne 'Stopped') { Stop-Service -Name $serviceName }
if ([IO.Path]::GetFullPath($sourcePath) -ne $targetPath) { Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force }
$binaryPath = '"' + $targetPath + '" --user-sid ' + $AllowedUserSid
if ($existingService) { & sc.exe config $serviceName binPath= $binaryPath start= auto | Out-Null }
else { & sc.exe create $serviceName binPath= $binaryPath start= auto DisplayName= 'Local Agent AI Station GPU Mode Helper' | Out-Null }
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
& sc.exe description $serviceName 'Switches NVIDIA GPU driver modes (WDDM/TCC) for Local Agent AI Station. Typed plans only.' | Out-Null
Start-Service -Name $serviceName
Get-Service -Name $serviceName
