"""Let Windows Setup refuse updates/removal while any Station GUI is running."""
import os

MUTEX_NAME = r'Local\LocalAgentAIStation.SetupGuard'
GLOBAL_MUTEX_NAME = r'Global\LocalAgentAIStation.SetupGuard'
_handle = None
_global_handle = None


def hold_setup_guard():
    global _handle, _global_handle
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    if _handle is None:
        _handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
        if not _handle:
            raise ctypes.WinError(ctypes.get_last_error())
    if _global_handle is None:
        try:
            _global_handle = kernel.CreateMutexW(None, False, GLOBAL_MUTEX_NAME)
        except Exception:
            pass
    # Windows closes the handles at process termination, after Tk/tray shutdown.


def release_setup_guard():
    global _handle, _global_handle
    if os.name != 'nt':
        return
    import ctypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    if _handle:
        try:
            kernel.CloseHandle(_handle)
        except Exception:
            pass
        _handle = None
    if _global_handle:
        try:
            kernel.CloseHandle(_global_handle)
        except Exception:
            pass
        _global_handle = None

