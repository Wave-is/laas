"""Station preferences. No credentials or production writes on import."""
from copy import deepcopy
from threading import RLock
from .paths import data_dir
from .storage import atomic_write, read_document, digest, ConfigurationError

CONFIG_DIR = data_dir() / 'config'
CONFIG_FILE = CONFIG_DIR / 'station.yaml'
DEFAULT_SETTINGS = {
    'version': '3.0.0', 'language': 'ru', 'station_mode': 'disabled',
    'active_engine': 'llama_swap', 'active_model_profile': 'none',
    'active_gpu_profile': 'gpu-unchanged', 'primary_agent_runtime': 'qwen-code',
    'preferred_frontend': 'qwen-desktop', 'tray_style': 'two_icons',
    'tray_theme': 'dark_tile', 'tray_display_mode': 'temp',
    'tray_metric_gpu0': 'temp', 'tray_metric_gpu1': 'temp',
    'tray_channels': None,
    'suppress_gpu_switch_warning': False,
    'poll_interval_sec': 3.0, 'excluded_gpu_uuids': [], 'autostart': False,
    'llama_swap_url': 'http://127.0.0.1:9292', 'ollama_url': 'http://127.0.0.1:11434',
    'workspace': '', 'llama_swap_executable': '', 'llama_swap_config': '',
    'llama_server_executable': '',
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
            raise ConfigurationError(f'Expected a mapping: {self.path}')
        self._data = {**deepcopy(DEFAULT_SETTINGS), **loaded}
        self._validate()
        self._digest = digest(self.path)

    def _validate(self):
        import math
        from .startup import startup_settings
        self._data['startup'] = startup_settings(self._data.get('startup'))
        from .tray_settings import validate_tray
        validate_tray(self._data)
        if type(self._data.get('suppress_gpu_switch_warning')) is not bool:
            raise ConfigurationError('suppress_gpu_switch_warning must be a boolean')
        interval = self._data.get('poll_interval_sec')
        if type(interval) not in (int, float) or not math.isfinite(interval) or not 2 <= interval <= 300:
            raise ConfigurationError('poll_interval_sec must be a finite number from 2 to 300 seconds')

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
