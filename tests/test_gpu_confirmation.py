"""No-op, remembered confirmation and live guards; never call a GPU driver."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.config import AppConfig
from src.gpu_modes import ProfileExecutionManager
from src.hardware_topology import GpuDeviceInfo, HardwareTopology
from src.profiles_schema import GpuHardwareProfile
from src.ui.gpu_confirmation import GpuControls, change_lines, TCC_WARNING
import src.gpu_modes as modes
import src.ui.gpu_confirmation as ui


@pytest.fixture
def environment(tmp_path, monkeypatch):
    cfg = AppConfig(tmp_path / 'station.yaml')
    manager = ProfileExecutionManager()
    profile = GpuHardwareProfile('test', 'Все в TCC', general_policy='all_tcc')
    top = HardwareTopology([GpuDeviceInfo(0, 'GPU-test', name='Test card',
        driver_mode='WDDM', pending_driver_mode='WDDM', tcc_supported=True, display_active=False)])
    monkeypatch.setattr(modes, 'config', cfg)
    monkeypatch.setattr(ui, 'config', cfg)
    monkeypatch.setattr(modes, 'profile_storage', SimpleNamespace(get_gpu_profile=lambda id: profile))
    monkeypatch.setattr(manager, 'get_current_topology', lambda: top)
    monkeypatch.setattr(ui, 'gpu_mode_manager', manager)
    service = Mock()
    service.is_service_running.side_effect = AssertionError('Unexpected driver service access')
    service.apply_driver_mode_plan.side_effect = AssertionError('Must never change hardware in these tests')
    monkeypatch.setattr(modes, 'gpu_service_client', service)
    return cfg, manager, top, service


class Harness(GpuControls):
    def __init__(self):
        self.busy = False
        self.deiconify = Mock()
        self.lift = Mock()
        self.status_label = Mock()
        self.gpu_combo = Mock()
        self.tray = Mock(HAS_NOTIFICATION=True)
        self._refresh_tray = Mock()
        self.state = lambda: 'withdrawn'

    def worker(self, action, callback, label=None):
        callback(action())


@pytest.mark.parametrize('devices', [0, 1, 4, 8])
def test_no_op_has_feedback_without_dialog_or_service(environment, monkeypatch, devices):
    cfg, manager, top, service = environment
    top.devices = [GpuDeviceInfo(i, f'GPU-{i}', driver_mode='TCC', pending_driver_mode='TCC') for i in range(devices)]
    dialog = Mock(side_effect=AssertionError('Empty plan must not open confirmation'))
    monkeypatch.setattr(ui, 'GpuConfirmDialog', dialog)
    app = Harness()
    app._preview_gpu('test')
    assert cfg.get('active_gpu_profile') == 'test'
    assert 'не требуется' in app.tray.notify.call_args.args[0]
    app.deiconify.assert_not_called()
    service.is_service_running.assert_not_called()
    service.apply_driver_mode_plan.assert_not_called()


def test_skipped_unsupported_card_is_not_reported_as_switched(environment, monkeypatch):
    cfg, manager, top, _ = environment
    top.devices[0].tcc_supported = False
    monkeypatch.setattr(ui, 'GpuConfirmDialog', Mock(side_effect=AssertionError('No applicable changes')))
    app = Harness()
    app._preview_gpu('test')
    assert 'TCC не поддерживается' in app.tray.notify.call_args.args[0]
    assert top.devices[0].driver_mode == 'WDDM'


@pytest.mark.parametrize('suppress', [False, True])
def test_confirmation_cancel_confirm_and_restart(environment, monkeypatch, suppress):
    cfg, manager, top, _ = environment
    apply = Mock(return_value={'Success': True, 'Message': 'Test only'})
    monkeypatch.setattr(manager, 'apply_gpu_profile_only', apply)
    dialog = Mock(return_value=SimpleNamespace(winfo_exists=lambda: False))
    monkeypatch.setattr(ui, 'GpuConfirmDialog', dialog)
    app = Harness()
    app._preview_gpu('test')
    apply.assert_not_called()
    assert not cfg.get('suppress_gpu_switch_warning')  # Opening/closing does not persist the checkbox.
    callback = dialog.call_args.args[2]
    callback(suppress)
    assert apply.call_count == 1
    assert apply.call_args.kwargs['expected_plan'] == manager.preview_gpu_plan('test')['Plan']
    reloaded = AppConfig(cfg.path)
    assert reloaded.get('suppress_gpu_switch_warning') is suppress
    monkeypatch.setattr(ui, 'config', reloaded)
    app._preview_gpu('test')
    assert dialog.call_count == (1 if suppress else 2)
    assert apply.call_count == (2 if suppress else 1)
    app._reset_gpu_confirmation()
    assert not AppConfig(cfg.path).get('suppress_gpu_switch_warning')
    app._preview_gpu('test')
    assert dialog.call_count == (2 if suppress else 3)


@pytest.mark.parametrize('display', [True, None])
def test_remembered_checkbox_never_bypasses_display_guard(environment, display):
    cfg, manager, top, service = environment
    cfg.set('suppress_gpu_switch_warning', True)
    top.devices[0].display_active = display
    app = Harness()
    app._preview_gpu('test')
    assert 'заблокировано' in app.tray.notify.call_args.args[0]
    assert not manager.apply_gpu_profile_only('test')['Success']
    service.apply_driver_mode_plan.assert_not_called()


@pytest.mark.parametrize('empty', [False, True])
def test_changed_plan_cannot_apply_unreviewed_changes(environment, empty):
    cfg, manager, top, service = environment
    expected = [] if empty else deepcopy(manager.preview_gpu_plan('test')['Plan'])
    top.devices.append(GpuDeviceInfo(1, 'GPU-new', driver_mode='WDDM',
        pending_driver_mode='WDDM', display_active=False, tcc_supported=True))
    result = manager.apply_gpu_profile_only('test', expected_plan=expected)
    assert not result['Success'] and 'изменилось' in result['Message']
    service.is_service_running.assert_not_called()
    assert cfg.get('active_gpu_profile') == 'gpu-unchanged'


def test_plan_shows_named_cards_modes_and_pending_state(environment):
    _, manager, top, _ = environment
    top.devices[0].pending_driver_mode = 'TCC'
    preview = manager.preview_gpu_plan('test')
    assert change_lines(preview) == ['GPU 0 · Test card\nWDDM → TCC · отложенный режим: TCC']
    assert set(preview['Plan'][0]) == {'gpu_stable_id', 'target_mode'}  # Wire API stays narrow.
    assert 'Отключите все мониторы' in TCC_WARNING and 'WDDM' in TCC_WARNING


def test_suppression_cannot_be_enabled_by_a_truthy_string(environment):
    cfg, _, _, _ = environment
    with pytest.raises(ValueError):
        cfg.set('suppress_gpu_switch_warning', 'false')
    assert not cfg.get('suppress_gpu_switch_warning')


def test_first_gpu_wddm_mode_ignores_display_on_integrated_graphics(monkeypatch):
    from src.gpu_modes import ProfileExecutionManager
    from src.hardware_topology import HardwareTopology
    manager = ProfileExecutionManager()
    top = HardwareTopology([
        GpuDeviceInfo(0, 'GPU-a', vendor='NVIDIA', driver_mode='TCC', pending_driver_mode='TCC', tcc_supported=True, display_active=False),
        GpuDeviceInfo(1, 'GPU-b', vendor='NVIDIA', driver_mode='TCC', pending_driver_mode='TCC', tcc_supported=True, display_active=False),
        GpuDeviceInfo(2, 'intel', vendor='Intel', driver_mode='WDDM', display_active=True)], is_simulated=True)
    plan = manager.preview_gpu_plan('gpu-first-wddm-rest-tcc', top)['Plan']
    assert plan == [{'gpu_stable_id': 'GPU-a', 'target_mode': 'WDDM'}]
    assert manager.current_quick_mode(top) == 'gpu-all-tcc'
    top.devices[0].driver_mode = 'WDDM'
    assert manager.current_quick_mode(top) == 'gpu-first-wddm-rest-tcc'
