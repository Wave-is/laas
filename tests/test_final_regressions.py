"""Failure cases observed during integration and final desktop checks."""
import json
import queue
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.config import AppConfig
from src.storage import ConfigurationError
from src.compatibility import compatibility_evaluator
from src.profiles_schema import ModelProfile, GpuHardwareProfile
from src.hardware_topology import HardwareTopology, GpuDeviceInfo
from src.validation import validate_registry


@pytest.mark.parametrize('value', [None, '3', True, float('nan'), float('inf'), 0, -1])
def test_invalid_poll_interval_cannot_disable_monitoring(tmp_path, value):
    cfg = AppConfig(tmp_path/'station.yaml')
    with pytest.raises(ConfigurationError):
        cfg.set('poll_interval_sec', value)
    assert cfg.get('poll_interval_sec') == 3
    assert not cfg.path.exists()


@pytest.mark.parametrize('requirement', ['require_p2p', 'require_nvlink'])
def test_strict_hardware_transport_is_enforced(requirement):
    top = HardwareTopology([GpuDeviceInfo(i, f'gpu{i}', display_active=False,
        vram_total_mib=24000, vram_free_mib=24000) for i in range(2)], is_simulated=True)
    model = ModelProfile('m', 'Model', 'test.gguf')
    preferred = GpuHardwareProfile('h', 'Hardware', prefer_p2p=True)
    assert compatibility_evaluator.evaluate(model, top, preferred).can_run
    setattr(preferred, requirement, True)
    assert not compatibility_evaluator.evaluate(model, top, preferred).can_run


def test_ui_callback_error_does_not_stop_telemetry_queue():
    from src.ui.control_center import ControlCenter
    events = queue.Queue()
    events.put(('result', {}, Mock(side_effect=ValueError('bad event'))))
    app = SimpleNamespace(events=events, stop_event=threading.Event(), busy=True,
        status_label=Mock(), after=Mock(), _drain_events=Mock())
    ControlCenter._drain_events(app)
    assert not app.busy
    app.after.assert_called_once_with(100, app._drain_events)


def test_stopping_backend_model_is_not_reported_as_active():
    from src.engines.llama_swap import LlamaSwapEngine
    engine = LlamaSwapEngine(); engine._active_model = 'm'
    engine.request = lambda *a, **k: {'running': [{'model': 'm', 'state': 'stopping'}]}
    assert engine.get_active_model() is None

def test_cpu_backend_disables_implicit_gpu_offload(tmp_path):
    from src.model_backend import build_model_entry
    weights=tmp_path/'weights.gguf'; weights.write_bytes(b'test')
    executable=tmp_path/'server.exe'; executable.write_bytes(b'test')
    model=ModelProfile('m','CPU',str(weights),backend='cpu',gpu_layers=0,min_gpu_count=0)
    entry=build_model_entry(model,[],str(executable))
    assert '--device none' in entry['cmd'] and '--no-op-offload' in entry['cmd']
    assert entry['env']==['CUDA_VISIBLE_DEVICES=']


def test_qwen_smoke_rejects_answer_from_another_model(tmp_path, monkeypatch):
    from src.agents.qwen_code.adapter import QwenCodeAdapter
    import src.agents.qwen_code.adapter as module
    qwen = QwenCodeAdapter(); qwen.command = ['qwen']
    qwen.help_text = '--bare --safe-mode --max-tool-calls --max-wall-time --auth-type'
    events = [{'type': 'system', 'subtype': 'init', 'model': 'wrong-model'},
        {'type': 'result', 'is_error': False, 'result': 'STATION_OK'}]
    monkeypatch.setattr(module.subprocess, 'run', lambda *a, **kw:
        SimpleNamespace(returncode=0, stdout=json.dumps(events), stderr=''))
    assert not qwen.smoke(ModelProfile('expected', 'Expected', 'f.gguf'), str(tmp_path)).ok


def test_qwen_terminal_respects_configured_home(tmp_path, monkeypatch):
    from src.agents.qwen_code.adapter import QwenCodeAdapter
    import src.agents.qwen_code.adapter as module
    qwen = QwenCodeAdapter(); qwen.command = ['qwen']; qwen.settings = {'home': str(tmp_path)}
    start = Mock(return_value={'running': True})
    monkeypatch.setattr(module, 'supervisor', SimpleNamespace(start=start))
    assert qwen.start(str(tmp_path)).ok
    assert start.call_args.kwargs['env']['QWEN_HOME'] == str(tmp_path)


@pytest.mark.parametrize('change', [
    {'health_url': 'https://user:password@example.com/health'},
    {'health_url': 'file:///private'}, {'restart_policy': 'always'},
    {'token_reference': 'secret with spaces'},
    {'environment_references': {'BROKEN=KEY': 'worker/token'}},
])
def test_invalid_service_configuration_is_rejected(change):
    with pytest.raises(ValueError):
        validate_registry('services.yaml', [{'id': 'worker', **change}])


def test_watchdog_stops_after_three_failed_restarts(monkeypatch):
    import src.shared_services as module
    services = module.SharedServices()
    services.desired.add('worker')
    monkeypatch.setattr(services, 'profiles', lambda: {'worker': {'restart_policy': 'on_failure'}})
    monkeypatch.setattr(services, 'status', lambda id: {'running': False})
    restart = Mock(side_effect=RuntimeError('cannot start'))
    monkeypatch.setattr(services, 'start', restart)
    clock = iter([1000, 1000, 2000, 2000, 3000, 3000])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    services.poll(allow_restart=False)
    assert restart.call_count == 0
    for _ in range(6): services.poll()
    assert restart.call_count == 3 and services.failures['worker'] == 3


def test_control_center_lazy_page_building():
    from src.ui.control_center import ControlCenter, PAGE_IDS
    assert hasattr(ControlCenter, '_ensure_page_built')
    assert hasattr(ControlCenter, '_init_background_services')

    class FakeCC:
        controller = None
        poll_hooks = []
        _build_startup_banner = lambda self: None
        _monitoring_alert = lambda self: None
        _execute_schedule_action = lambda self, *a: None
        _schedules_poll = lambda self, *a: None
        call_in_ui = lambda self, f: None
        notify = lambda self, m, t: None

    cc = FakeCC()
    ControlCenter._init_background_services(cc)
    assert hasattr(cc, 'startup_runner')
    assert hasattr(cc, 'metrics_store')
    assert hasattr(cc, 'schedule_manager')


def test_instance_path_normalization(tmp_path):
    from src.instance import StationInstance
    p1 = str(tmp_path)
    p2 = str(tmp_path).upper()
    inst1 = StationInstance(p1)
    inst2 = StationInstance(p2)
    assert inst1.directory == inst2.directory
    assert inst1.mutex_name == inst2.mutex_name
    assert inst1.port == inst2.port

