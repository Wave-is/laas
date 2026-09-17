"""Model library: find .gguf files, build profiles without YAML, move the models folder, quick chat.

Nothing here touches the Tk thread. Long operations accept progress callbacks and a cancel event.
Model files are only ever read, copied or (after explicit confirmation in the UI) deleted by
``delete_originals``; profile deletion never touches files.
"""
import hashlib
import json
import os
import re
import shutil
import threading
import time
import urllib.request
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from . import gguf
from .i18n import tr
from .profiles_schema import ModelProfile

MIB = 1024 * 1024
DEFAULT_CONTEXT_LIMIT = 131072
STATUSES = ('production', 'stable', 'fallback', 'experimental', 'manual', 'disabled')
HASH_LIMIT = 64 * MIB
COPY_CHUNK = 8 * MIB


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- paths

def norm(path):
    """Comparable absolute path: case-insensitive on Windows, forward slashes."""
    return os.path.normcase(os.path.abspath(str(path))).replace('\\', '/').rstrip('/')


def is_inside(path, folder):
    if not path or not folder:
        return False
    p, f = norm(path), norm(folder)
    return p == f or p.startswith(f + '/')


def resolve(value, models_dir):
    if not value:
        return None
    path = Path(value)
    if path.is_absolute() or not models_dir:
        return path
    return Path(models_dir) / path


def store_path(path, models_dir):
    """Path to store in a profile: relative (forward slashes) inside the models folder, else absolute."""
    if not path:
        return None
    path = Path(path)
    if models_dir and is_inside(path, models_dir):
        try:
            return Path(os.path.relpath(os.path.abspath(path), os.path.abspath(models_dir))).as_posix()
        except ValueError:
            pass
    return str(path)


def is_mmproj_name(name):
    return 'mmproj' in Path(name).name.lower()


def is_secondary_split(name):
    match = gguf.SPLIT_PATTERN.search(Path(name).name)
    return bool(match) and int(match.group(1)) != 1


# ---------------------------------------------------------------- names and ids

def slugify(text, existing=()):
    base = re.sub(r'[^a-z0-9._-]+', '-', str(text).lower()).strip('-._')
    base = re.sub(r'-{2,}', '-', base)[:80] or 'model'
    taken = set(existing)
    candidate, number = base, 2
    while candidate in taken:
        candidate = f'{base}-{number}'
        number += 1
    return candidate


def display_name(path, info=None):
    stem = gguf.SPLIT_PATTERN.sub('', Path(path).name)
    stem = re.sub(r'\.gguf$', '', stem, flags=re.I)
    return stem


def _family_tokens(name):
    stem = re.sub(r'\.gguf$', '', Path(name).name, flags=re.I).lower()
    stem = gguf.SPLIT_PATTERN.sub('', stem)
    stem = re.sub(r'mmproj|model|(?<![a-z0-9])(ud-)?(i?q\d(_[a-z0-9]+)*|f16|f32|bf16)(?![a-z0-9])', ' ', stem)
    return [t for t in re.split(r'[^a-z0-9.]+', stem) if t]


def find_mmproj(weights_path):
    """Projector files (name contains 'mmproj') next to the weights, best family match first.

    A candidate must share at least the first name token (for example 'qwen3.8') with the model;
    when only one projector is in the folder and nothing matches, none is suggested.
    """
    weights_path = Path(weights_path)
    try:
        candidates = [p for p in weights_path.parent.iterdir()
                      if p.is_file() and p.suffix.lower() == '.gguf' and is_mmproj_name(p.name) and p != weights_path]
    except OSError:
        return []
    model = _family_tokens(weights_path.name)
    scored = []
    for candidate in candidates:
        tokens = _family_tokens(candidate.name)
        common = 0
        for a, b in zip(model, tokens):
            if a != b:
                break
            common += 1
        overlap = len(set(model) & set(tokens))
        if common or (model and tokens and model[0] in tokens):
            scored.append((common, overlap, candidate))
    scored.sort(key=lambda row: (-row[0], -row[1], row[2].name))
    return [row[2] for row in scored]


# ---------------------------------------------------------------- scan

@dataclass
class FoundModel:
    path: Path
    size_bytes: int
    parts: int = 1


def profile_paths(profiles, models_dir):
    result = set()
    for profile in profiles:
        for value in (profile.weights_path, profile.mmproj_path):
            path = resolve(value, models_dir)
            if path:
                result.add(norm(path))
    return result


def scan_models(folder, profiles, models_dir=None, cancel=None):
    """.gguf files under folder that no profile uses (projectors and later split parts skipped)."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    known = profile_paths(profiles, models_dir if models_dir is not None else folder)
    found = []
    for root, dirs, files in os.walk(folder):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
        for name in sorted(files):
            if not name.lower().endswith('.gguf') or is_mmproj_name(name) or is_secondary_split(name):
                continue
            path = Path(root) / name
            if norm(path) in known:
                continue
            parts = gguf.split_parts(path)
            found.append(FoundModel(path, gguf.weights_size(path), len(parts)))
    return found


# ---------------------------------------------------------------- profiles

GPU_ALL, GPU_ONE = 'all', 'one'


def gpu_choice(profile):
    """UI choice for a profile: 'all', 'one' or a minimum count as a string."""
    if profile.gpu_selection_policy == 'largest_vram_gpu' or profile.max_gpu_count == 1:
        return GPU_ONE
    if profile.min_gpu_count and profile.min_gpu_count > 1:
        return str(profile.min_gpu_count)
    return GPU_ALL


def apply_gpu_choice(profile, choice):
    if choice == GPU_ONE:
        profile.gpu_selection_policy = 'largest_vram_gpu'
        profile.min_gpu_count, profile.max_gpu_count, profile.split_mode = 1, 1, 'none'
    else:
        if profile.gpu_selection_policy in ('largest_vram_gpu', ''):
            profile.gpu_selection_policy = 'all_compute_gpus'
        profile.min_gpu_count = int(choice) if str(choice).isdigit() else 1
        profile.max_gpu_count = None
        if profile.split_mode == 'none':
            profile.split_mode = 'layer'
    return profile


def default_context(info):
    trained = info.context_length if info else None
    return min(trained, DEFAULT_CONTEXT_LIMIT) if trained else 32768


def build_profile(*, id, name, weights_path, models_dir=None, mmproj_path=None, context=32768, kv_type='f16',
                  gpu=GPU_ALL, vision=False, status='manual', quant='', estimate=None, base=None):
    """New ModelProfile (or an edited copy of base) from dialog fields."""
    profile = deepcopy(base) if base else ModelProfile(id=id, name=name, weights_path='', split_mode='layer')
    before = profile.to_dict() if base else None
    profile.name = name.strip()
    def stored(value, original):
        # An unchanged file keeps the spelling the profile already had (absolute or relative).
        if value and original and norm(resolve(original, models_dir)) == norm(value):
            return original
        return store_path(value, models_dir) if value else None
    profile.weights_path = stored(weights_path, base.weights_path if base else None) or ''
    profile.mmproj_path = stored(mmproj_path, base.mmproj_path if base else None)
    profile.context = int(context)
    profile.kv_type = kv_type
    profile.vision = bool(vision)
    profile.status = status
    profile.quant = quant or profile.quant
    modalities = [m for m in (profile.modalities or ['text']) if m != 'image']
    profile.modalities = modalities + ['image'] if vision else modalities
    apply_gpu_choice(profile, gpu)
    # VRAM requirements follow the estimate for new profiles and when the load-relevant fields change.
    relevant = ('weights_path', 'mmproj_path', 'context', 'kv_type', 'vision', 'min_gpu_count', 'gpu_selection_policy')
    if estimate is not None and estimate.complete and (not base or any(before[k] != getattr(profile, k) for k in relevant)):
        need = int(estimate.total_mib)
        profile.min_total_vram_mib = need
        count = max(1, profile.min_gpu_count)
        profile.min_free_vram_per_gpu_mib = min(8192, need // count // 2)
    if not base:
        profile.startup_key = profile.id
        profile.qualified = False
    elif before != profile.to_dict():
        profile.qualified = False
    return profile


def validate_profile(profile, profiles):
    """Validate the whole registry with profile added or replaced; returns nothing, raises ValueError."""
    from .validation import validate_registry
    if not profile.name:
        raise ValueError(tr('Укажите название модели'))
    if not re.fullmatch(r'[A-Za-z0-9._-]{1,120}', profile.id or ''):
        raise ValueError(tr('id может содержать только латинские буквы, цифры, точку, дефис и подчёркивание'))
    if profile.context < 512:
        raise ValueError(tr('Контекст должен быть не меньше 512 токенов'))
    if profile.kv_type not in gguf.KV_BYTES:
        raise ValueError(tr('Неизвестный тип KV-кэша: {kind}', kind=profile.kv_type))
    if not profile.weights_path:
        raise ValueError(tr('Выберите файл модели .gguf'))
    if profile.vision and not profile.mmproj_path:
        raise ValueError(tr('Для изображений нужен файл mmproj'))
    rows = [p.to_dict() for p in profiles if p.id != profile.id] + [profile.to_dict()]
    validate_registry('model_profiles.yaml', rows)


def delete_model_profile(storage, model_id):
    """Remove a profile from the registry. Model files are never touched."""
    with storage._lock:
        if model_id not in storage.model_profiles:
            return False
        old = storage.model_profiles.pop(model_id)
        try:
            storage._save('model_profiles')
        except Exception:
            storage.model_profiles[model_id] = old
            raise
        storage.extras.get('model_profiles', {}).pop(model_id, None)
        return True


def profile_in_use(model_id, config_values, presets, ready_ids=()):
    """Reasons why a profile must not be deleted now (empty when free)."""
    reasons = []
    if model_id == 'none':
        reasons.append(tr('Встроенный профиль «Без модели» удалить нельзя.'))
    if model_id in ready_ids:
        reasons.append(tr('Модель сейчас загружена на сервере моделей.'))
    if model_id in (config_values.get('active_model_profile'), config_values.get('selected_model_profile')):
        reasons.append(tr('Профиль выбран как активная модель.'))
    for preset in presets:
        if model_id in (getattr(preset, 'model_profile_id', None), getattr(preset, 'fallback_model_id', None)):
            reasons.append(tr('Профиль используется режимом «{preset}».', preset=preset.name))
    return reasons


def assigned_devices(profile, topology, gpu_profile=None):
    """GPUs the model would run on; all NVIDIA GPUs when the evaluator cannot tell."""
    if topology is None:
        return []
    try:
        from .compatibility import compatibility_evaluator
        devices = compatibility_evaluator.evaluate(profile, topology, gpu_profile).assigned_gpus
    except Exception:
        devices = []
    if devices:
        return devices
    nvidia = [d for d in topology.devices if d.vendor == 'NVIDIA']
    if gpu_choice(profile) == GPU_ONE:
        return sorted(nvidia, key=lambda d: d.vram_total_mib or 0, reverse=True)[:1]
    return nvidia


def estimate_for_profile(profile, models_dir, topology, gpu_profile=None, context=None, kv_type=None):
    """(GgufInfo or None, VramEstimate or None, FitResult or None) for a saved or edited profile. Does file IO."""
    weights = resolve(profile.weights_path, models_dir)
    if not weights or not weights.is_file():
        return None, None, None
    info = gguf.read_gguf_cached(str(weights))
    mmproj = resolve(profile.mmproj_path, models_dir) if profile.vision else None
    mmproj_bytes = mmproj.stat().st_size if mmproj and mmproj.is_file() else 0
    devices = assigned_devices(profile, topology, gpu_profile)
    estimate = gguf.estimate_vram(gguf.weights_size(weights), info, context or profile.context,
                                  kv_type or profile.kv_type, mmproj_bytes, max(1, len(devices)))
    return info, estimate, gguf.fit_verdict(estimate, devices)


# ---------------------------------------------------------------- moving the models folder

@dataclass
class MovePlan:
    source: Path
    destination: Path
    files: List[Path]                      # relative to source
    total_bytes: int
    free_bytes: Optional[int]
    profiles: List[ModelProfile] = field(default_factory=list)  # updated copies
    problems: List[str] = field(default_factory=list)

    @property
    def ok(self):
        return not self.problems


def free_space(path):
    path = Path(path)
    for candidate in [path, *path.parents]:
        if candidate.exists():
            try:
                return shutil.disk_usage(candidate).free
            except OSError:
                return None
    return None


def running_paths_in(rows, folder):
    """Names of running models whose command line references a file inside folder."""
    prefix = norm(folder) + '/'
    busy = []
    for row in rows or []:
        command = str(row.get('cmd') or '').replace('\\', '/')
        if os.name == 'nt':
            command = command.lower()
        if prefix in command:
            busy.append(row.get('name') or row.get('model') or '?')
    return busy


def rewrite_profiles(profiles, source, destination, models_dir):
    """Updated copies of profiles whose files are inside source.

    Relative paths stay relative when the models folder itself moves (they follow models_dir);
    absolute paths are rewritten under destination with the same separator style.
    """
    moving_models_dir = bool(models_dir) and norm(models_dir) == norm(source)
    changed = []
    for profile in profiles:
        updated = deepcopy(profile)
        touched = False
        for key in ('weights_path', 'mmproj_path'):
            value = getattr(profile, key)
            if not value:
                continue
            relative = not Path(value).is_absolute()
            target = resolve(value, models_dir)
            if not target or not is_inside(target, source):
                continue
            if relative and moving_models_dir:
                continue
            rest = os.path.relpath(os.path.abspath(target), os.path.abspath(source))
            new = Path(destination) / rest
            text = str(new)
            if '/' in value and '\\' not in value:
                text = new.as_posix()
            setattr(updated, key, text)
            touched = True
        if touched:
            changed.append(updated)
    return changed


def plan_move(source, destination, profiles, models_dir, running_rows=()):
    source, destination = Path(source), Path(destination)
    problems = []
    files, total = [], 0
    if not source.is_dir():
        problems.append(tr('Папка моделей не найдена: {path}', path=source))
    if norm(source) == norm(destination):
        problems.append(tr('Новая папка совпадает с текущей.'))
    elif is_inside(destination, source):
        problems.append(tr('Новая папка не может находиться внутри текущей.'))
    elif is_inside(source, destination):
        problems.append(tr('Текущая папка не может находиться внутри новой.'))
    busy = running_paths_in(running_rows, source)
    if busy:
        problems.append(tr('Сервер моделей сейчас использует файлы из этой папки: {models}. Перенос возможен после выгрузки модели.',
                           models=', '.join(busy)))
    if source.is_dir() and not problems:
        for root, dirs, names in os.walk(source):
            dirs.sort()
            for name in sorted(names):
                path = Path(root) / name
                relative = path.relative_to(source)
                files.append(relative)
                total += path.stat().st_size
                target = destination / relative
                if target.exists():
                    problems.append(tr('В новой папке уже есть файл: {path}', path=target))
    free = free_space(destination)
    if free is not None and total > free:
        problems.append(tr('Недостаточно места: нужно {need} GiB, свободно {free} GiB.',
                           need=f'{total / 1024 ** 3:.1f}', free=f'{free / 1024 ** 3:.1f}'))
    return MovePlan(source, destination, files, total, free,
                    rewrite_profiles(profiles, source, destination, models_dir), problems[:20])


def _sha256(path, cancel=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(COPY_CHUNK), b''):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            digest.update(chunk)
    return digest.hexdigest()


def copy_files(plan, progress=None, cancel=None, hash_limit=HASH_LIMIT):
    """Copy every planned file and verify it. On failure or cancel the copies made are removed.

    progress(done_bytes, total_bytes, relative_path) is called from this thread.
    """
    created = []
    done = 0
    last = 0.0
    try:
        for relative in plan.files:
            source = plan.source / relative
            target = plan.destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                raise FileExistsError(tr('В новой папке уже есть файл: {path}', path=target))
            created.append(target)
            with open(source, 'rb') as reader, open(target, 'xb') as writer:
                for chunk in iter(lambda: reader.read(COPY_CHUNK), b''):
                    if cancel is not None and cancel.is_set():
                        raise Cancelled()
                    writer.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if progress and now - last > 0.2:
                        last = now
                        progress(done, plan.total_bytes, str(relative))
            shutil.copystat(source, target)
            if source.stat().st_size != target.stat().st_size:
                raise IOError(tr('Размер копии не совпадает: {path}', path=target))
            if source.stat().st_size <= hash_limit and _sha256(source, cancel) != _sha256(target, cancel):
                raise IOError(tr('Контрольная сумма копии не совпадает: {path}', path=target))
        if progress:
            progress(done, plan.total_bytes, '')
        return created
    except BaseException:
        for path in reversed(created):
            try:
                path.unlink()
            except OSError:
                pass
        _remove_empty_dirs(plan.destination, keep_root=True)
        raise


def apply_move(plan, storage, config):
    """Save updated profiles and the new models folder after a verified copy."""
    for profile in plan.profiles:
        storage.save_model_profile(profile)
    if config.get('models_dir') and norm(config.get('models_dir')) == norm(plan.source):
        config.update({'models_dir': str(plan.destination)})
    elif not config.get('models_dir'):
        config.update({'models_dir': str(plan.destination)})


def delete_originals(plan):
    """Delete the copied originals (only files of the plan whose copy exists with the same size)."""
    removed = 0
    for relative in plan.files:
        source, target = plan.source / relative, plan.destination / relative
        try:
            if target.is_file() and source.is_file() and target.stat().st_size == source.stat().st_size:
                source.unlink()
                removed += 1
        except OSError:
            pass
    _remove_empty_dirs(plan.source, keep_root=True)
    return removed


def _remove_empty_dirs(folder, keep_root=True):
    folder = Path(folder)
    if not folder.is_dir():
        return
    for root, dirs, files in os.walk(folder, topdown=False):
        if keep_root and Path(root) == folder:
            continue
        try:
            os.rmdir(root)
        except OSError:
            pass


# ---------------------------------------------------------------- quick chat

@dataclass
class ChatStats:
    first_token_s: Optional[float] = None
    total_s: float = 0.0
    tokens: int = 0
    tokens_per_s: Optional[float] = None
    usage: dict = field(default_factory=dict)


def ready_models(base_url, opener=None, timeout=3):
    """Models in state 'ready' from GET /running of llama-swap: [(model id, display name)]."""
    opener = opener or urllib.request.urlopen
    with opener(urllib.request.Request(base_url.rstrip('/') + '/running'), timeout=timeout) as response:
        rows = json.loads(response.read() or b'{}').get('running', [])
    return [(r['model'], r.get('name') or r['model']) for r in rows if r.get('state') == 'ready' and r.get('model')]


def stream_chat(api_url, model, messages, on_token, cancel=None, opener=None, timeout=600, max_tokens=None):
    """POST an OpenAI chat completion with stream=true and parse server-sent events.

    on_token(text) is called for each content or reasoning delta. Returns ChatStats.
    """
    opener = opener or urllib.request.urlopen
    payload = {'model': model, 'messages': messages, 'stream': True, 'stream_options': {'include_usage': True}}
    if max_tokens:
        payload['max_tokens'] = max_tokens
    request = urllib.request.Request(api_url.rstrip('/') + '/chat/completions', data=json.dumps(payload).encode('utf-8'),
                                     headers={'Content-Type': 'application/json', 'Accept': 'text/event-stream'}, method='POST')
    stats = ChatStats()
    started = time.monotonic()
    first = None
    with opener(request, timeout=timeout) as response:
        for raw in response:
            if cancel is not None and cancel.is_set():
                break
            line = raw.decode('utf-8', errors='replace').strip()
            if not line.startswith('data:'):
                continue
            data = line[5:].strip()
            if data == '[DONE]':
                break
            try:
                event = json.loads(data)
            except ValueError:
                continue
            if event.get('error'):
                raise RuntimeError(str(event['error'].get('message') if isinstance(event['error'], dict) else event['error']))
            if event.get('usage'):
                stats.usage = event['usage']
            for choice in event.get('choices') or []:
                delta = choice.get('delta') or {}
                text = (delta.get('reasoning_content') or '') + (delta.get('content') or '')
                if text:
                    if first is None:
                        first = time.monotonic()
                        stats.first_token_s = first - started
                    stats.tokens += 1
                    on_token(text)
            timings = event.get('timings')
            if isinstance(timings, dict) and timings.get('predicted_per_second'):
                stats.tokens_per_s = float(timings['predicted_per_second'])
    stats.total_s = time.monotonic() - started
    completion = stats.usage.get('completion_tokens') if stats.usage else None
    if completion:
        stats.tokens = int(completion)
    if stats.tokens_per_s is None and first is not None and stats.tokens > 1:
        generation = time.monotonic() - first
        stats.tokens_per_s = (stats.tokens - 1) / generation if generation > 0 else None
    return stats
