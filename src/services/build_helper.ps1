$ErrorActionPreference = 'Stop'
$compilerPath = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $compilerPath)) { throw '.NET Framework 4 compiler is required.' }
$outputPath = Join-Path $PSScriptRoot 'LocalAgentGpuModeHelper.exe'
& $compilerPath /nologo /target:exe /optimize+ /platform:x64 "/out:$outputPath" /r:System.ServiceProcess.dll /r:System.Web.Extensions.dll (Join-Path $PSScriptRoot 'LocalAgentGpuModeHelper.cs')
if ($LASTEXITCODE -ne 0) { throw 'GPU helper compilation failed.' }
Write-Output $outputPath
