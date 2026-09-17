"""Compile Station model profiles into a private llama-swap configuration."""
from pathlib import Path
import os
import shlex
import subprocess
from .storage import atomic_write, read_document
from .paths import data_dir
from .compatibility import compatibility_evaluator
from .model_server import resolve_model_file
from .i18n import tr

def launch_signature(model, hardware_profile, executable, topology):
    import hashlib
    import json
    fields = ('id', 'weights_path', 'mmproj_path', 'context', 'mtp_depth', 'batch', 'ubatch',
        'kv_type', 'split_mode', 'tensor_split_policy', 'gpu_selection_policy', 'explicit_gpu_uuids',
        'backend', 'gpu_layers', 'vision', 'cpu_offload', 'endpoint', 'provider_type')
    values = {key: getattr(model, key) for key in fields}
    values['hardware'] = hardware_profile.to_dict() if hardware_profile else None
    values['devices'] = [(d.uuid, d.driver_mode) for d in topology.devices]
    for key, value in [('weights', resolve_model_file(model.weights_path)), ('projector', resolve_model_file(model.mmproj_path)), ('server', executable)]:
        path = Path(value) if value else None
        stat = path.stat() if path and path.is_file() else None
        values[key] = [str(path), stat.st_size, stat.st_mtime_ns] if stat else str(path)
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode('utf-8')).hexdigest()

def tensor_split(model, devices):
    if model.tensor_split_policy not in ('auto', '', 'none'):
        values = [float(v) for v in model.tensor_split_policy.split(',')]
        if len(values) != len(devices) or any(v <= 0 for v in values):
            raise ValueError(tr('Tensor split не совпадает с числом выбранных GPU'))
        return ','.join(str(v) for v in values)
    if not devices:
        return ''
    capacities = [max(1, (d.vram_free_mib if d.vram_free_mib is not None else d.vram_total_mib) or 1) for d in devices]
    minimum = min(capacities)
    return ','.join(f'{v/minimum:.4f}' for v in capacities)

def build_model_entry(model, devices, executable):
    if not executable or not Path(executable).is_file():
        raise ValueError(tr('Не найден llama-server.exe (llama.cpp). Укажите папку движка в «Настройки → Папки и сервер моделей».'))
    weights, mmproj = resolve_model_file(model.weights_path), resolve_model_file(model.mmproj_path)
    if not Path(weights).is_file():
        raise ValueError(tr('Файл весов модели «{name}» не найден: {path}', name=model.name, path=weights))
    args = [executable, '-m', weights, '-c', str(model.context), '-ngl',
        '0' if model.backend == 'cpu' else str(model.gpu_layers), '--parallel', '1', '--host', '127.0.0.1', '--port', '${PORT}',
        '-b', str(model.batch), '-ub', str(model.ubatch), '-fa', 'on']
    if model.backend == 'cpu':
        args += ['--device', 'none', '--no-op-offload', '--no-kv-offload']
        if model.vision:
            args += ['--no-mmproj-offload']
    if model.vision:
        if not mmproj or not Path(mmproj).is_file():
            raise ValueError(tr('Файл mmproj модели «{name}» не найден: {path}', name=model.name, path=mmproj))
        args += ['--mmproj', mmproj]
    if len(devices) > 1:
        args += ['--split-mode', model.split_mode, '--tensor-split', tensor_split(model, devices)]
    if model.mtp_depth:
        args += ['--spec-type', 'draft-mtp', '--spec-draft-n-max', str(model.mtp_depth)]
    if model.kv_type:
        args += ['-ctk', model.kv_type, '-ctv', model.kv_type]
    # llama-swap parses argv, never invokes a shell. CUDA UUIDs avoid driver index reordering.
    command = subprocess.list2cmdline(args) if os.name == 'nt' else shlex.join(args)
    entry = {'cmd': command, 'proxy': 'http://127.0.0.1:${PORT}', 'name': model.name}
    if model.backend == 'cuda':
        entry['env'] = ['CUDA_VISIBLE_DEVICES=' + ','.join(d.uuid for d in devices)]
    elif model.backend == 'cpu':
        entry['env'] = ['CUDA_VISIBLE_DEVICES=']
    return entry

def compile_swap(profiles, topology, hardware_profile, executable, path=None):
    target = Path(path or data_dir() / 'backend/llama-swap.yaml')
    models, skipped = {}, {}
    for model in profiles:
        if model.id == 'none' or model.provider_type != 'llama_cpp' or model.status == 'disabled':
            continue
        evaluation = compatibility_evaluator.evaluate(model, topology, hardware_profile)
        if evaluation.can_run:
            models[model.backend_model_id] = build_model_entry(model, evaluation.assigned_gpus, executable)
        else:
            skipped[model.id] = evaluation.summary
    if not models:
        raise ValueError(tr('Нет моделей, которые можно запустить на текущем оборудовании: {reasons}', reasons='; '.join(f'{k}: {v}' for k, v in skipped.items())))
    document = {'healthCheckTimeout': max(p.startup_timeout for p in profiles), 'logLevel': 'warn',
        'captureBuffer': 0, 'models': models}
    old = read_document(target, None)
    changed = old != document
    atomic_write(target, document)
    return target, changed, skipped
