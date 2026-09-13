param([string]$Python = 'python')
$ErrorActionPreference='Stop'
Push-Location $PSScriptRoot
try {
    & $Python (Join-Path $PSScriptRoot 'tools\build_brand_assets.py')
    if ($LASTEXITCODE -ne 0) { throw 'Brand assets build failed.' }
    & $Python -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Tests failed. Build cancelled.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'src\services\build_helper.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'GPU helper build failed.' }
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot 'tests\helper_protocol.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'GPU helper protocol checks failed.' }
    & $Python -m PyInstaller --noconfirm LocalAgentAIStation.spec
    if ($LASTEXITCODE -ne 0) { throw 'Application build failed.' }
} finally { Pop-Location }
