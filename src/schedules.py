"""Schedules manager: time-based GPU/model switching and idle model auto-unload."""
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import yaml

from .config import config
from .i18n import tr
from .paths import data_dir
from .storage import atomic_write, UniqueLoader

log = logging.getLogger(__name__)

SCHEDULES_FILE = 'schedules.yaml'
TIME_PATTERN = re.compile(r'^([01]?[0-9]|2[0-3]):([0-5][0-9])$')

ACTION_TITLES = {
    'gpu_profile': lambda: tr('Переключить GPU-профиль'),
    'model_profile': lambda: tr('Загрузить модель'),
    'unload_model': lambda: tr('Выгрузить модель'),
    'stop_backend': lambda: tr('Остановить сервер моделей'),
}

DAY_NAMES = [
    lambda: tr('Пн'),
    lambda: tr('Вт'),
    lambda: tr('Ср'),
    lambda: tr('Чт'),
    lambda: tr('Пт'),
    lambda: tr('Сб'),
    lambda: tr('Вс'),
]


def schedules_path() -> Path:
    return data_dir() / 'config' / SCHEDULES_FILE


def format_days(days: List[int]) -> str:
    days_set = set(days)
    if days_set == set(range(7)):
        return tr('Каждый день')
    if days_set == {0, 1, 2, 3, 4}:
        return tr('Будни (Пн–Пт)')
    if days_set == {5, 6}:
        return tr('Выходные (Сб–Вс)')
    names = [DAY_NAMES[d]() for d in sorted(days) if 0 <= d < 7]
    return ', '.join(names) if names else tr('Никогда')


class ScheduleManager:
    """Manages scheduled tasks and idle model unload detection."""

    def __init__(self, action_runner: Optional[Callable[[str, str], Any]] = None,
                 notify: Optional[Callable[[str], Any]] = None):
        self.action_runner = action_runner
        self.notify = notify
        self.tasks: List[Dict[str, Any]] = []
        self.last_triggered_minute: Optional[str] = None
        self.last_request_count: Optional[int] = None
        self.last_activity_time: Optional[float] = None
        self.last_model: Optional[str] = None
        self.load_schedules()

    def load_schedules(self) -> List[Dict[str, Any]]:
        path = schedules_path()
        if not path.is_file():
            self.tasks = []
            return self.tasks
        try:
            content = path.read_text(encoding='utf-8')
            data = yaml.load(content, Loader=UniqueLoader) or []
            if isinstance(data, list):
                self.tasks = [t for t in data if isinstance(t, dict) and t.get('id')]
            else:
                self.tasks = []
        except Exception:
            log.exception('Failed to load schedules from %s', path)
            self.tasks = []
        return self.tasks

    def save_schedules(self) -> None:
        path = schedules_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            atomic_write(path, self.tasks, backup=False)
        except Exception:
            log.exception('Failed to save schedules to %s', path)

    def add_task(self, *, name: str, time_str: str, days: List[int], action: str, target: str) -> Dict[str, Any]:
        time_str = time_str.strip()
        match = TIME_PATTERN.match(time_str)
        if not match:
            raise ValueError(tr('Укажите время в формате ЧЧ:ММ (от 00:00 до 23:59)'))
        normalized_time = f'{int(match.group(1)):02d}:{int(match.group(2)):02d}'
        task_id = f'task_{int(datetime.now().timestamp())}_{len(self.tasks) + 1}'
        task = {
            'id': task_id,
            'name': name.strip() or tr('Задача в {time}', time=normalized_time),
            'enabled': True,
            'time': normalized_time,
            'days': sorted(list(set(days))),
            'action': action,
            'target': target,
        }
        self.tasks.append(task)
        self.save_schedules()
        return task

    def remove_task(self, task_id: str) -> bool:
        initial_len = len(self.tasks)
        self.tasks = [t for t in self.tasks if t.get('id') != task_id]
        if len(self.tasks) != initial_len:
            self.save_schedules()
            return True
        return False

    def toggle_task(self, task_id: str, enabled: bool) -> bool:
        for t in self.tasks:
            if t.get('id') == task_id:
                t['enabled'] = bool(enabled)
                self.save_schedules()
                return True
        return False

    def poll(self, backend_info: Optional[Dict[str, Any]], now_dt: Optional[datetime] = None) -> None:
        now_dt = now_dt or datetime.now()
        self._check_schedules(now_dt)
        self._check_idle_unload(backend_info, now_dt.timestamp())

    def _check_schedules(self, now_dt: datetime) -> None:
        current_minute = now_dt.strftime('%Y-%m-%d %H:%M')
        if current_minute == self.last_triggered_minute:
            return

        time_str = now_dt.strftime('%H:%M')
        weekday = now_dt.weekday()  # 0 = Monday

        due_tasks = [
            t for t in self.tasks
            if t.get('enabled') and t.get('time') == time_str and weekday in t.get('days', [])
        ]

        if due_tasks:
            self.last_triggered_minute = current_minute
            for task in due_tasks:
                self._execute_task(task)

    def _execute_task(self, task: Dict[str, Any]) -> None:
        action = task.get('action')
        target = task.get('target', '')
        name = task.get('name') or task.get('id')
        log.info('Executing scheduled task: %s (action=%s, target=%s)', name, action, target)
        if self.notify:
            self.notify(tr('Выполняется задача по расписанию: «{name}»', name=name))
        if self.action_runner:
            try:
                self.action_runner(action, target)
            except Exception:
                log.exception('Scheduled task failed: %s', name)

    def _check_idle_unload(self, backend_info: Optional[Dict[str, Any]], now_ts: float) -> None:
        enabled = config.get('idle_unload_enabled', False)
        if not enabled or not backend_info:
            return

        active_model = backend_info.get('active_model') or 'none'
        in_flight = backend_info.get('in_flight', 0)
        requests_total = backend_info.get('requests_total')

        # If model changed, reset timer
        if active_model != self.last_model:
            self.last_model = active_model
            self.last_activity_time = now_ts
            self.last_request_count = requests_total
            return

        # If no model loaded, nothing to unload
        if active_model == 'none':
            self.last_activity_time = now_ts
            return

        # If active requests are processing, update activity time
        if in_flight > 0:
            self.last_activity_time = now_ts

        # If request count changed, update activity time
        if requests_total is not None and requests_total != self.last_request_count:
            self.last_request_count = requests_total
            self.last_activity_time = now_ts

        if self.last_activity_time is None:
            self.last_activity_time = now_ts
            return

        timeout_minutes = int(config.get('idle_unload_timeout_minutes', 30))
        timeout_seconds = timeout_minutes * 60

        idle_seconds = now_ts - self.last_activity_time
        if idle_seconds >= timeout_seconds:
            log.info('Model %s idle for %d s (timeout %d s); auto-unloading', active_model, idle_seconds, timeout_seconds)
            self.last_activity_time = now_ts  # reset to avoid repeat triggers
            if self.notify:
                self.notify(tr('Модель «{model}» выгружена из-за отсутствия запросов ({mins} мин).',
                               model=active_model, mins=timeout_minutes))
            if self.action_runner:
                try:
                    self.action_runner('unload_model', 'none')
                except Exception:
                    log.exception('Auto-unload failed')
