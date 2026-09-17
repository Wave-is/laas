"""Current-user Startup shortcut; never schedules an elevated task or separate LLM."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
from .paths import data_dir, SOURCE_DIR
from .hardware import hidden_options
from .i18n import tr

# Fixed script, data supplied on stdin as JSON, never interpolated into shell code.
SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$folder = if ($request.folder) { $request.folder } else { [Environment]::GetFolderPath('Startup') }
$path = Join-Path $folder 'Local Agent AI Station.lnk'
$shell = New-Object -ComObject WScript.Shell
if (Test-Path -LiteralPath $path) {
    $old = $shell.CreateShortcut($path)
    $owned = ([IO.Path]::GetFileName($old.TargetPath) -eq 'LocalAgentAIStation.exe') -or
        ($old.Description -eq 'Local Agent AI Station — Windows startup') -or
        (([IO.Path]::GetFileName($old.TargetPath) -in @('python.exe','pythonw.exe')) -and $old.Arguments.Contains('main.pyw'))
    if ($request.action -ne 'get' -and -not $owned) { throw 'STATION_SHORTCUT_FOREIGN' }
}
if ($request.action -eq 'enable') {
    if (-not (Test-Path -LiteralPath $request.target -PathType Leaf)) { throw 'STATION_TARGET_MISSING' }
    [IO.Directory]::CreateDirectory($folder) | Out-Null
    $temporary = Join-Path $folder (([Guid]::NewGuid().ToString()) + '.lnk')
    try {
        $link = $shell.CreateShortcut($temporary)
        $link.TargetPath = $request.target
        $link.Arguments = $request.arguments
        $link.WorkingDirectory = $request.working_directory
        $link.Description = 'Local Agent AI Station — Windows startup'
        $link.IconLocation = $request.icon
        $link.Save()
        Move-Item -LiteralPath $temporary -Destination $path -Force
    } finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
} elseif ($request.action -eq 'disable') {
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path }
}
if (Test-Path -LiteralPath $path) {
    $link = $shell.CreateShortcut($path)
    @{enabled=$true; target=$link.TargetPath; arguments=$link.Arguments; path=$path} | ConvertTo-Json -Compress
} else { @{enabled=$false; path=$path} | ConvertTo-Json -Compress }
'''


def launch_command():
    if getattr(sys, 'frozen', False):
        executable, arguments = Path(sys.executable), []
        working = executable.parent
        icon = str(executable) + ',0'
    else:
        executable = Path(sys.executable).with_name('pythonw.exe')
        arguments = [str(SOURCE_DIR / 'main.pyw')]
        working = SOURCE_DIR
        icon = str(SOURCE_DIR / 'assets/brand/station.ico') + ',0'
    if not executable.is_file():
        raise ValueError(tr('Не найден EXE Station или pythonw.exe. Используйте готовую Windows-сборку.'))
    arguments += ['--data-dir', str(data_dir()), '--startup']
    return {'target': str(executable.resolve()), 'arguments': subprocess.list2cmdline(arguments),
            'working_directory': str(working), 'icon': icon}


class WindowsStartup:
    def __init__(self, folder=None):
        # Alternate folder is only used by integration tests; GUI always uses Windows Startup.
        self.folder = str(Path(folder).resolve()) if folder else None

    def _call(self, action):
        if os.name != 'nt':
            raise OSError(tr('Автозагрузка доступна в Windows.'))
        payload = {'action': action, 'folder': self.folder}
        if action == 'enable':
            payload.update(launch_command())
        powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        encoded = base64.b64encode(SCRIPT.encode('utf-16-le')).decode('ascii')
        result = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                                input=json.dumps(payload, ensure_ascii=True), capture_output=True,
                                encoding='utf-8', errors='replace', timeout=15, **hidden_options())
        if result.returncode:
            if 'STATION_SHORTCUT_FOREIGN' in result.stderr:
                raise OSError(tr('Этот ярлык принадлежит другой программе. Изменения не выполнены.'))
            if 'STATION_TARGET_MISSING' in result.stderr:
                raise OSError(tr('Программа Station не найдена.'))
            raise OSError(tr('Не удалось изменить автозагрузку Windows. {details}', details=result.stderr.strip()[-600:]))
        return json.loads(result.stdout.strip().lstrip('\ufeff'))

    def status(self):
        return self._call('get')

    def set_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError(tr('Ожидается значение да/нет.'))
        result = self._call('enable' if enabled else 'disable')
        if result['enabled'] != enabled:
            raise OSError(tr('Windows не подтвердила изменение автозагрузки.'))
        return result
