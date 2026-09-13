"""Own process identities, never stop processes merely because their names match."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import threading
import psutil
from .paths import data_dir
from .storage import atomic_write, read_document
from .hardware import hidden_options

class ProcessSupervisor:
    def __init__(self, directory=None):
        self.directory = Path(directory or data_dir())
        self.journal = self.directory / 'processes.json'
        self.records = read_document(self.journal, {})
        self._lock = threading.RLock()
        self._children = {}

    def _save(self):
        atomic_write(self.journal, self.records, backup=False)

    def owned_process(self, key):
        record = self.records.get(key)
        if not record:
            return None
        try:
            p = psutil.Process(record['pid'])
            if (abs(p.create_time() - record['created']) < .01 and
                os.path.normcase(p.exe()) == os.path.normcase(record['exe'])):
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
        return None

    def status(self, key):
        p = self.owned_process(key)
        return {'running': p is not None, 'owned': p is not None, 'pid': p.pid if p else None}

    def start(self, key, argv, *, cwd=None, env=None, visible=False):
        if not argv or not isinstance(argv, list) or not all(isinstance(a, str) and '\0' not in a for a in argv):
            raise ValueError('Expected executable and a list of arguments')
        if Path(argv[0]).suffix.lower() in ('.bat', '.cmd', '.ps1'):
            raise ValueError('Use the runtime executable directly, not a shell script')
        with self._lock:
            if self.owned_process(key):
                return self.status(key)
            logs = self.directory / 'logs'
            logs.mkdir(parents=True, exist_ok=True)
            logfile = logs / (hashlib.sha256(key.encode()).hexdigest()[:12] + '.log')
            kwargs = hidden_options()
            if visible and os.name == 'nt':
                kwargs = {'creationflags': subprocess.CREATE_NEW_CONSOLE}
            child_env = os.environ.copy()
            if env:
                child_env.update(env)
            with logfile.open('ab') as log:
                p = subprocess.Popen(argv, cwd=cwd or None, env=child_env,
                    stdin=None if visible else subprocess.DEVNULL,
                    stdout=None if visible else log, stderr=None if visible else log, **kwargs)
            try:
                identity = psutil.Process(p.pid)
                record = {'pid': p.pid, 'created': identity.create_time(), 'exe': identity.exe(), 'log': str(logfile)}
                self.records[key] = record
                self._save()
            except Exception:
                p.terminate()
                p.wait(timeout=5)
                self.records.pop(key, None)
                raise
            self._children[key] = p
            return self.status(key)

    def stop(self, key, timeout=8):
        with self._lock:
            root = self.owned_process(key)
            if not root:
                return {'success': True, 'message': 'No owned process is running'}
            try:
                tree = [root] + root.children(recursive=True)
                for process in reversed(tree):
                    try:
                        process.terminate()
                    except psutil.NoSuchProcess:
                        pass
                _, alive = psutil.wait_procs(tree, timeout=timeout)
                for process in alive:
                    process.kill()  # psutil verifies process identity to reject PID reuse.
                _, alive = psutil.wait_procs(alive, timeout=3)
                if alive:
                    return {'success': False, 'message': 'Owned processes did not stop'}
            except psutil.AccessDenied:
                return {'success': False, 'message': 'Access denied stopping owned process'}
            self.records.pop(key, None)
            self._children.pop(key, None)
            self._save()
            return {'success': True, 'message': 'Owned process stopped'}

    def tail(self, key, limit=16000):
        path = self.records.get(key, {}).get('log')
        if not path or not Path(path).is_file():
            return ''
        with Path(path).open('rb') as f:
            f.seek(max(0, Path(path).stat().st_size - limit))
            return f.read().decode('utf-8', errors='replace')

supervisor = ProcessSupervisor()
