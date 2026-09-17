"""Fallback when the GPU helper service is not installed: run the helper once with a UAC prompt.

The elevated process accepts only the same typed plan as the service and writes its result
to a new file in a private temporary folder. Nothing stays running afterwards.
"""
import ctypes as C
from ctypes import wintypes as W
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from .gpu_mode_client import validate_plan
from ..i18n import tr

HELPER = 'LocalAgentGpuModeHelper.exe'
ERROR_CANCELLED = 1223


def helper_executable():
    """Prefer the copy installed next to Station in Program Files (not user-writable)."""
    candidates = []
    if getattr(sys, 'frozen', False):
        candidates.append(Path(sys.executable).parent / HELPER)
    from ..paths import SOURCE_DIR
    candidates.append(SOURCE_DIR / 'src/services' / HELPER)
    return next((path for path in candidates if path.is_file()), None)


class SHELLEXECUTEINFOW(C.Structure):
    _fields_ = [('cbSize', W.DWORD), ('fMask', C.c_ulong), ('hwnd', W.HWND), ('lpVerb', W.LPCWSTR),
                ('lpFile', W.LPCWSTR), ('lpParameters', W.LPCWSTR), ('lpDirectory', W.LPCWSTR),
                ('nShow', C.c_int), ('hInstApp', W.HINSTANCE), ('lpIDList', C.c_void_p),
                ('lpClass', W.LPCWSTR), ('hkeyClass', W.HKEY), ('dwHotKey', W.DWORD),
                ('hIconOrMonitor', W.HANDLE), ('hProcess', W.HANDLE)]


def apply_plan_elevated(plan, timeout_ms=120000):
    if os.name != 'nt':
        return {'Success': False, 'Message': tr('Переключение режимов GPU доступно только в Windows.')}
    validate_plan(plan)
    exe = helper_executable()
    if not exe:
        return {'Success': False, 'Message': tr('Не найден {helper}. Переустановите Station.', helper=HELPER)}
    folder = Path(tempfile.mkdtemp(prefix='station-gpu-'))
    try:
        plan_path, result_path = folder / 'plan.json', folder / 'result.json'
        plan_path.write_text(json.dumps({'Plan': plan}), encoding='utf-8')
        info = SHELLEXECUTEINFOW(cbSize=C.sizeof(SHELLEXECUTEINFOW), fMask=0x40,  # SEE_MASK_NOCLOSEPROCESS
            lpVerb='runas', lpFile=str(exe), nShow=0,
            lpParameters=subprocess.list2cmdline(['--apply-plan', str(plan_path), '--result', str(result_path)]))
        shell = C.WinDLL('shell32', use_last_error=True)
        shell.ShellExecuteExW.argtypes = [C.POINTER(SHELLEXECUTEINFOW)]
        if not shell.ShellExecuteExW(C.byref(info)):
            if C.get_last_error() == ERROR_CANCELLED:
                return {'Success': False, 'Message': tr('Переключение GPU отменено: права администратора не подтверждены.')}
            return {'Success': False, 'Message': tr('Не удалось запустить переключение GPU (ошибка Windows {code}).', code=C.get_last_error())}
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.CloseHandle.argtypes = [W.HANDLE]
        try:
            if kernel.WaitForSingleObject(info.hProcess, timeout_ms) != 0:
                return {'Success': False, 'Message': tr('Переключение GPU не завершилось вовремя. Проверьте режимы карт перед повтором.')}
        finally:
            kernel.CloseHandle(info.hProcess)
        if not result_path.is_file():
            return {'Success': False, 'Message': tr('Переключение GPU не вернуло результат. Проверьте режимы карт.')}
        result = json.loads(result_path.read_text(encoding='utf-8'))
        return result if isinstance(result, dict) else {'Success': False, 'Message': tr('Некорректный ответ переключения GPU.')}
    finally:
        shutil.rmtree(folder, ignore_errors=True)
