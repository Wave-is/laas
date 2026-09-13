"""Service form values and presentation, independent of Tk and networking."""
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

KINDS = {'comfyui': 'ComfyUI', 'http': 'Image worker / HTTP-сервис'}
LOCATIONS = {'remote': 'Уже работает / другой компьютер', 'local': 'Запускать на этом компьютере'}


def http_url(value):
    value = value.strip()
    url = urlsplit(value)
    try:
        valid_port = url.port is None or 1 <= url.port <= 65535
    except ValueError:
        valid_port = False
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or not valid_port or any(c.isspace() for c in value) or '\\' in value):
        raise ValueError('Укажите полный адрес http:// или https:// с корректным портом, без логина и пароля.')
    return value


def profile_from_form(original=None, *, name, location, kind, url='', health_url='',
                      executable='', working_directory='', arguments='', monitor=False, restart=False):
    profile = deepcopy(original or {})
    name = name.strip()
    if not name:
        raise ValueError('Введите название сервиса.')
    if location not in LOCATIONS or kind not in KINDS:
        raise ValueError('Выберите тип сервиса и способ запуска.')
    url = http_url(url).rstrip('/') if url.strip() else ''
    if location == 'remote' and not url:
        raise ValueError('Укажите адрес уже работающего сервиса.')
    if kind == 'comfyui':
        if not url:
            raise ValueError('Укажите адрес ComfyUI, например http://127.0.0.1:8188.')
        if urlsplit(url).query or urlsplit(url).fragment:
            raise ValueError('Для ComfyUI укажите основной адрес без параметров и #.')
        health_url = url + '/system_stats'
    elif health_url.strip():
        health_url = http_url(health_url)
    else:
        health_url = url
    profile.update(id=profile.get('id') or 'service-' + uuid4().hex[:12], name=name,
        type=location, kind=kind, url=url, health_url=health_url,
        monitor_enabled=bool(monitor), restart_policy='on_failure' if restart and location == 'local' else 'never')
    if location == 'local':
        executable = executable.strip().strip('"')
        working_directory = working_directory.strip().strip('"')
        if not Path(executable).is_absolute() or not Path(executable).is_file():
            raise ValueError('Выберите существующий файл программы, например python.exe.')
        if Path(executable).suffix.lower() in ('.bat', '.cmd', '.ps1'):
            raise ValueError('Выберите саму программу (например python.exe); её параметры укажите ниже.')
        if working_directory and not Path(working_directory).is_dir():
            raise ValueError('Рабочая папка не найдена.')
        profile.update(executable=executable, working_directory=working_directory,
            arguments=[line.strip() for line in arguments.splitlines() if line.strip()])
    else:
        for key in ('executable', 'working_directory', 'arguments'):
            profile.pop(key, None)
    from .validation import validate_registry
    validate_registry('services.yaml', [profile])
    return profile


def service_actions(profile):
    actions = ['check'] if profile.get('health_url') else []
    if profile.get('url'):
        actions += ['open', 'copy']
    if profile.get('type', 'local') == 'local':
        actions = ['start', 'stop'] + actions + ['log']
    return actions + ['edit']


def status_text(state):
    health = state.get('health', 'UNKNOWN')
    if health == 'READY':
        title = 'Доступен'
    elif health == 'UNAVAILABLE':
        title = 'Нет подключения'
    elif health == 'ERROR':
        title = 'Требует внимания'
    elif state.get('running'):
        title = 'Процесс запущен'
    else:
        title = 'Проверки выключены' if state.get('monitor_paused') else 'Ещё не проверен'
    detail = state.get('message', '')
    if state.get('checked_at'):
        from datetime import datetime
        stamp = datetime.fromtimestamp(state['checked_at']).strftime('%H:%M:%S')
        detail = f'Последняя проверка: {stamp}. ' + detail
        if state.get('monitor_paused'):
            title = 'Проверки выключены'
            detail += ' Это результат последней ручной проверки.'
    if not state.get('remote') and not state.get('running') and health not in ('READY', 'ERROR', 'UNAVAILABLE'):
        title = 'Остановлен'
    return title, detail
