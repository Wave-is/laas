"""Restart a Station-owned model server (llama-swap) that died unexpectedly.

The watchdog only acts on a server it has seen running as Station-owned. A deliberate stop
removes the supervisor record (or is announced with expect_stopped()); a crash leaves the
record with a dead PID. Servers started outside Station are never touched.
"""
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from .i18n import tr

KEY = 'service:llama-swap'
log = logging.getLogger(__name__)


class ModelServerWatchdog:
    def __init__(self, *, record_exists, process_alive, load_model, start_server, stop_server=None,
                 active_model=lambda: 'none', model_name=lambda model: model, address=lambda: '',
                 is_busy=lambda: False, enabled=lambda: True, notify=None, history_path=None,
                 threshold=3, hang_polls=20, max_restarts=3, window=600, backoff=(10, 30, 60),
                 clock=time.monotonic, spawn=None, history_limit=500, stop_grace=120):
        self.record_exists, self.process_alive = record_exists, process_alive
        self.load_model, self.start_server, self.stop_server = load_model, start_server, stop_server
        self.active_model, self.model_name, self.address = active_model, model_name, address
        self.is_busy, self.enabled, self.notify = is_busy, enabled, notify
        self.history_path = Path(history_path) if history_path else None
        self.threshold, self.hang_polls = threshold, hang_polls
        self.max_restarts, self.window, self.backoff = max_restarts, window, tuple(backoff)
        self.clock, self.history_limit, self.stop_grace = clock, history_limit, stop_grace
        self.spawn = spawn or (lambda target: threading.Thread(target=target, daemon=True, name='model-server-watchdog').start())
        self._lock = threading.RLock()
        self._history_lock = threading.Lock()
        self.expected = False
        self.model = 'none'
        self.failures = 0
        self.outage = False
        self.next_attempt = None
        self.restarts = []
        self.restarting = False
        self.stop_requested_at = None
        self.last_event = self._last_saved_event()

    def expect_stopped(self):
        """Call before stopping the server on purpose: the next outage is not a crash."""
        with self._lock:
            self.expected = False
            self._clear_outage()
            self.stop_requested_at = self.clock()

    def _clear_outage(self):
        self.failures, self.outage, self.next_attempt = 0, False, None

    def poll(self, info):
        """Inspect one backend_info snapshot. Returns a short state name (for tests and logs)."""
        with self._lock:
            if not self.enabled():
                self.expected = False
                self._clear_outage()
                return 'disabled'
            if self.restarting:
                return 'restarting'
            online, owned = bool(info and info.get('online')), bool(info and info.get('owned'))
            stop_requested = self.stop_requested_at is not None and self.clock() - self.stop_requested_at < self.stop_grace
            if online and owned:
                self._clear_outage()
                if stop_requested:
                    return 'stopping'
                self.stop_requested_at = None
                self.expected = True
                self.model = self.active_model() or 'none'
                return 'ok'
            if online:
                self.expected = False
                self._clear_outage()
                return 'external'
            if stop_requested:
                self.stop_requested_at = None
                self.expected = False
                return 'stopped'
            if not self.expected:
                return 'idle'
            # supervisor.stop() removes the record; a crash leaves it with a dead PID.
            if not self.outage and not self.record_exists():
                self.expected = False
                self._clear_outage()
                return 'stopped'
            alive = self.process_alive()
            now = self.clock()
            self.restarts = [t for t in self.restarts if now - t < self.window]
            if not self.outage:
                self.failures += 1
                if self.failures < (self.hang_polls if alive else self.threshold):
                    return 'suspect'
                self.outage = True
                self.next_attempt = now + self._delay()
                message = (tr('Сервер моделей не отвечает, хотя его процесс работает. Station перезапустит его.') if alive else
                    tr('Сервер моделей неожиданно завершился. Station перезапустит его.'))
                self._event('hang' if alive else 'crash', message, notify=len(self.restarts) < self.max_restarts)
            if len(self.restarts) >= self.max_restarts:
                self.expected = False
                self._clear_outage()
                self._event('gave_up', tr('Сервер моделей перезапускался {count} раз за {minutes} мин и снова остановился. '
                    'Автоперезапуск прекращён: проверьте журнал сервера и запустите его вручную.',
                    count=len(self.restarts), minutes=self.window // 60))
                return 'gave_up'
            if now < self.next_attempt:
                return 'backoff'
            if self.is_busy():
                return 'busy'
            self.restarts.append(now)
            self.restarting = True
            model = self.model
            self.spawn(lambda: self._restart(model, alive))
            return 'restart'

    def _delay(self):
        return self.backoff[min(len(self.restarts), len(self.backoff) - 1)] if self.backoff else 0

    def _restart(self, model, alive):
        try:
            if alive and self.stop_server:
                stopped = self.stop_server()
                if not stopped.get('Success'):
                    raise RuntimeError(stopped.get('Message') or tr('процесс не остановился'))
            result = self.load_model(model) if model and model != 'none' else self.start_server()
            success, detail = bool(result.get('Success')), result.get('Message') or ''
        except Exception as exc:
            log.exception('Model server restart failed')
            success, detail = False, str(exc)
        with self._lock:
            self.restarting = False
            name = self.model_name(model) if model and model != 'none' else None
            if success:
                self._clear_outage()
                self.expected = True
                address = self.address()
                if name:
                    self._event('restarted', tr('Сервер моделей перезапущен, модель «{name}» загружена. Адрес: {address}',
                        name=name, address=address), model)
                else:
                    self._event('restarted', tr('Сервер моделей перезапущен без модели. Адрес: {address}', address=address), model)
            else:
                self.next_attempt = self.clock() + self._delay()
                self._event('restart_failed', tr('Не удалось перезапустить сервер моделей: {error}', error=detail[:300]), model, notify=False)

    def _event(self, event, message, model=None, notify=True):
        entry = {'time': datetime.now().isoformat(timespec='seconds'), 'event': event,
                 'model': model if model is not None else self.model, 'message': message}
        self.last_event = entry
        self._append(entry)
        if notify and self.notify:
            try:
                self.notify(message)
            except Exception:
                log.exception('Watchdog notification failed')

    def _append(self, entry):
        if not self.history_path:
            return
        with self._history_lock:
            try:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                with self.history_path.open('a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')
                lines = self.history_path.read_text(encoding='utf-8').splitlines()
                if len(lines) > self.history_limit:
                    self.history_path.write_text('\n'.join(lines[-self.history_limit:]) + '\n', encoding='utf-8')
            except OSError:
                log.exception('Cannot write watchdog history')

    def history(self, limit=100):
        if not self.history_path or not self.history_path.is_file():
            return []
        entries = []
        for line in self.history_path.read_text(encoding='utf-8', errors='replace').splitlines()[-limit:]:
            try:
                entries.append(json.loads(line))
            except ValueError:
                continue
        return entries

    def _last_saved_event(self):
        try:
            entries = self.history(1)
        except OSError:
            return None
        return entries[-1] if entries else None


def create_watchdog(is_busy=lambda: False, notify=None):
    """Watchdog wired to the real supervisor, model server and settings."""
    from .config import config
    from .gpu_modes import gpu_mode_manager
    from .paths import data_dir
    from .process_manager import pm
    from .profile_storage import profile_storage
    from .supervisor import supervisor
    from . import model_server

    def local_model():
        model = profile_storage.get_model_profile(config.get('active_model_profile', 'none'))
        return model.id if model and model.provider_type not in ('openai_compatible', 'ollama') else 'none'

    def model_name(model_id):
        model = profile_storage.get_model_profile(model_id)
        return model.name if model else model_id

    def busy():
        if is_busy() or gpu_mode_manager.state != 'IDLE':
            return True
        if not gpu_mode_manager._lock.acquire(blocking=False):
            return True
        gpu_mode_manager._lock.release()
        return False

    return ModelServerWatchdog(
        record_exists=lambda: KEY in supervisor.records,
        process_alive=lambda: supervisor.status(KEY)['running'],
        load_model=gpu_mode_manager.apply_model_profile_only,
        start_server=gpu_mode_manager.start_backend,
        stop_server=pm.stop_llama_swap,
        active_model=local_model, model_name=model_name, address=model_server.api_url,
        is_busy=busy, enabled=lambda: config.get('watchdog_enabled', True) is not False,
        notify=notify, history_path=data_dir() / 'logs' / 'watchdog.jsonl')
