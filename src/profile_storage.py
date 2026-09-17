"""Independent profile registries. Missing files never overwrite existing files."""
from pathlib import Path
from threading import RLock
from .paths import data_dir
from .storage import atomic_write, read_document, digest, ConfigurationError
from .profiles_schema import GpuHardwareProfile, ModelProfile, StationPreset

STORAGE_DIR = data_dir() / 'config'
GPU_PROFILES_FILE = STORAGE_DIR / 'hardware_profiles.yaml'
MODEL_PROFILES_FILE = STORAGE_DIR / 'model_profiles.yaml'
STATION_PRESETS_FILE = STORAGE_DIR / 'station_presets.yaml'

def get_default_gpu_profiles():
    return [
        GpuHardwareProfile(id='gpu-unchanged', name='Текущая конфигурация', general_policy='unchanged'),
        GpuHardwareProfile(id='gpu-all-wddm', name='Все совместимые GPU: WDDM', general_policy='all_wddm'),
        GpuHardwareProfile(id='gpu-one-graphics-rest-compute', name='Графика + выделенные вычисления', general_policy='one_graphics_rest_compute'),
        GpuHardwareProfile(id='gpu-all-tcc', name='Все совместимые GPU: TCC', general_policy='all_tcc', prefer_p2p=True),
        GpuHardwareProfile(id='gpu-largest-vram-only', name='GPU с наибольшей памятью', general_policy='largest_vram'),
        GpuHardwareProfile(id='gpu-nvlink-clique', name='Лучшая подтверждённая P2P группа', general_policy='best_p2p_clique', prefer_p2p=True),
    ]

def get_default_model_profiles():
    return [ModelProfile(id='none', name='Без модели', weights_path='', min_gpu_count=0,
        min_total_vram_mib=0, min_free_vram_per_gpu_mib=0, split_mode='none', backend='cpu',
        status='stable', qualified=True)]

def get_default_station_presets():
    return [
        StationPreset(id='work', name='Работа', gpu_profile_id='gpu-all-wddm', is_builtin=True,
            description='Графический режим. Выберите установленную модель и агента.'),
        StationPreset(id='compromise', name='Компромисс', gpu_profile_id='gpu-one-graphics-rest-compute',
            is_builtin=True, description='Графическая GPU и доступные вычислительные GPU. Требуется выбор модели.'),
        StationPreset(id='super-ai', name='Супер ИИ', gpu_profile_id='gpu-all-tcc', is_builtin=True,
            description='Совместимые GPU в вычислительном режиме. Агент и модель выбираются независимо.'),
    ]

class ProfileStorage:
    TYPES = {'gpu_profiles': ('hardware_profiles.yaml', GpuHardwareProfile, get_default_gpu_profiles),
             'model_profiles': ('model_profiles.yaml', ModelProfile, get_default_model_profiles),
             'station_presets': ('station_presets.yaml', StationPreset, get_default_station_presets)}

    def __init__(self, directory=None):
        self.directory = Path(directory or STORAGE_DIR)
        self._lock = RLock()
        self._digests = {}
        self.extras = {name: {} for name in self.TYPES}
        self.warnings = []
        self.load_all()

    def load_all(self):
        staged = {}
        hashes = {}
        extras = {}
        warnings = []
        for name, (filename, cls, defaults) in self.TYPES.items():
            path = self.directory / filename
            rows = read_document(path, None)
            if rows is None and not path.exists():
                values = defaults()
            else:
                if not isinstance(rows, list):
                    raise ConfigurationError(f'Expected a list: {path}')
                from .validation import validate_registry
                unknown = {}
                validate_registry(filename, rows, strict=False, unknown_fields=unknown)
                values = [cls.from_dict(row) for row in rows]
                # Fields written by other tools are preserved on save and reported, never fatal.
                extras[name] = {row['id']: {k: row[k] for k in unknown[row['id']]} for row in rows if row['id'] in unknown}
                warnings += [f'{filename}: профиль «{id}» содержит поля, которые Station не использует: {", ".join(keys)}'
                             for id, keys in unknown.items()]
            ids = [p.id for p in values]
            if len(ids) != len(set(ids)):
                raise ConfigurationError(f'Duplicate profile ID: {path}')
            staged[name] = {p.id: p for p in values}
            hashes[name] = digest(path)
        for name, values in staged.items():
            setattr(self, name, values)
        self._digests = hashes
        self.extras = {name: extras.get(name, {}) for name in self.TYPES}
        self.warnings = warnings

    def _save(self, name):
        path = self.directory / self.TYPES[name][0]
        rows = [{**p.to_dict(), **self.extras.get(name, {}).get(p.id, {})} for p in getattr(self, name).values()]
        atomic_write(path, rows, expected_digest=self._digests[name])
        self._digests[name] = digest(path)

    def save_all(self):
        with self._lock:
            for name in self.TYPES:
                self._save(name)

    def _put(self, name, profile):
        with self._lock:
            collection = getattr(self, name)
            old = collection.copy()
            collection[profile.id] = profile
            try:
                self._save(name)
            except Exception:
                setattr(self, name, old)
                raise

    def get_gpu_profile(self, pid):
        return self.gpu_profiles.get(pid)
    def get_model_profile(self, mid):
        return self.model_profiles.get(mid)
    def get_station_preset(self, sid):
        return self.station_presets.get(sid)
    def save_gpu_profile(self, profile):
        self._put('gpu_profiles', profile)
    def save_model_profile(self, profile):
        self._put('model_profiles', profile)
    def save_station_preset(self, profile):
        self._put('station_presets', profile)
    def delete_station_preset(self, sid):
        with self._lock:
            if sid not in self.station_presets:
                return False
            old = self.station_presets.pop(sid)
            try:
                self._save('station_presets')
            except Exception:
                self.station_presets[sid] = old
                raise
            return True

profile_storage = ProfileStorage()
