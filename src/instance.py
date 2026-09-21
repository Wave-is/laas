"""One control center per data directory, with a loopback SHOW-only channel and Win32 Named Mutex."""
import hashlib
import logging
import socket
import sys
import threading
import os
from pathlib import Path
from .paths import data_dir
from .i18n import tr

log = logging.getLogger(__name__)

ERROR_ALREADY_EXISTS = 183


class StationInstance:
    def __init__(self, directory=None):
        self.directory = Path(directory or data_dir()).resolve()
        key = os.path.normcase(str(self.directory)).encode('utf-8')
        digest = hashlib.sha256(key).hexdigest()
        self.port = 47000 + int(digest[:8], 16) % 1500
        self.mutex_name = f'Local\\LocalAgentAIStation_{digest[:16]}'
        self.mutex_handle = None
        self.server = None
        self.stop = threading.Event()
        self.show_callback = None
        self.pending_show = False
        self._lock = threading.Lock()

    def acquire(self):
        """Acquire single-instance lock. Returns True if this is the primary instance, False if already running."""
        # 1. Win32 Named Mutex check (instant, kernel-level, zero race condition)
        if sys.platform == 'win32':
            try:
                import ctypes
                handle = ctypes.windll.kernel32.CreateMutexW(None, False, self.mutex_name)
                last_err = ctypes.windll.kernel32.GetLastError()
                if last_err == ERROR_ALREADY_EXISTS:
                    # Another instance is already running! Signal it to show window and exit cleanly.
                    if handle:
                        ctypes.windll.kernel32.CloseHandle(handle)
                    self._signal_existing()
                    return False
                self.mutex_handle = handle
            except Exception as exc:
                log.debug("Win32 mutex check failed: %s", exc)

        # 2. Bind loopback socket for inter-process SHOW signals
        server = socket.socket()
        server.settimeout(0.5)
        try:
            server.bind(('127.0.0.1', self.port))
            server.listen(5)
        except OSError:
            server.close()
            # If socket bind fails, try to notify existing instance and exit
            signaled = self._signal_existing()
            if self.mutex_handle:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
                self.mutex_handle = None
            if signaled:
                return False
            raise RuntimeError(tr('Канал связи Station занят. Закройте предыдущий экземпляр и повторите попытку.'))

        self.server = server
        self._start_accept_loop()
        return True

    def _signal_existing(self):
        """Send SHOW command to the running primary instance."""
        try:
            with socket.create_connection(('127.0.0.1', self.port), timeout=2.0) as peer:
                peer.sendall(b'SHOW\n')
                resp = peer.recv(64)
                if resp == b'LOCAL_AGENT_AI_STATION\n':
                    return True
        except OSError:
            pass
        return True  # Return True so duplicate process exits rather than crashing

    def _start_accept_loop(self):
        def run():
            while not self.stop.is_set():
                try:
                    client, _ = self.server.accept()
                    with client:
                        client.settimeout(1.5)
                        data = client.recv(64)
                        if data == b'SHOW\n':
                            with self._lock:
                                if self.show_callback:
                                    self.show_callback()
                                else:
                                    self.pending_show = True
                            client.sendall(b'LOCAL_AGENT_AI_STATION\n')
                except (OSError, AttributeError):
                    pass
        threading.Thread(target=run, daemon=True).start()

    def listen(self, show):
        """Attach the UI show callback; triggers immediately if a SHOW was already received during startup."""
        with self._lock:
            self.show_callback = show
            if self.pending_show:
                self.pending_show = False
                try:
                    show()
                except Exception:
                    pass

    def close(self):
        self.stop.set()
        if self.server:
            try:
                self.server.close()
            except Exception:
                pass
            self.server = None
        if self.mutex_handle and sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.kernel32.CloseHandle(self.mutex_handle)
            except Exception:
                pass
            self.mutex_handle = None
