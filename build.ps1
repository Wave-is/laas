param([string]$Python = 'python')
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    # Release builds use reviewed, committed assets. Re-generating PNG/ICO with a
    # different Pillow version can change bytes and dirty a clean checkout.
    foreach ($asset in @('station.svg','station.png','station.ico')) {
        if (-not (Test-Path (Join-Path $PSScriptRoot "assets/brand/$asset"))) {
            throw 'Brand assets missing. Run tools/build_brand_assets.py and commit them first.'
        }
    }
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Build cancelled.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'src\services\build_helper.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'GPU helper build failed.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'tests\helper_protocol.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'GPU helper protocol checks failed.' }
    & $Python -m PyInstaller --noconfirm LocalAgentAIStation.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally { Pop-Location }
