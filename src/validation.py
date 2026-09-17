"""Validation shared by YAML editors, migrations and runtime configuration loading."""
import math
import re
from .profiles_schema import ModelProfile, GpuHardwareProfile, StationPreset, GpuSelectionPolicy

def validate_registry(filename, rows, *, strict=True, unknown_fields=None):
    """strict=False keeps unrecognised fields (reported via unknown_fields) instead of failing."""
    if not isinstance(rows, list):
        raise ValueError('Registry must be a list')
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip():
            raise ValueError('Every profile requires a nonempty string id')
        if row['id'] in ids:
            raise ValueError('Duplicate profile ID: ' + row['id'])
        ids.add(row['id'])
        cls = {'model_profiles.yaml': ModelProfile, 'hardware_profiles.yaml': GpuHardwareProfile,
               'station_presets.yaml': StationPreset}.get(filename)
        if cls:
            unknown = set(row) - set(cls.__dataclass_fields__)
            if unknown and strict:
                raise ValueError(f'Профиль «{row["id"]}»: неизвестные поля ' + ', '.join(sorted(unknown)))
            if unknown and unknown_fields is not None:
                unknown_fields[row['id']] = sorted(unknown)
            cls.from_dict(row)
        if filename == 'model_profiles.yaml':
            if row.get('gpu_selection_policy', 'all_compute_gpus') not in {p.value for p in GpuSelectionPolicy}:
                raise ValueError('Invalid GPU selection policy')
            if row.get('backend', 'cuda') not in ('cuda', 'cpu', 'vulkan', 'rocm', 'sycl'):
                raise ValueError('Unsupported backend')
            if row.get('status', 'manual') not in ('production', 'stable', 'fallback', 'experimental', 'manual', 'disabled'):
                raise ValueError('Invalid model status')
            for key in ('context', 'batch', 'ubatch', 'min_gpu_count', 'min_total_vram_mib', 'min_free_vram_per_gpu_mib', 'mtp_depth', 'gpu_layers'):
                value = row.get(key, 0)
                if type(value) is not int or value < 0:
                    raise ValueError(key + ' must be a nonnegative integer')
            if not 5 <= row.get('startup_timeout', 240) <= 3600:
                raise ValueError('startup_timeout must be between 5 and 3600 seconds')
            maximum = row.get('max_gpu_count')
            if maximum is not None and (type(maximum) is not int or maximum < row.get('min_gpu_count', 1)):
                raise ValueError('max_gpu_count must be >= min_gpu_count')
            for key in ('qualified', 'vision', 'tool_calling', 'reasoning', 'cpu_offload', 'prefer_p2p', 'require_p2p'):
                if key in row and type(row[key]) is not bool:
                    raise ValueError(key + ' must be a boolean')
        if filename == 'hardware_profiles.yaml':
            if row.get('general_policy', 'all_gpus') not in ('all_gpus', 'all_wddm', 'all_tcc', 'all_compute', 'one_graphics_rest_compute', 'custom', 'unchanged', 'largest_vram', 'best_p2p_clique'):
                raise ValueError('Invalid hardware policy')
            seen = set()
            for rule in row.get('rules', []):
                if rule.get('role', 'auto') not in ('graphics', 'compute', 'mixed', 'excluded', 'auto'):
                    raise ValueError('Invalid GPU role')
                if rule.get('target_driver_mode', 'UNCHANGED') not in ('WDDM', 'TCC', 'UNCHANGED', 'UNSUPPORTED'):
                    raise ValueError('Invalid driver mode')
                uid = rule.get('gpu_stable_id')
                if not isinstance(uid, str) or not uid or uid.lower() in seen:
                    raise ValueError('GPU rules need distinct stable IDs')
                seen.add(uid.lower())
        if filename in ('agent_frontends.yaml', 'services.yaml'):
            args = row.get('launch_arguments', row.get('arguments', []))
            if not isinstance(args, list) or not all(isinstance(a, str) and '\0' not in a for a in args):
                raise ValueError('Arguments must be a list of strings')
        if filename == 'services.yaml':
            if row['id'] == 'llama-swap':
                raise ValueError('llama-swap is reserved for the built-in model backend')
            if row.get('type', 'local') not in ('local', 'remote'):
                raise ValueError('Service type must be local or remote')
            if row.get('restart_policy', 'never') not in ('never', 'on_failure'):
                raise ValueError('Invalid service restart policy')
            if row.get('kind', 'http') not in ('http', 'comfyui'):
                raise ValueError('Unknown service kind')
            if 'monitor_enabled' in row and type(row['monitor_enabled']) is not bool:
                raise ValueError('monitor_enabled must be a boolean')
            from .service_profiles import http_url
            for key in ('health_url', 'url'):
                if row.get(key):
                    http_url(row[key])
            from .secrets_store import SecretStore
            if row.get('token_reference'):
                SecretStore.target(row['token_reference'])
            refs = row.get('environment_references', {})
            if not isinstance(refs, dict):
                raise ValueError('environment_references must be a mapping')
            for key, reference in refs.items():
                if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', key):
                    raise ValueError('Invalid environment variable name')
                SecretStore.target(reference)
        if filename == 'station_presets.yaml':
            for key in ('gpu_profile_id', 'model_profile_id', 'primary_agent_runtime', 'preferred_frontend'):
                if key in row and not isinstance(row[key], str):
                    raise ValueError(key + ' must be a string')
    return rows
