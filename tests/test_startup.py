"""Startup ordering, opt-in defaults, failure/cancel boundaries and frontend ownership."""
import threading
from types import SimpleNamespace as NS
from unittest.mock import Mock
import pytest
from src.config import AppConfig
from src.startup import startup_settings, startup_steps, StartupRunner
from src.storage import ConfigurationError
from src.ui.agent_controls import frontend_action_state


def fixture_runner():
    calls = []
    def launch(kind):
        def run(id, **kwargs):
            calls.append((kind, id))
            return {'Success': True, 'Message': 'started'}
        return Mock(side_effect=run)
    controller = NS(frontends={'desktop': {'name': 'Desktop', 'runtime_id': 'qwen', 'status': 'INSTALLED'}},
        adapters={'qwen': NS(id='qwen', manifest={})},
        model_binding_state=Mock(return_value='READY'), frontend_status=Mock(return_value={'running': False}),
        launch_frontend=launch('frontend'))
    services = NS(profiles=lambda: {'worker': {'name': 'Worker', 'type': 'local'}, 'remote': {'type': 'remote'}},
                  start=launch('service'))
    models = NS(apply_model_profile_only=launch('model'))
    profiles = NS(model_profiles={'model': NS(id='model', name='Model')})
    runner = StartupRunner(controller, services, models, profiles)
    settings = startup_settings({'enabled': True, 'service_ids': ['worker'], 'model_id': 'model', 'frontend_ids': ['desktop']})
    return runner, settings, calls


def test_new_or_old_configuration_starts_nothing(tmp_path):
    cfg = AppConfig(tmp_path / 'station.yaml')
    assert startup_steps(cfg.get('startup')) == []
    assert cfg.get('startup')['minimized'] is False
    cfg.set('autostart', True)  # old unused field must not start models/agents
    assert startup_steps(AppConfig(cfg.path).get('startup')) == []


@pytest.mark.parametrize('patch', [{'delay_seconds': -1}, {'delay_seconds': 301}, {'delay_seconds': True},
    {'enabled': 'yes'}, {'minimized': 1}, {'stop_on_error': None}, {'frontend_ids': ['a', 'a']}, {'service_ids': 'a'}])
def test_invalid_settings_do_not_overwrite_saved_data(tmp_path, patch):
    cfg = AppConfig(tmp_path / 'station.yaml')
    cfg.set('startup', startup_settings({'delay_seconds': 5}))
    before = cfg.path.read_bytes()
    with pytest.raises(ConfigurationError):
        cfg.set('startup', patch)
    assert cfg.path.read_bytes() == before
    assert cfg.get('startup')['delay_seconds'] == 5


def test_selected_components_survive_reload_without_starting(tmp_path):
    cfg = AppConfig(tmp_path / 'station.yaml')
    saved = startup_settings({'enabled': True, 'minimized': True, 'delay_seconds': 12,
                             'model_id': 'qwen', 'frontend_ids': ['desktop', 'pi'], 'service_ids': ['local']})
    cfg.set('startup', saved)
    assert AppConfig(cfg.path).get('startup') == saved


def test_order_readiness_binding_and_once_only():
    runner, settings, calls = fixture_runner()
    assert runner.run(settings, threading.Event())['Success']
    assert calls == [('service', 'worker'), ('model', 'model'), ('frontend', 'desktop')]
    runner.controller.model_binding_state.assert_called_once()
    runner.controller.launch_frontend.assert_called_once_with('desktop', remember=False)
    runner.run(settings, threading.Event())
    assert len(calls) == 3


def test_model_failure_prevents_agent_start():
    runner, settings, calls = fixture_runner()
    runner.models.apply_model_profile_only.side_effect = lambda id: {'Success': False, 'Message': 'No VRAM'}
    result = runner.run(settings, threading.Event())
    assert not result['Success'] and 'No VRAM' in result['Message']
    runner.controller.launch_frontend.assert_not_called()


def test_continue_on_error_is_explicit():
    runner, settings, calls = fixture_runner()
    settings['stop_on_error'] = False
    runner.services.start.side_effect = OSError('missing executable')
    result = runner.run(settings, threading.Event())
    assert not result['Success']
    assert calls == [('model', 'model'), ('frontend', 'desktop')]


@pytest.mark.parametrize('kind,id', [('service', 'remote'), ('service', 'missing'), ('model', 'missing'), ('frontend', 'missing')])
def test_deleted_or_remote_components_are_not_started(kind, id):
    runner, settings, calls = fixture_runner()
    settings.update(service_ids=[], frontend_ids=[], model_id='none')
    settings[{'service': 'service_ids', 'frontend': 'frontend_ids', 'model': 'model_id'}[kind]] = id if kind == 'model' else [id]
    assert not runner.run(settings, threading.Event())['Success']
    assert calls == []


def test_autostart_never_applies_a_new_binding():
    runner, settings, calls = fixture_runner()
    runner.controller.model_binding_state.return_value = 'NEEDS_REVIEW'
    result = runner.run(settings, threading.Event())
    assert not result['Success'] and 'не настроен на модель' in result['Message']
    runner.controller.launch_frontend.assert_not_called()


def test_cancel_before_and_between_steps():
    runner, settings, calls = fixture_runner()
    cancel = threading.Event()
    def start(id):
        calls.append(('service', id))
        cancel.set()
        return {'Success': True}
    runner.services.start.side_effect = start
    assert not runner.run(settings, cancel)['Success']
    assert calls == [('service', 'worker')]
    runner, settings, calls = fixture_runner()
    assert not runner.run(settings, cancel)['Success']
    assert calls == []


def test_already_running_agent_does_not_require_binding_rewrite():
    runner, settings, calls = fixture_runner()
    runner.controller.frontend_status.return_value = {'running': True}
    runner.run(settings, threading.Event())
    runner.controller.model_binding_state.assert_not_called()


def test_controller_reuses_owned_frontend_and_keeps_selection(monkeypatch):
    import src.controller as module
    controller = module.StationController()
    controller.frontends = {'qwen-desktop': {'runtime_id': 'qwen-code', 'type': 'desktop', 'status': 'INSTALLED'}}
    monkeypatch.setattr(module, 'supervisor', NS(status=Mock(return_value={'running': True}), start=Mock()))
    before = module.config.get('preferred_frontend')
    assert controller.launch_frontend('qwen-desktop', remember=False)['Success']
    module.supervisor.start.assert_not_called()
    assert module.config.get('preferred_frontend') == before


def test_frontend_buttons_only_stop_owned_and_only_start_available():
    assert frontend_action_state({'status': 'INSTALLED'}, False) == {'start': True, 'stop': False}
    assert frontend_action_state({'status': 'INSTALLED'}, True) == {'start': False, 'stop': True}
    assert frontend_action_state({'status': 'NOT INSTALLED'}, False) == {'start': False, 'stop': False}
    assert frontend_action_state({'status': 'INSTALLED'}, True, busy=True) == {'start': False, 'stop': False}


def test_windows_command_persists_data_directory_and_handles_spaces(tmp_path, monkeypatch):
    import src.windows_startup as module
    executable = tmp_path / 'Station App/LocalAgentAIStation.exe'
    executable.parent.mkdir()
    executable.write_bytes(b'fixture')
    monkeypatch.setattr(module.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(module.sys, 'executable', str(executable))
    monkeypatch.setattr(module, 'data_dir', lambda: tmp_path / 'Private Data')
    value = module.launch_command()
    assert value['target'] == str(executable)
    assert value['arguments'] == f'--data-dir "{tmp_path / "Private Data"}" --startup'
    assert value['working_directory'] == str(executable.parent)


def test_autostart_launches_when_selected_model_is_bound_despite_stale_entries():
    runner, settings, calls = fixture_runner()
    runner.controller.model_binding_state.return_value = 'READY_STALE'
    assert runner.run(settings, threading.Event())['Success']
    assert ('frontend', 'desktop') in calls


def test_desktop_opened_outside_station_is_running_but_not_stoppable(monkeypatch):
    import os
    import src.controller as module
    from src.ui.agent_controls import frontend_state_text
    controller = module.StationController()
    exe = r'C:\Apps\Qwen Code Desktop\qwen-code-desktop.exe'
    controller.frontends = {'qwen-desktop': {'runtime_id': 'qwen-code', 'type': 'desktop', 'status': 'INSTALLED', 'executable': exe}}
    monkeypatch.setattr(module, 'supervisor', NS(status=Mock(return_value={'running': False, 'owned': False, 'pid': None})))
    frontend = controller.frontends['qwen-desktop']
    stopped = controller.frontend_status('qwen-desktop', processes={})
    assert stopped == {'running': False, 'owned': False, 'pid': None}
    assert frontend_state_text(frontend, stopped) == 'Не запущен · установлен'
    external = controller.frontend_status('qwen-desktop', processes={os.path.normcase(exe): 4242})
    assert external == {'running': True, 'owned': False, 'pid': 4242}
    assert frontend_state_text(frontend, external) == 'Запущен вне Station, PID 4242'
    assert frontend_action_state(frontend, True, owned=False) == {'start': False, 'stop': False}
