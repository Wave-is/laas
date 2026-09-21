param([string]$Python = 'python', [string]$ISCC = '', [switch]$SkipBuild, [string]$EngineDir = $env:STATION_ENGINE_DIR)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $SkipBuild) { & ./build.ps1 -Python $Python }
    # Bundle the model engine: only llama.cpp and llama-swap binaries/licenses, never models or configs.
    $engineOut = Join-Path $PSScriptRoot 'dist/engine'
    if (Test-Path $engineOut) { Remove-Item $engineOut -Recurse -Force }
    if ($EngineDir) {
        foreach ($part in @('llama.cpp', 'llama-swap')) {
            $source = Join-Path $EngineDir $part
            if (-not (Test-Path (Join-Path $source ($(if ($part -eq 'llama.cpp') { 'llama-server.exe' } else { 'llama-swap.exe' }))))) {
                throw "Engine part missing: $source"
            }
            New-Item -ItemType Directory -Force (Join-Path $engineOut $part) | Out-Null
            Get-ChildItem $source -File | Where-Object { $_.Extension -in '.exe', '.dll' -or $_.Name -like 'LICENSE*' -or $_.Name -eq 'README.md' } |
                Copy-Item -Destination (Join-Path $engineOut $part)
        }
    } else { Write-Warning 'No -EngineDir: the installer will not include llama.cpp/llama-swap.' }
    & $Python tools/prepare_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release preparation failed.' }
    if (-not $ISCC) {
        $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
        $userCandidate = Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'
        $archiveCandidate = Join-Path $PSScriptRoot '_archive\2026-09-17\repo-leftovers\runtime\tools\inno-6.7.3\ISCC.exe'
        if (Test-Path -LiteralPath $candidate) { $ISCC = $candidate }
        elseif (Test-Path -LiteralPath $userCandidate) { $ISCC = $userCandidate }
        elseif (Test-Path -LiteralPath $archiveCandidate) { $ISCC = $archiveCandidate }
        else { $ISCC = (Get-Command ISCC.exe -ErrorAction Stop).Source }
    }
    $version = & $Python -c 'from src.version import VERSION; print(VERSION)'
    $numeric = & $Python -c 'from src.version import WINDOWS_VERSION; print(".".join(map(str, WINDOWS_VERSION)))'
    & $ISCC "/DAppVersion=$version" "/DWindowsVersion=$numeric" installer/Station.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    & $Python tools/prepare_release.py --finalize
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }
} finally { Pop-Location }
