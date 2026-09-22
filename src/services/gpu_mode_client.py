"""Bounded local named-pipe client. The helper only accepts typed GPU mode plans."""
import ctypes as C
import json
import os
import re
import time
from ctypes import wintypes as W
from ..i18n import tr

PIPE_NAME = r'\\.\pipe\LocalAgentGpuModeHelper'
ACTIONS = {'GetHardwareStatus', 'GetDriverModes', 'ApplyDriverModePlan', 'CancelSwitch'}

def validate_plan(plan):
    if not isinstance(plan, list) or len(plan) > 64:
        raise ValueError(tr('План может содержать не более 64 GPU'))
    seen = set()
    for item in plan:
        if not isinstance(item, dict) or set(item) != {'gpu_stable_id', 'target_mode'}:
            raise ValueError(tr('План принимает только gpu_stable_id и target_mode'))
        uid = item['gpu_stable_id']
        if not isinstance(uid, str) or not re.fullmatch(r'GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}', uid):
            raise ValueError(tr('Ожидался полный UUID GPU NVIDIA'))
        if uid.lower() in seen or item['target_mode'] not in ('WDDM', 'TCC', 'UNCHANGED'):
            raise ValueError(tr('GPU указана дважды или целевой режим недопустим'))
        seen.add(uid.lower())
    return plan

class GpuModeServiceClient:
    def __init__(self, pipe_name=PIPE_NAME):
        self.pipe_name = pipe_name
    def is_service_running(self):
        if os.name != 'nt':
            return False
        kernel = C.WinDLL('kernel32', use_last_error=True)
        kernel.WaitNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD]
        return bool(kernel.WaitNamedPipeW(self.pipe_name, 1)) or C.get_last_error() == 231
    def is_service_installed(self):
        import subprocess
        from ..hardware import hidden_options
        if os.name != 'nt':
            return False
        return subprocess.run(['sc.exe', 'query', 'LocalAgentGpuModeHelper'], capture_output=True,
            timeout=3, **hidden_options()).returncode == 0
    def send_request(self, action, payload=None, timeout_ms=5000):
        if action not in ACTIONS:
            return {'Success': False, 'Message': tr('Служба GPU не поддерживает это действие')}
        request = {'Action': action}
        if payload:
            if action != 'ApplyDriverModePlan' or set(payload) != {'Plan'}:
                return {'Success': False, 'Message': tr('Некорректные данные запроса к службе GPU')}
            try:
                request['Plan'] = validate_plan(payload['Plan'])
            except ValueError as exc:
                return {'Success': False, 'Message': str(exc)}
        if os.name != 'nt':
            return {'Success': False, 'Message': tr('Переключение режимов GPU доступно только в Windows.')}
        try:
            return self._exchange(json.dumps(request).encode('utf-8') + b'\n', timeout_ms)
        except (OSError, TimeoutError, ValueError) as exc:
            return {'Success': False, 'Message': str(exc)}
    def _exchange(self, data, timeout_ms):
        kernel = C.WinDLL('kernel32', use_last_error=True)
        class OVERLAPPED(C.Structure):
            _fields_ = [('Internal', C.c_size_t), ('InternalHigh', C.c_size_t),
                ('Offset', W.DWORD), ('OffsetHigh', W.DWORD), ('hEvent', W.HANDLE)]
        kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
        kernel.CreateFileW.restype = W.HANDLE
        kernel.CreateEventW.argtypes = [C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR]
        kernel.CreateEventW.restype = W.HANDLE
        kernel.CloseHandle.argtypes = [W.HANDLE]
        kernel.WaitNamedPipeW.argtypes = [W.LPCWSTR, W.DWORD]
        kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
        kernel.CancelIoEx.argtypes = [W.HANDLE, C.POINTER(OVERLAPPED)]
        kernel.GetOverlappedResult.argtypes = [W.HANDLE, C.POINTER(OVERLAPPED), C.POINTER(W.DWORD), W.BOOL]
        for name in ('ReadFile', 'WriteFile'):
            getattr(kernel, name).argtypes = [W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(OVERLAPPED)]
        deadline = time.monotonic() + timeout_ms / 1000
        if not kernel.WaitNamedPipeW(self.pipe_name, min(timeout_ms, 1000)):
            raise OSError(tr('Служба LocalAgentGpuModeHelper недоступна; установите её в настройках'))
        handle = kernel.CreateFileW(self.pipe_name, 0xc0000000, 0, None, 3, 0x40000000, None)
        if handle == C.c_void_p(-1).value:
            raise OSError(C.get_last_error(), tr('Не удалось подключиться к службе GPU'))
        def io(write, buffer, size):
            event = kernel.CreateEventW(None, True, False, None)
            if not event:
                raise OSError(tr('Не удалось создать событие ввода-вывода для канала службы GPU'))
            overlapped = OVERLAPPED(hEvent=event)
            count = W.DWORD()
            try:
                ok = (kernel.WriteFile if write else kernel.ReadFile)(handle, buffer, size, C.byref(count), C.byref(overlapped))
                if not ok:
                    if C.get_last_error() != 997:
                        raise OSError(C.get_last_error(), tr('Ошибка обмена данными со службой GPU'))
                    remaining = max(0, int((deadline - time.monotonic()) * 1000))
                    if kernel.WaitForSingleObject(event, remaining) != 0:
                        kernel.CancelIoEx(handle, C.byref(overlapped))
                        kernel.GetOverlappedResult(handle, C.byref(overlapped), C.byref(count), True)
                        raise TimeoutError(tr('Служба GPU не ответила вовремя; обновите состояние оборудования перед повтором'))
                    if not kernel.GetOverlappedResult(handle, C.byref(overlapped), C.byref(count), False):
                        raise OSError(C.get_last_error(), tr('Не удалось завершить обмен данными со службой GPU'))
                return count.value
            finally:
                kernel.CloseHandle(event)
        try:
            out = C.create_string_buffer(data)
            if io(True, out, len(data)) != len(data):
                raise OSError(tr('Запрос к службе GPU отправлен не полностью'))
            result = bytearray()
            while b'\n' not in result:
                buf = C.create_string_buffer(4096)
                count = io(False, buf, len(buf))
                if not count:
                    raise OSError(tr('Служба GPU разорвала соединение'))
                result.extend(buf.raw[:count])
                if len(result) > 65536:
                    raise ValueError(tr('Слишком большой ответ службы GPU'))
            response = json.loads(result.split(b'\n')[0])
            if not isinstance(response, dict):
                raise ValueError(tr('Некорректный ответ службы GPU'))
            return response
        finally:
            kernel.CloseHandle(handle)
    def get_hardware_status(self):
        return self.send_request('GetHardwareStatus')
    def get_driver_modes(self):
        return self.send_request('GetDriverModes')
    def apply_driver_mode_plan(self, plan, timeout_ms=60000):
        return self.send_request('ApplyDriverModePlan', {'Plan': plan}, timeout_ms)
    def cancel_switch(self):
        return self.send_request('CancelSwitch')

    def _tools_dir(self):
        """Return path to {app}\tools where install_helper.ps1 lives."""
        import sys
        from pathlib import Path
        if getattr(sys, 'frozen', False):
            # Frozen exe: tools/ is a sibling of the exe (or one level up for _internal layout)
            app = Path(sys.executable).parent
            for candidate in (app / 'tools', app.parent / 'tools'):
                if (candidate / 'install_helper.ps1').is_file():
                    return candidate
        # Dev mode: src/services/
        return Path(__file__).parent

    def install_service(self):
        """Launch install_helper.ps1 with UAC elevation. Returns (success, message)."""
        import subprocess
        tools = self._tools_dir()
        script = tools / 'install_helper.ps1'
        helper = tools / 'LocalAgentGpuModeHelper.exe'
        if not script.is_file():
            return False, tr('Файл install_helper.ps1 не найден в папке {path}', path=tools)
        if not helper.is_file():
            return False, tr('Файл LocalAgentGpuModeHelper.exe не найден в папке {path}', path=tools)
        try:
            import ctypes
            # ShellExecuteW with "runas" verb — shows UAC dialog
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas',
                'powershell.exe',
                f'-NoProfile -ExecutionPolicy Bypass -File "{script}" -InstallPath "{tools}"',
                str(tools), 1  # SW_SHOWNORMAL
            )
            # ShellExecuteW returns >32 on success
            if rc > 32:
                return True, tr('Служба GPU установлена. Перезапустите Station для применения.')
            return False, tr('Установка службы GPU отклонена или не удалась (код {rc})', rc=rc)
        except Exception as exc:
            return False, tr('Не удалось запустить установку службы GPU: {error}', error=exc)

    def uninstall_service(self):
        """Launch uninstall_helper.ps1 with UAC elevation. Returns (success, message)."""
        import ctypes
        tools = self._tools_dir()
        script = tools / 'uninstall_helper.ps1'
        if not script.is_file():
            return False, tr('Файл uninstall_helper.ps1 не найден в папке {path}', path=tools)
        try:
            rc = ctypes.windll.shell32.ShellExecuteW(
                None, 'runas',
                'powershell.exe',
                f'-NoProfile -ExecutionPolicy Bypass -File "{script}"',
                str(tools), 1
            )
            if rc > 32:
                return True, tr('Служба GPU удалена.')
            return False, tr('Удаление службы GPU отклонено или не удалось (код {rc})', rc=rc)
        except Exception as exc:
            return False, tr('Не удалось запустить удаление службы GPU: {error}', error=exc)


gpu_service_client = GpuModeServiceClient()
