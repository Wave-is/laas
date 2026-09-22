# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules
root=Path(SPECPATH)
from PyInstaller.utils.win32.versioninfo import VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct, VarFileInfo, VarStruct
release = {}
exec((root/'src/version.py').read_text(encoding='utf-8'), release)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=release['WINDOWS_VERSION'], prodvers=release['WINDOWS_VERSION'],
                     mask=0x3f, flags=0x2, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[StringFileInfo([StringTable('040904B0', [
        StringStruct('CompanyName', 'Local Agent AI Station contributors'),
        StringStruct('FileDescription', 'Local Agent AI Station'),
        StringStruct('FileVersion', release['VERSION']),
        StringStruct('ProductName', 'Local Agent AI Station'),
        StringStruct('ProductVersion', release['VERSION']),
        StringStruct('OriginalFilename', 'LocalAgentAIStation.exe'),
        StringStruct('LegalCopyright', 'Copyright 2026 Hermes Station Contributors; Wave-is and contributors'),
    ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
datas=collect_data_files('customtkinter')
for p in (root/'src/agents').glob('*/manifest.yaml'):
    datas.append((str(p),str(p.parent.relative_to(root))))
for name in ('install_helper.ps1','uninstall_helper.ps1','LocalAgentGpuModeHelper.exe'):
    datas.append((str(root/'src/services'/name),'src/services'))
datas += [(str(root/'docs/examples'),'docs/examples')]
datas += [(str(root/'docs/TESTING_GUIDE_RU.md'),'docs')]
datas += [(str(root/'docs'/name),'docs') for name in ('SERVICES_GUIDE_RU.md','SERVICES_GUIDE_EN.md','SERVICES_GUIDE_UK.md')]
datas += [(str(root/'assets/brand'),'assets/brand')]
datas += [(str(p), str(p.parent.relative_to(root))) for p in (root/'locales').glob('*/*.json')]
a=Analysis(['main.pyw'],pathex=[str(root)],datas=datas,
    hiddenimports=collect_submodules('src.agents') + ['src.agents.'+p.parent.name+'.adapter' for p in (root/'src/agents').glob('*/manifest.yaml')],
    excludes=['torch','tensorflow','matplotlib','pandas','numpy','scipy','IPython','pytest'],
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='LocalAgentAIStation',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(root / 'assets/brand/station.ico'),
    version=version_info
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='LocalAgentAIStation'
)
