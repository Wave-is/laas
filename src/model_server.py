"""One place that answers: which model server, where it listens, which files it uses.

The model server is llama-swap: a small proxy that listens on one port and starts
llama-server (llama.cpp) for the requested model. Station always feeds it the
configuration generated from its model registry.
"""
import os
from pathlib import Path
from urllib.parse import urlsplit
from .config import config
from .paths import data_dir

SERVER_TITLE = 'Сервер моделей (llama-swap)'
SWAP_EXE = 'llama-swap.exe' if os.name == 'nt' else 'llama-swap'
LLAMA_SERVER_EXE = 'llama-server.exe' if os.name == 'nt' else 'llama-server'
SEARCH_DEPTH = 3


def generated_config_path() -> Path:
    return data_dir() / 'backend/llama-swap.yaml'


def port() -> int:
    url = urlsplit(config.get('llama_swap_url') or 'http://127.0.0.1:9292')
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1'):
        raise ValueError('Адрес сервера моделей должен быть локальным: http://127.0.0.1:<порт>')
    return url.port or 9292


def lan_enabled() -> bool:
    return config.get('llama_swap_lan_access') is True


def listen_address() -> str:
    return ('0.0.0.0' if lan_enabled() else '127.0.0.1') + f':{port()}'


def local_url() -> str:
    return f'http://127.0.0.1:{port()}'


def api_url() -> str:
    return local_url() + '/v1'


def lan_addresses():
    """IPv4 addresses of active network adapters, excluding loopback and link-local."""
    try:
        import psutil
        stats = psutil.net_if_stats()
        result = []
        for name, rows in psutil.net_if_addrs().items():
            if name in stats and not stats[name].isup:
                continue
            for row in rows:
                if row.family.name == 'AF_INET' and not row.address.startswith(('127.', '169.254.')):
                    result.append(row.address)
        return sorted(set(result))
    except Exception:
        return []


def lan_urls():
    return [f'http://{ip}:{port()}/v1' for ip in lan_addresses()] if lan_enabled() else []


def _search(folder, name):
    if not folder:
        return None
    root = Path(folder)
    if not root.is_dir():
        return None
    queue = [(root, 0)]
    while queue:
        current, depth = queue.pop(0)
        candidate = current / name
        if candidate.is_file():
            return candidate
        if depth >= SEARCH_DEPTH:
            continue
        try:
            children = sorted(p for p in current.iterdir() if p.is_dir() and not p.name.startswith('.'))
        except OSError:
            continue
        queue.extend((child, depth + 1) for child in children)
    return None


def runtime_dir():
    """Folder with llama.cpp and llama-swap. Older settings stored only the two executables."""
    value = config.get('runtime_dir')
    if value:
        return Path(value)
    for key in ('llama_swap_executable', 'llama_server_executable'):
        exe = config.get(key)
        if exe and Path(exe).is_file():
            parent = Path(exe).parent
            return parent.parent if parent.name.lower() in ('llama-swap', 'llama.cpp', 'bin') else parent
    return None


def models_dir():
    value = config.get('models_dir')
    return Path(value) if value else None


def detect_models_dir(profiles):
    """The folder holding most existing model files of the registry, if any."""
    from collections import Counter
    parents = Counter(str(Path(p.weights_path).parent) for p in profiles
                      if p.weights_path and Path(p.weights_path).is_absolute() and Path(p.weights_path).is_file())
    return Path(parents.most_common(1)[0][0]) if parents else None


def fill_missing_folders(profiles):
    """Settings created before these fields existed: store what is already in use, once."""
    changes = {}
    if not config.get('runtime_dir') and runtime_dir():
        changes['runtime_dir'] = str(runtime_dir())
    if not config.get('models_dir'):
        found = detect_models_dir(profiles)
        if found:
            changes['models_dir'] = str(found)
    if changes:
        config.update(changes)
    return changes


def find_executable(name):
    """An explicit path wins when it exists; otherwise search the engine folder."""
    key = 'llama_swap_executable' if name == SWAP_EXE else 'llama_server_executable'
    explicit = config.get(key)
    if explicit and Path(explicit).is_file():
        return Path(explicit)
    return _search(runtime_dir(), name)


def swap_executable():
    return find_executable(SWAP_EXE)


def llama_server_executable():
    return find_executable(LLAMA_SERVER_EXE)


def resolve_model_file(value):
    """Profiles may store absolute paths or paths relative to the models folder."""
    if not value:
        return value
    path = Path(value)
    if path.is_absolute() or not models_dir():
        return str(path)
    return str(models_dir() / path)


def describe_missing_runtime():
    folder = runtime_dir()
    where = f'в папке движка {folder}' if folder else 'папка движка не выбрана'
    return ('Не найден llama-swap.exe или llama-server.exe (' + where + '). '
            'Укажите папку движка в «Настройки → Папки и сервер моделей».')
