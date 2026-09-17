"""Compatibility based on selected devices, known resources and runtime capabilities."""
from dataclasses import dataclass
from pathlib import Path
from typing import List, Any
from .profiles_schema import CompatibilityStatus as Status
from .hardware_topology import GpuDeviceInfo
from .model_server import resolve_model_file
from .i18n import tr

@dataclass
class CompatibilityResult:
    status: Status
    can_run: bool
    summary: str
    reasons: List[str]
    warnings: List[str]
    assigned_gpus: List[GpuDeviceInfo]
    transport_mode: str = 'Unknown'
    def to_dict(self):
        return dict(status=self.status.value, can_run=self.can_run, summary=self.summary,
            reasons=self.reasons, warnings=self.warnings, assigned_gpus=[g.to_dict() for g in self.assigned_gpus],
            transport_mode=self.transport_mode)

class CompatibilityEvaluator:
    def evaluate(self, model, topology, gpu_profile=None):
        assigned, errors, warnings, unknown = [], [], [], []
        def result():
            status = Status.INCOMPATIBLE if errors else Status.UNKNOWN if unknown else Status.COMPATIBLE_WITH_WARNING if warnings else Status.COMPATIBLE
            reasons = errors or unknown or [tr('Проверенные требования профиля удовлетворены.')]
            return CompatibilityResult(status, not errors and not unknown, ' '.join(reasons[:3]) + (' ' + tr('(и ещё {count})', count=len(reasons) - 3) if len(reasons) > 3 else ''), reasons, warnings, assigned, transport)
        transport = 'None'
        if model.id == 'none':
            return CompatibilityResult(Status.COMPATIBLE, True, tr('Модель не выбрана'), [], [], [])
        if model.status == 'disabled':
            errors.append(tr('Профиль модели отключён.'))
        if model.provider_type in ('openai_compatible', 'ollama'):
            if not model.endpoint:
                errors.append(tr('Endpoint модели не задан.'))
            transport = 'HTTP'
            return result()
        weights = resolve_model_file(model.weights_path)
        mmproj = resolve_model_file(model.mmproj_path)
        if not model.weights_path:
            errors.append(tr('Путь к файлу весов не задан.'))
        elif not topology.is_simulated and not Path(weights).is_file():
            errors.append(tr('Файл весов модели не найден: {path}', path=weights))
        if model.vision and (not model.mmproj_path or (not topology.is_simulated and not Path(mmproj).is_file())):
            errors.append(tr('Для работы с изображениями нужен файл mmproj, но он не найден: {path}', path=str(mmproj or tr('путь не задан'))))
        if model.allowed_hardware_profiles and (not gpu_profile or gpu_profile.id not in model.allowed_hardware_profiles):
            errors.append(tr('Выбранный аппаратный профиль не разрешён для этой модели.'))
        if topology.discovery_error:
            unknown.append(tr('Опрос оборудования не завершён: {error}', error=topology.discovery_error))
        assigned = self._resolve_assigned_gpus(model, topology, gpu_profile)
        if model.gpu_selection_policy == 'explicit_uuid_list':
            wanted = {s.lower() for s in model.explicit_gpu_uuids}
            if not wanted or wanted != {g.uuid.lower() for g in assigned}:
                errors.append(tr('Явно выбранные GPU отсутствуют, исключены или несовместимы с backend.'))
        if len(assigned) < model.min_gpu_count:
            errors.append(tr('Недостаточно доступных GPU: требуется {required}, выбрано {selected}.', required=model.min_gpu_count, selected=len(assigned)))
        if model.max_gpu_count is not None and len(assigned) > model.max_gpu_count:
            errors.append(tr('Превышено разрешённое количество GPU: максимум {maximum}.', maximum=model.max_gpu_count))
        if model.backend == 'cpu' or (not assigned and model.cpu_offload and model.min_gpu_count == 0):
            transport = 'CPU'
            if weights and Path(weights).is_file():
                import psutil
                if Path(weights).stat().st_size > psutil.virtual_memory().available:
                    errors.append(tr('Недостаточно свободной RAM даже для весов модели.'))
            warnings.append(tr('CPU inference: требуется квалификация RAM с учётом KV-кэша и контекста.'))
            return result()
        if any(g.vram_total_mib is None for g in assigned):
            unknown.append(tr('Объём VRAM выбранных GPU неизвестен.'))
        elif sum(g.vram_total_mib for g in assigned) < model.min_total_vram_mib:
            errors.append(tr('Недостаточно видеопамяти: требуется {required} MiB.', required=model.min_total_vram_mib))
        for g in assigned:
            if g.vram_free_mib is None:
                unknown.append(tr('{gpu}: свободная VRAM неизвестна.', gpu=g.name))
            elif g.vram_free_mib < model.min_free_vram_per_gpu_mib:
                errors.append(tr('{gpu}: недостаточно свободной VRAM ({free} MiB).', gpu=g.name, free=g.vram_free_mib))
        if assigned and all(g.vram_free_mib is not None for g in assigned):
            if sum(g.vram_free_mib for g in assigned) < model.min_total_vram_mib and not model.cpu_offload:
                errors.append(tr('Недостаточно суммарной свободной VRAM для профиля.'))
            if not topology.is_simulated and Path(weights).is_file() and not model.cpu_offload:
                weight_mib = Path(weights).stat().st_size / 1048576
                if sum(g.vram_free_mib for g in assigned) < weight_mib:
                    errors.append(tr('Свободной VRAM недостаточно даже для файла весов; KV-кэш требует дополнительной памяти.'))
                if model.min_total_vram_mib < weight_mib:
                    warnings.append(tr('Профиль не задаёт полный бюджет VRAM. Фактическая загрузка и контекст требуют smoke-проверки.'))
        split = model.tensor_split_policy
        if split not in ('auto', 'none', ''):
            try:
                ratios = [float(x.strip()) for x in split.split(',')]
                if len(ratios) != len(assigned) or any(x <= 0 or not __import__('math').isfinite(x) for x in ratios):
                    raise ValueError()
                if not model.cpu_offload and model.min_total_vram_mib:
                    for g, ratio in zip(assigned, ratios):
                        if g.vram_free_mib is not None and model.min_total_vram_mib * ratio / sum(ratios) > g.vram_free_mib:
                            errors.append(tr('Tensor split выделяет {gpu} больше памяти, чем доступно.', gpu=g.name))
            except (ValueError, ZeroDivisionError):
                errors.append(tr('Tensor split должен содержать положительный вес для каждой выбранной GPU.'))
        p2p = len(assigned) > 1 and all(topology.is_p2p_active_between(a.index, b.index)
            for i, a in enumerate(assigned) for b in assigned[i+1:])
        transport = tr('CUDA Direct P2P (проверена доступность)') if p2p else 'PCIe / Host Fallback Transport' if len(assigned) > 1 else 'Single device'
        if (model.require_p2p or (gpu_profile and gpu_profile.require_p2p)) and not p2p:
            errors.append(tr('Профиль строго требует аппаратный CUDA Direct P2P; доступность не подтверждена.'))
        elif len(assigned) > 1 and not p2p:
            warnings.append(tr('CUDA P2P не подтверждён. Допускается fallback transport, если его поддерживает runtime.'))
        nvlink = len(assigned) > 1 and all(b.index in a.nvlink_peers and a.index in b.nvlink_peers
            for i, a in enumerate(assigned) for b in assigned[i+1:])
        if gpu_profile and gpu_profile.require_nvlink and not nvlink:
            errors.append(tr('Аппаратный профиль строго требует NVLink между выбранными GPU; наличие не подтверждено.'))
        if any(g.display_active is True for g in assigned):
            warnings.append(tr('На выбранной GPU активен рабочий стол Windows; учитывайте графическую нагрузку.'))
        if any(g.display_active is None for g in assigned):
            warnings.append(tr('Состояние подключённых дисплеев неизвестно.'))
        if model.status in ('manual', 'experimental') or not model.qualified:
            warnings.append(tr('Модель ещё не квалифицирована smoke-тестом Station.'))
        return result()

    def _resolve_assigned_gpus(self, model, topology, gpu_profile):
        if model.backend == 'cpu':
            return []
        vendors = {'cuda': {'NVIDIA'}, 'rocm': {'AMD'}, 'sycl': {'Intel'}, 'vulkan': {'NVIDIA', 'AMD', 'Intel', 'Other'}}
        candidates = [d for d in topology.devices if d.vendor in vendors.get(model.backend, set())]
        excluded, compute, graphics = set(), set(), set()
        if gpu_profile:
            excluded.update(x.lower() for x in gpu_profile.excluded_devices)
            if gpu_profile.included_devices:
                included = {x.lower() for x in gpu_profile.included_devices}
                candidates = [g for g in candidates if g.uuid.lower() in included]
            for r in gpu_profile.rules:
                rid = r.gpu_stable_id.lower()
                if r.role == 'excluded' or not r.allow_compute:
                    excluded.add(rid)
                elif r.role == 'graphics':
                    graphics.add(rid)
                elif r.role == 'compute':
                    compute.add(rid)
            if gpu_profile.general_policy == 'one_graphics_rest_compute':
                displays = [g for g in topology.devices if g.display_active is True]
                chosen = displays or topology.devices[:1]
                graphics.update(g.uuid.lower() for g in chosen)
            if gpu_profile.general_policy == 'largest_vram':
                candidates = sorted(candidates, key=lambda g: g.vram_total_mib or 0, reverse=True)[:1]
        candidates = [d for d in candidates if d.uuid.lower() not in excluded]
        policy = model.gpu_selection_policy
        if policy == 'explicit_uuid_list':
            lookup = {g.uuid.lower(): g for g in candidates}
            return [lookup[u.lower()] for u in model.explicit_gpu_uuids if u.lower() in lookup]
        if policy == 'largest_vram_gpu':
            return sorted(candidates, key=lambda d: d.vram_total_mib or 0, reverse=True)[:1]
        if policy == 'exclude_display_gpu':
            return [d for d in candidates if d.display_active is False and d.uuid.lower() not in graphics]
        if policy in ('all_compute_gpus', 'dedicated_compute_only', 'auto'):
            dedicated = [d for d in candidates if d.uuid.lower() not in graphics and
                (d.uuid.lower() in compute or d.is_tcc or d.display_active is False)]
            if policy == 'dedicated_compute_only':
                return dedicated
            if len(dedicated) >= model.min_gpu_count:
                candidates = dedicated
            else:
                candidates = [d for d in candidates if d.uuid.lower() not in graphics]
        if policy == 'best_p2p_clique' or (gpu_profile and gpu_profile.general_policy == 'best_p2p_clique'):
            groups = [[d for d in candidates if d.index in group] for group in topology.cuda_p2p_cliques]
            groups = [g for g in groups if len(g) >= max(2, model.min_gpu_count)]
            return max(groups, key=lambda g: sum(d.vram_free_mib or 0 for d in g), default=[])
        return candidates

compatibility_evaluator = CompatibilityEvaluator()
