"""Station preferences. No credentials or production writes on import."""
from copy import deepcopy
from threading import RLock
from .paths import data_dir
from .storage import atomic_write, read_document, digest, ConfigurationError
from .i18n import tr, system_language

CONFIG_DIR = data_dir() / 'config'
CONFIG_FILE = CONFIG_DIR / 'station.yaml'
def _default_models_folder():
    from pathlib import Path
    try:
        if Path('D:/').exists():
            return 'D:\\LLM'
    except Exception:
        pass
    return 'C:\\LLM'

DEFAULT_SETTINGS = {
    'version': '3.0.0', 'language': system_language(), 'station_mode': 'disabled',
    'active_engine': 'llama_swap', 'active_model_profile': 'none',
    'active_gpu_profile': 'gpu-unchanged', 'primary_agent_runtime': 'qwen-code',
    'preferred_frontend': 'qwen-desktop', 'tray_style': 'dual_tile',
    'tray_theme': 'dark_tile', 'tray_display_mode': 'temp',
    'tray_metric_gpu0': 'temp', 'tray_metric_gpu1': 'temp',
    'tray_channels': None,
    'suppress_gpu_switch_warning': False,
    'poll_interval_sec': 3.0, 'excluded_gpu_uuids': [], 'autostart': False,
    'llama_swap_url': 'http://127.0.0.1:9292', 'ollama_url': 'http://127.0.0.1:11434',
    'monitoring_alert_threshold_c': 90,
    # runtime_dir holds llama.cpp and llama-swap; explicit executables override the search there.
    'workspace': '', 'last_agent_folder': '', 'runtime_dir': '', 'models_dir': '',
    'logs_dir': '', 'llama_swap_lan_access': False,
    'llama_swap_executable': '', 'llama_server_executable': '',
    'cluster_nodes': None, 'cluster_poll_interval_sec': 2.0,
}

class AppConfig:
    def __init__(self, path=None):
        self.path = path or CONFIG_FILE
        self._lock = RLock()
        self._data = deepcopy(DEFAULT_SETTINGS)
        self.load()

    def load(self):
        loaded = read_document(self.path, {})
        if not isinstance(loaded, dict):
            raise ConfigurationError(tr('Ожидался словарь настроек: {path}', path=self.path))
        self._data = {**deepcopy(DEFAULT_SETTINGS), **loaded}
        self._validate()
        self._digest = digest(self.path)

    def _validate(self):
        import math
        from .startup import startup_settings
        self._data['startup'] = startup_settings(self._data.get('startup'))
        from .tray_settings import validate_tray
        validate_tray(self._data)
        for key in ('llama_swap_lan_access', 'suppress_gpu_switch_warning'):
            if type(self._data.get(key)) is not bool:
                raise ConfigurationError(tr('{key}: нужно логическое значение (true/false)', key=key))
        if type(self._data.get('suppress_gpu_switch_warning')) is not bool:
            raise ConfigurationError(tr('{key}: нужно логическое значение (true/false)', key='suppress_gpu_switch_warning'))
        interval = self._data.get('poll_interval_sec')
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 2 <= interval <= 300:
            raise ConfigurationError(tr('poll_interval_sec: нужно число от 2 до 300 секунд'))

    def save(self):
        atomic_write(self.path, self._data, expected_digest=self._digest)
        self._digest = digest(self.path)

    def get(self, key, default=None):
        with self._lock:
            return deepcopy(self._data.get(key, default))

    def set(self, key, value):
        self.update({key: value})

    def update(self, changes):
        with self._lock:
            old = deepcopy(self._data)
            self._data.update(deepcopy(changes))
            try:
                self._validate()
                self.save()
            except Exception:
                self._data = old
                raise

config = AppConfig()
