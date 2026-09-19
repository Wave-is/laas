"""Own process identities, never stop processes merely because their names match."""
import hashlib
import re
import json
import os
from pathlib import Path
import subprocess
import threading
import psutil
from .paths import data_dir, logs_dir
from .storage import atomic_write, read_document
from .hardware import hidden_options
from .i18n import tr

def log_name(key):
    """Readable log file name for a process key, e.g. service:llama-swap -> service-llama-swap."""
    name = re.sub(r'[^A-Za-z0-9._-]+', '-', key).strip('.-')[:80]
    return name or hashlib.sha256(key.encode()).hexdigest()[:12]

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
            raise ValueError(tr('Не указана программа для запуска или её параметры заданы неверно. Проверьте путь и параметры в настройках.'))
        if Path(argv[0]).suffix.lower() in ('.bat', '.cmd', '.ps1'):
            raise ValueError(tr('Укажите .exe-файл, а не .bat/.cmd/.ps1: {path}', path=argv[0]))
        with self._lock:
            if self.owned_process(key):
                return self.status(key)
            logs = logs_dir()
            logs.mkdir(parents=True, exist_ok=True)
            logfile = logs / (log_name(key) + '.log')
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
                return {'success': True, 'message': tr('Процесс не запущен из Station')}
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
                    return {'success': False, 'message': tr('Процесс (PID {pid}) или его дочерние процессы не остановились. '
                        'Закройте их вручную в Диспетчере задач.', pid=root.pid)}
            except psutil.AccessDenied:
                return {'success': False, 'message': tr('Нет прав на остановку процесса (PID {pid}). '
                    'Закройте его вручную или запустите Station от имени администратора.', pid=root.pid)}
            self.records.pop(key, None)
            self._children.pop(key, None)
            self._save()
            return {'success': True, 'message': tr('Процесс остановлен (PID {pid})', pid=root.pid)}

    def tail(self, key, limit=16000):
        path = self.records.get(key, {}).get('log')
        if not path or not Path(path).is_file():
            return ''
        with Path(path).open('rb') as f:
            f.seek(max(0, Path(path).stat().st_size - limit))
            return f.read().decode('utf-8', errors='replace')

supervisor = ProcessSupervisor()
