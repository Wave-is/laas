"""Independent hardware and model operations with verified state transitions."""
from datetime import datetime, timezone
import threading
from pathlib import Path
from .config import config
from .profile_storage import profile_storage
from .profiles_schema import GpuHardwareProfile, StationPreset
from .hardware_topology import topology_engine
from .hardware import hardware
from .compatibility import compatibility_evaluator
from .services.gpu_mode_client import gpu_service_client
from .process_manager import pm
from .supervisor import supervisor
from .engines.llama_swap import LlamaSwapEngine
from .model_backend import compile_swap, launch_signature

class ProfileExecutionManager:
    def __init__(self):
        self._lock = threading.RLock()
        self.state = 'IDLE'
        self.last_switch_log = []
        self.engine = LlamaSwapEngine()
        self.rollback_plan = []
    def log(self, message):
        self.last_switch_log.append(datetime.now(timezone.utc).isoformat() + ' ' + message)
        self.last_switch_log = self.last_switch_log[-200:]
    def get_current_topology(self):
        return topology_engine.discover_live()
    def detect_hardware_mode(self, topology=None):
        top = topology or self.get_current_topology()
        devices = [d for d in top.devices if d.vendor == 'NVIDIA']
        if not top.devices:
            return 'CPU_ONLY'
        if not devices:
            return 'GRAPHICS'
        if all(d.driver_mode == 'TCC' for d in devices):
            return 'AI_MAX'
        if all(d.driver_mode == 'WDDM' for d in devices):
            return 'GRAPHICS'
        if all(d.driver_mode in ('WDDM', 'TCC') for d in devices):
            return 'HYBRID'
        return 'UNKNOWN'
    def get_active_gpu_profile(self):
        selected = profile_storage.get_gpu_profile(config.get('active_gpu_profile'))
        return selected or profile_storage.get_gpu_profile('gpu-unchanged')
    def get_active_model_profile(self):
        return profile_storage.get_model_profile(config.get('active_model_profile', 'none')) or profile_storage.get_model_profile('none')
    def get_active_preset(self):
        return next((p for p in profile_storage.station_presets.values() if
            p.gpu_profile_id == config.get('active_gpu_profile') and p.model_profile_id == config.get('active_model_profile') and
            p.primary_agent_runtime == config.get('primary_agent_runtime') and p.preferred_frontend == config.get('preferred_frontend')), None)
    def get_nvlink_status(self):
        top = self.get_current_topology()
        return {'physical_bridge': 'LINKED' if top.physical_nvlink_cliques else 'DISCONNECTED' if top.nvlink_verified else 'UNKNOWN',
            'cuda_p2p_status': 'AVAILABLE' if top.cuda_p2p_cliques else 'UNAVAILABLE' if top.p2p_verified else 'UNKNOWN',
            'p2p_active': bool(top.cuda_p2p_cliques), 'traffic': 'NOT MEASURED'}
    def preview_gpu_plan(self, profile_id, topology=None):
        profile = profile_storage.get_gpu_profile(profile_id)
        if not profile:
            raise ValueError('Hardware profile not found')
        top = topology or self.get_current_topology()
        if top.discovery_error:
            raise ValueError('Hardware discovery incomplete: ' + top.discovery_error)
        plan, warnings, changes = [], [], []
        included = {s.lower() for s in profile.included_devices}
        excluded = {s.lower() for s in profile.excluded_devices}
        rules = {r.gpu_stable_id.lower(): r for r in profile.rules}
        missing = set(rules) - {d.uuid.lower() for d in top.devices}
        if missing:
            raise ValueError('Profile references missing GPU UUIDs: ' + ', '.join(sorted(missing)))
        displays = [d for d in top.devices if d.display_active is True]
        graphics = {d.uuid for d in displays or top.devices[:1]}
        for d in top.devices:
            if d.uuid.lower() in excluded or (included and d.uuid.lower() not in included):
                continue
            rule = rules.get(d.uuid.lower())
            if rule and rule.role == 'excluded':
                continue
            target = rule.target_driver_mode if rule else 'UNCHANGED'
            if not rule:
                if profile.general_policy == 'all_wddm': target = 'WDDM'
                elif profile.general_policy in ('all_tcc', 'all_compute'): target = 'TCC'
                elif profile.general_policy == 'one_graphics_rest_compute': target = 'WDDM' if d.uuid in graphics else 'TCC'
            if target in ('UNCHANGED', 'UNSUPPORTED'):
                continue
            if d.vendor != 'NVIDIA':
                if rule and target != d.driver_mode:
                    raise ValueError(d.name + ': driver mode switching is unsupported')
                continue
            if d.driver_mode == target and d.pending_driver_mode in (target, 'UNKNOWN'):
                continue
            if target == 'TCC' and d.tcc_supported is False:
                if rule:
                    raise ValueError(d.name + ': режим TCC не поддерживается')
                warnings.append(d.name + ': TCC не поддерживается, режим оставлен без изменений')
                continue
            if target == 'TCC' and d.tcc_supported is None:
                warnings.append(d.name + ': поддержка TCC неизвестна; результат должен подтвердить драйвер')
            if target == 'TCC' and d.display_active is not False:
                raise ValueError(d.name + ': переключение в TCC заблокировано — дисплей активен или его состояние неизвестно. Отключите мониторы от этой карты и подключите их к GPU, остающейся в WDDM.')
            plan.append({'gpu_stable_id': d.uuid, 'target_mode': target})
            changes.append({'gpu_stable_id': d.uuid, 'name': d.name, 'index': d.index,
                'current_mode': d.driver_mode, 'pending_mode': d.pending_driver_mode, 'target_mode': target})
        return {'Plan': plan, 'Warnings': warnings, 'Profile': profile.id,
            'ProfileName': profile.name, 'Changes': changes}
    def apply_gpu_profile_only(self, gpu_profile_id, suppress_model_handling=False, expected_plan=None):
        with self._lock:
            try:
                top = self.get_current_topology()
                preview = self.preview_gpu_plan(gpu_profile_id, top)
                plan = preview['Plan']
                # A reviewed (including empty) plan never authorizes newly discovered changes.
                if expected_plan is not None and sorted(plan, key=lambda p: p['gpu_stable_id']) != sorted(expected_plan, key=lambda p: p['gpu_stable_id']):
                    return {'Success': False, 'Message': 'Состояние GPU изменилось после проверки. Выберите профиль ещё раз.'}
                if not plan:
                    config.set('active_gpu_profile', gpu_profile_id)
                    return {'Success': True, 'Message': 'No driver changes required', **preview}
                if any(supervisor.status(k)['running'] for k in supervisor.records if k.startswith(('agent:', 'frontend:'))):
                    return {'Success': False, 'Message': 'Finish and close active agent sessions before changing GPU drivers'}
                if not gpu_service_client.is_service_running():
                    return {'Success': False, 'Message': 'Install LocalAgentGpuModeHelper in Settings first'}
                if pm.is_llama_swap_running() and not pm.stop_llama_swap():
                    return {'Success': False, 'Message': 'Backend is externally managed; stop it before changing GPU drivers'}
                config.update({'active_model_profile': 'none', 'active_preset': None})
                self.rollback_plan = [{'gpu_stable_id': p['gpu_stable_id'], 'target_mode': top.get_device_by_uuid(p['gpu_stable_id']).driver_mode} for p in plan]
                self.state = 'SWITCHING'
                self.log('Applying ' + str(plan))
                response = gpu_service_client.apply_driver_mode_plan(plan, timeout_ms=45000+15000*len(plan))
                hardware.reinit()
                verified = topology_engine.discover_live(force=True)
                if response.get('Success'):
                    for entry in plan:
                        device = verified.get_device_by_uuid(entry['gpu_stable_id'])
                        if device is None or device.driver_mode != entry['target_mode'] or device.pending_driver_mode != entry['target_mode']:
                            response = {'Success': False, 'Message': 'Driver switch failed independent verification'}
                            break
                if response.get('Success'):
                    config.set('active_gpu_profile', gpu_profile_id)
                    response['Message'] = 'GPU modes verified. AI remains stopped; select Start model when ready.'
                self.log(response.get('Message', str(response)))
                response['RollbackPlan'] = self.rollback_plan
                return response
            except Exception as exc:
                self.log(str(exc))
                return {'Success': False, 'Message': str(exc)}
            finally:
                self.state = 'IDLE'
    def apply_model_profile_only(self, model_profile_id, auto_start=True):
        with self._lock:
            model = profile_storage.get_model_profile(model_profile_id)
            if not model:
                return {'Success': False, 'Message': 'Model profile not found'}
            if model.id == 'none':
                active = self.get_active_model_profile()
                if active and active.provider_type in ('openai_compatible', 'ollama'):
                    return {'Success': False, 'Message': 'Модель управляется внешним сервером. Выгрузите её средствами этого сервера.'}
                if not pm.free_gpu():
                    return {'Success': False, 'Message': 'Backend refused unload or is externally managed'}
                config.update({'active_model_profile': 'none', 'active_preset': None})
                self.engine._active_model = None
                return {'Success': True, 'Message': 'Model unloaded'}
            if model.provider_type in ('openai_compatible', 'ollama'):
                try:
                    if not auto_start:
                        config.set('selected_model_profile', model.id)
                        return {'Success': True, 'Message': 'External model selected'}
                    import requests
                    response = requests.post(model.endpoint.rstrip('/') + '/chat/completions',
                        json={'model': model.backend_model_id, 'messages': [{'role': 'user', 'content': 'Reply OK.'}],
                            'max_tokens': 16, 'chat_template_kwargs': {'enable_thinking': False}}, timeout=model.startup_timeout)
                    response.raise_for_status()
                    if not response.json().get('choices'):
                        raise ValueError('External backend returned no choices')
                    config.set('active_model_profile', model.id)
                    return {'Success': True, 'Message': 'External model answered a health request'}
                except Exception as exc:
                    return {'Success': False, 'Message': str(exc)}
            top = self.get_current_topology()
            hw_profile = self.get_active_gpu_profile()
            signature = launch_signature(model, hw_profile, config.get('llama_server_executable'), top)
            if auto_start and pm.is_llama_swap_running():
                try:
                    rows = self.engine.request('/running').get('running', [])
                    running = [r['model'] for r in rows]
                    if config.get('active_model_signature') == signature and any(
                            r['model'] == model.backend_model_id and r.get('state') == 'ready' for r in rows):
                        if self.engine.switch_model(model.backend_model_id, model.startup_timeout):
                            config.set('active_model_profile', model.id)
                            return {'Success': True, 'Message': 'Loaded model answered a health request'}
                    if running:
                        # This preflight checks only whether unloading could help. Actual free
                        # memory is measured again after the owned backend releases its model.
                        from dataclasses import replace
                        candidate = replace(top, devices=[replace(d, vram_free_mib=d.vram_total_mib) for d in top.devices])
                        possible = compatibility_evaluator.evaluate(model, candidate, hw_profile)
                        if not possible.can_run:
                            return {'Success': False, 'Message': possible.summary, 'Evaluation': possible.to_dict()}
                        if not supervisor.status('service:llama-swap')['owned']:
                            return {'Success': False, 'Message': 'Current backend is externally managed; Station cannot unload it'}
                        if not pm.free_gpu():
                            return {'Success': False, 'Message': 'Owned backend refused model unload'}
                        config.update({'active_model_profile': 'none', 'active_preset': None})
                        hardware.reinit()
                        top = topology_engine.discover_live(force=True)
                except Exception as exc:
                    return {'Success': False, 'Message': str(exc)}
            evaluation = compatibility_evaluator.evaluate(model, top, hw_profile)
            if not evaluation.can_run:
                return {'Success': False, 'Message': evaluation.summary, 'Evaluation': evaluation.to_dict()}
            if not auto_start:
                config.set('selected_model_profile', model.id)
                return {'Success': True, 'Message': 'Model selected; backend has not been started'}
            try:
                path, changed, _ = compile_swap(list(profile_storage.model_profiles.values()), top, hw_profile, config.get('llama_server_executable'))
                if changed and pm.is_llama_swap_running():
                    if not pm.stop_llama_swap():
                        raise RuntimeError('Backend configuration changed, but the external process cannot be restarted')
                    self.engine._active_model = None
                    config.update({'active_model_profile': 'none', 'active_preset': None})
                    hardware.reinit()
                    top = topology_engine.discover_live(force=True)
                    evaluation = compatibility_evaluator.evaluate(model, top, hw_profile)
                    if not evaluation.can_run:
                        raise RuntimeError(evaluation.summary)
                    path, _, _ = compile_swap(list(profile_storage.model_profiles.values()), top, hw_profile, config.get('llama_server_executable'))
                if not pm.is_llama_swap_running():
                    if not pm.start_llama_swap(path):
                        raise RuntimeError('llama-swap failed to start. Check configured paths and service logs.')
                if not self.engine.switch_model(model.backend_model_id, model.startup_timeout):
                    config.update({'active_model_profile': 'none', 'active_preset': None})
                    raise RuntimeError(self.engine.last_error or 'Model health request failed')
                config.update({'active_model_profile': model.id, 'selected_model_profile': model.id,
                    'active_model_signature': signature, 'active_preset': None})
                return {'Success': True, 'Message': 'Model answered a real health request', 'Evaluation': evaluation.to_dict()}
            except Exception as exc:
                return {'Success': False, 'Message': str(exc)}
    def apply_preset(self, preset_id):
        with self._lock:
            p = profile_storage.get_station_preset(preset_id)
            if not p or not profile_storage.get_gpu_profile(p.gpu_profile_id) or not profile_storage.get_model_profile(p.model_profile_id):
                return {'Success': False, 'Message': 'Preset has missing profile references'}
            result = self.apply_gpu_profile_only(p.gpu_profile_id, True)
            if not result.get('Success'):
                return result
            result = self.apply_model_profile_only(p.model_profile_id, p.auto_start_model)
            if result.get('Success'):
                config.update({'active_preset': p.id, 'primary_agent_runtime': p.primary_agent_runtime, 'preferred_frontend': p.preferred_frontend})
            return result
    def save_current_as_preset(self, name, icon='*', description=''):
        import uuid
        p = StationPreset(id='user-'+uuid.uuid4().hex[:8], name=name, icon=icon, description=description,
            gpu_profile_id=config.get('active_gpu_profile'), model_profile_id=config.get('active_model_profile'),
            primary_agent_runtime=config.get('primary_agent_runtime'), preferred_frontend=config.get('preferred_frontend'))
        profile_storage.save_station_preset(p)
        return p

gpu_mode_manager = ProfileExecutionManager()
