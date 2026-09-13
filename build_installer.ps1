param([string]$Python = 'python', [string]$ISCC = '', [switch]$SkipBuild)
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    if (-not $SkipBuild) { & ./build.ps1 -Python $Python }
    & $Python tools/prepare_release.py
    if ($LASTEXITCODE -ne 0) { throw 'Release preparation failed.' }
    if (-not $ISCC) {
        $candidate = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
        if (Test-Path -LiteralPath $candidate) { $ISCC = $candidate }
        else { $ISCC = (Get-Command ISCC.exe -ErrorAction Stop).Source }
    }
    $version = & $Python -c 'from src.version import VERSION; print(VERSION)'
    $numeric = & $Python -c 'from src.version import WINDOWS_VERSION; print(".".join(map(str, WINDOWS_VERSION)))'
    & $ISCC "/DAppVersion=$version" "/DWindowsVersion=$numeric" installer/Station.iss
    if ($LASTEXITCODE -ne 0) { throw 'Installer compilation failed.' }
    & $Python tools/prepare_release.py --finalize
    if ($LASTEXITCODE -ne 0) { throw 'Release verification failed.' }
} finally { Pop-Location }
