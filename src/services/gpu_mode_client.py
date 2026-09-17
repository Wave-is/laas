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

gpu_service_client = GpuModeServiceClient()
