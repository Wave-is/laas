"""Let Windows Setup refuse updates/removal while any Station GUI is running."""
import os

MUTEX_NAME = r'Local\LocalAgentAIStation.SetupGuard'
_handle = None


def hold_setup_guard():
    global _handle
    if os.name != 'nt' or _handle is not None:
        return
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
    kernel.CreateMutexW.restype = wintypes.HANDLE
    _handle = kernel.CreateMutexW(None, False, MUTEX_NAME)
    if not _handle:
        raise ctypes.WinError(ctypes.get_last_error())
    # Windows closes the handle at process termination, after Tk/tray shutdown.
