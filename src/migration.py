"""Explicit, previewable migration; Station 2 files are never changed."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
import os
import shlex
import shutil
import tempfile
from .config import DEFAULT_SETTINGS
from .paths import legacy_dir, data_dir
from .profile_storage import get_default_gpu_profiles, get_default_model_profiles, get_default_station_presets
from .profiles_schema import ModelProfile, GpuHardwareProfile, StationPreset
from .storage import read_document, atomic_write, digest
from .i18n import tr

@dataclass
class MigrationPlan:
    source: Path
    destination: Path
    documents: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    destination_hashes: dict = field(default_factory=dict)
    def summary(self):
        return {'source': str(self.source), 'destination': str(self.destination),
            'files': list(self.documents), 'model_ids': [m['id'] for m in self.documents['model_profiles.yaml']],
            'warnings': self.warnings, 'legacy_files_unchanged': True}

def models_from_swap(path):
    doc = read_document(Path(path), {})
    models = []
    for mid, entry in doc.get('models', {}).items():
        # llama-swap accepts quoted commands; existing Windows stack uses forward slash paths.
        tokens = shlex.split(entry.get('cmd', ''), posix=True)
        def arg(*flags, default=''):
            for flag in flags:
                if flag in tokens and tokens.index(flag) + 1 < len(tokens):
                    return tokens[tokens.index(flag) + 1]
            return default
        weights = arg('-m', '--model')
        if not weights:
            continue
        mmproj = arg('--mmproj') or None
        models.append(ModelProfile(id=mid, name=mid, weights_path=weights, mmproj_path=mmproj,
            context=int(arg('-c', '--ctx-size', default='4096')), batch=int(arg('-b', '--batch-size', default='2048')),
            ubatch=int(arg('-ub', '--ubatch-size', default='512')), mtp_depth=int(arg('--spec-draft-n-max', default='0')),
            split_mode=arg('--split-mode', default='layer'), tensor_split_policy='auto',
            vision=bool(mmproj), startup_key=mid, status='manual', qualified=False,
            min_gpu_count=1, min_total_vram_mib=0, min_free_vram_per_gpu_mib=0,
            prefer_p2p=True, require_p2p=False, modalities=['text', 'image'] if mmproj else ['text']))
    return models

def preview_migration(source=None, destination=None, swap_config=None):
    source, destination = Path(source or legacy_dir()), Path(destination or data_dir())
    plan = MigrationPlan(source, destination)
    settings = read_document(source / 'settings.json', {})
    station = dict(DEFAULT_SETTINGS)
    station.update({k: v for k, v in settings.items() if k in DEFAULT_SETTINGS})
    station.update(version='3.0.0', active_model_profile='none', station_mode='disabled', autostart=False)
    gpu_profiles = {p.id: p for p in get_default_gpu_profiles()}
    for row in read_document(source / 'gpu_profiles.json', []):
        p = GpuHardwareProfile.from_dict(row)
        # Preserve custom UUID rules, but correct reusable built-in templates.
        if p.id not in gpu_profiles:
            gpu_profiles[p.id] = p
    models = {m.id: m for m in get_default_model_profiles()}
    for row in read_document(source / 'model_profiles.json', []):
        model = ModelProfile.from_dict(row)
        if model.id == 'none':
            continue
        model.qualified = False
        model.status = 'manual'
        model.tensor_split_policy = 'auto'
        model.require_p2p = False if model.id == 'qwen3.8-27b-production' else model.require_p2p
        # A legacy fictional game profile is retained as disabled, not certified production.
        if model.id == 'game-agent-production':
            model.status = 'disabled'
        models[model.id] = model
        if not Path(model.weights_path).is_file():
            plan.warnings.append(tr('{id}: файл весов модели не найден', id=model.id))
    if swap_config:
        swap_config = Path(swap_config)
        for model in models_from_swap(swap_config):
            existing = models.get(model.id)
            if existing:
                model.name = existing.name
                model.min_total_vram_mib = existing.min_total_vram_mib
                model.min_free_vram_per_gpu_mib = existing.min_free_vram_per_gpu_mib
                model.min_gpu_count = existing.min_gpu_count
            models[model.id] = model
        station['llama_swap_config'] = str(swap_config)
        plan.sources[str(swap_config)] = digest(swap_config)
    presets = {p.id: p for p in get_default_station_presets()}
    for row in read_document(source / 'station_presets.json', []):
        p = StationPreset.from_dict(row)
        if p.id not in presets:
            p.auto_start_model = p.auto_start_agents = False
            presets[p.id] = p
    for id in ('work', 'super-ai'):
        if 'qwen3.8-27b-production' in models:
            presets[id].model_profile_id = 'qwen3.8-27b-production'
    plan.documents = {
        'station.yaml': station,
        'hardware_profiles.yaml': [p.to_dict() for p in gpu_profiles.values()],
        'model_profiles.yaml': [p.to_dict() for p in models.values()],
        'station_presets.yaml': [p.to_dict() for p in presets.values()],
        'agent_runtimes.yaml': [{'id': id, 'optional': True} for id in ('hermes', 'qwen-code', 'pi', 'openclaw', 'aider')],
        'agent_frontends.yaml': [], 'services.yaml': [], 'secrets.references.yaml': {},
        'compatibility_map.yaml': {str(source): str(destination / 'config')},
    }
    existing_dir = destination / 'config'
    if existing_dir.exists():
        files = [p for p in existing_dir.iterdir() if p.name != 'backups']
        if any(p.name != 'station.yaml' or not p.is_file() for p in files):
            raise ValueError(tr('Профили Station 3 уже существуют. Чтобы сохранить их, импортируйте данные в новую папку.'))
        for p in files:
            plan.destination_hashes[p.name] = digest(p)
        if files:
            current = read_document(existing_dir / 'station.yaml', {})
            plan.documents['station.yaml'].update(current)
            plan.warnings.append(tr('Текущие настройки Station 3 и выбранные пути к программам сохранены.'))
    for path in source.glob('*.json'):
        plan.sources[str(path)] = digest(path)
    plan.warnings.append(tr('Импортированные модели нужно проверить. Во время переноса службы, агенты и режим видеокарт не запускаются и не меняются.'))
    return plan

def apply_migration(plan):
    config_dir = plan.destination / 'config'
    current = {p.name: digest(p) for p in config_dir.iterdir() if p.is_file()} if config_dir.exists() else {}
    if current != plan.destination_hashes or (config_dir.exists() and any(p.is_dir() and p.name != 'backups' for p in config_dir.iterdir())):
        raise ValueError(tr('Настройки в папке назначения изменились после просмотра. Просмотрите перенос заново.'))
    for source, expected in plan.sources.items():
        if digest(Path(source)) != expected:
            raise ValueError(tr('Исходный файл изменился после просмотра: {path}', path=source))
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = plan.destination / 'backups' / ('migration-' + stamp)
    backup.mkdir(parents=True)
    for i, source in enumerate(plan.sources):
        shutil.copy2(source, backup / f'{i}-{Path(source).name}')
    staging = Path(tempfile.mkdtemp(prefix='.migration-', dir=plan.destination))
    try:
        for name, content in plan.documents.items():
            atomic_write(staging / name, content, backup=False)
        report = dict(plan.summary(), backup=str(backup), completed_utc=stamp)
        atomic_write(staging / 'migration_report.yaml', report)
        prior = backup / 'prior-station-config'
        for path in (config_dir, prior, staging):
            if not path.resolve().is_relative_to(plan.destination.resolve()):
                raise ValueError(tr('Пути переноса выходят за пределы папки назначения'))
        if config_dir.exists():
            os.replace(config_dir, prior)
        try:
            os.replace(staging, config_dir)
        except Exception:
            if prior.exists() and not config_dir.exists():
                os.replace(prior, config_dir)
            raise
    finally:
        if staging.exists():
            # Only files created by this transaction; no recursive deletion.
            for path in staging.iterdir():
                path.unlink()
            staging.rmdir()
    return report
