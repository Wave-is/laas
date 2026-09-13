# Run elevated once. Daily operations use the named pipe as a regular user.
param([string]$AllowedUserSid)
$ErrorActionPreference = 'Stop'
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this installer as Administrator once to install the GPU helper.'
}
if (-not $AllowedUserSid) { $AllowedUserSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value }
if ($AllowedUserSid -notmatch '^S-1-5-21-(\d+-){3}\d+$') { throw 'Expected a local user SID.' }
$sourcePath = Join-Path $PSScriptRoot 'LocalAgentGpuModeHelper.exe'
if (-not (Test-Path -LiteralPath $sourcePath)) { throw 'Build the helper first with build_helper.ps1.' }
$installPath = Join-Path $env:ProgramFiles 'LocalAgentAIStation'
$targetPath = Join-Path $installPath 'LocalAgentGpuModeHelper.exe'
foreach ($stationPath in @($installPath, $targetPath)) {
    if ((Test-Path -LiteralPath $stationPath) -and ((Get-Item -LiteralPath $stationPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Service installation paths must not be links or reparse points.'
    }
}
New-Item -ItemType Directory -Path $installPath -Force | Out-Null
# A SYSTEM service must never load its executable from a user-writable project folder.
$stationAcl = New-Object Security.AccessControl.DirectorySecurity
$stationAcl.SetAccessRuleProtection($true, $false)
$stationAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
foreach ($stationEntry in @(@('S-1-5-18','FullControl'), @('S-1-5-32-544','FullControl'), @('S-1-5-32-545','ReadAndExecute'))) {
    $stationRule = [Security.AccessControl.FileSystemAccessRule]::new(
        [Security.Principal.SecurityIdentifier]::new($stationEntry[0]),
        [Security.AccessControl.FileSystemRights]$stationEntry[1],
        [Security.AccessControl.InheritanceFlags]'ContainerInherit, ObjectInherit',
        [Security.AccessControl.PropagationFlags]::None, [Security.AccessControl.AccessControlType]::Allow)
    $stationAcl.AddAccessRule($stationRule)
}
Set-Acl -LiteralPath $installPath -AclObject $stationAcl
$serviceName = 'LocalAgentGpuModeHelper'
$existingService = Get-Service -Name $serviceName -ErrorAction SilentlyContinue
if ($existingService) { Stop-Service -Name $serviceName }
if (Test-Path -LiteralPath $targetPath) { Copy-Item -LiteralPath $targetPath -Destination ($targetPath + '.rollback') -Force }
Copy-Item -LiteralPath $sourcePath -Destination $targetPath -Force
# Replacing an existing file can preserve old explicit permissions: reset those as well.
$stationFileAcl = New-Object Security.AccessControl.FileSecurity
$stationFileAcl.SetAccessRuleProtection($false, $false)
$stationFileAcl.SetOwner([Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))
Set-Acl -LiteralPath $targetPath -AclObject $stationFileAcl
$binaryPath = '"' + $targetPath + '" --user-sid ' + $AllowedUserSid
if ($existingService) { & sc.exe config $serviceName binPath= $binaryPath start= auto }
else { & sc.exe create $serviceName binPath= $binaryPath start= auto DisplayName= 'Local Agent AI Station GPU Mode Helper' }
if ($LASTEXITCODE -ne 0) { throw 'Service registration failed.' }
Start-Service -Name $serviceName
Get-Service -Name $serviceName
