"""Provider isolation, npm discovery, saved binding verification and rollback."""
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from src.agents import load_adapters
from src.agents.pi.adapter import PiAdapter
from src.agents.openclaw.adapter import OpenClawAdapter
from src.agents.local_provider import provider_id
from src.agent_sync import apply_preview, combine_previews, preview_merge, SyncTransaction
from src.profiles_schema import ModelProfile
from src.storage import atomic_write, read_document, ConfigurationConflict, ConfigurationError


@pytest.fixture
def model():
    return ModelProfile('m', 'Local model', 'weights.gguf', context=32768)


def ready(adapter, home):
    adapter.command = ['node', 'cli.js']
    adapter.settings = {'home': str(home)}
    adapter.help_text = '--provider --model --print --mode --no-tools --no-extensions --no-skills --no-prompt-templates --no-themes --no-session --system-prompt'
    if isinstance(adapter, OpenClawAdapter):
        adapter.config_help = 'validate'
        adapter.exec_help = '--config --cwd --json --timeout --state-dir'
        adapter.tui_help = '--local'
        adapter.gateway_help = '--bind --port --tailscale'
    return adapter


def transaction(adapter, model):
    return combine_previews([adapter.configure_model_provider([model]).data, adapter.configure_model_binding(model).data])


def test_registry_keeps_qwen_default_and_adds_optional_agents():
    from src.config import DEFAULT_SETTINGS
    assert {'qwen-code', 'hermes', 'pi', 'openclaw', 'aider'} <= set(load_adapters())
    assert DEFAULT_SETTINGS['primary_agent_runtime'] == 'qwen-code'


@pytest.mark.parametrize('package,entry', [('@earendil-works/pi-coding-agent', 'dist/bundle/cli.js'),
    ('@mariozechner/pi-coding-agent', 'dist/cli.js'), ('openclaw', 'openclaw.mjs')])
def test_npm_shim_resolves_actual_package_entrypoint(tmp_path, monkeypatch, package, entry):
    import src.agents.discovery as module
    binary = 'openclaw' if package == 'openclaw' else 'pi'
    prefix = tmp_path / 'npm with spaces'
    root = prefix / 'node_modules' / package
    target = root / entry; target.parent.mkdir(parents=True); target.write_text('test')
    atomic_write(root / 'package.json', {'name': package, 'bin': {binary: entry}})
    shim = prefix / (binary + '.cmd'); shim.write_text('must never run')
    monkeypatch.setattr(module.shutil, 'which', lambda name: str(shim) if name == binary else 'node.exe' if name == 'node' else None)
    command, found = module.npm_installation(binary, (package,))
    assert command == ['node.exe', str(target.resolve())]
    assert str(shim) not in command and found == root


def test_explicit_npm_package_cannot_be_replaced_by_path_runtime(tmp_path, monkeypatch):
    import src.agents.discovery as module
    monkeypatch.setattr(module.shutil, 'which', lambda name: 'unrelated.exe')
    with pytest.raises(ValueError, match='was not found'):
        module.npm_installation('pi', ('@earendil-works/pi-coding-agent',), {'package_root': str(tmp_path)})


def test_npm_entrypoint_cannot_escape_package(tmp_path):
    from src.agents.discovery import npm_installation
    root = tmp_path / 'package'; root.mkdir()
    (tmp_path / 'outside.js').write_text('test')
    atomic_write(root / 'package.json', {'name': 'openclaw', 'bin': {'openclaw': '../outside.js'}})
    with pytest.raises(ValueError, match='entrypoint'):
        npm_installation('openclaw', ('openclaw',), {'package_root': str(root), 'node': 'node.exe'})


@pytest.mark.parametrize('cls', [PiAdapter, OpenClawAdapter])
def test_same_backend_model_on_distinct_endpoints_keeps_distinct_bindings(tmp_path, model, cls):
    a = ready(cls(), tmp_path)
    first = replace(model, provider_type='openai', startup_key='shared-model', endpoint='http://localhost:9001/v1')
    second = replace(first, id='other', endpoint='http://localhost:9002/v1')
    preview = a.configure_model_provider([first, second, replace(model, id='disabled', status='disabled')]).data
    doc = preview.after if cls == PiAdapter else preview.after['models']
    providers = doc['providers']
    assert len(providers) == 2
    assert providers[provider_id(first)]['baseUrl'] != providers[provider_id(second)]['baseUrl']
    assert {p['models'][0]['id'] for p in providers.values()} == {'shared-model'}


def test_pi_two_files_preserve_custom_configuration(tmp_path, model):
    a = ready(PiAdapter(), tmp_path)
    atomic_write(tmp_path / 'settings.json', {'theme': 'custom', 'defaultModel': 'old'})
    atomic_write(tmp_path / 'models.json', {'providers': {'private': {'apiKey': 'PRIVATE_SECRET'}}})
    preview = transaction(a, model)
    assert isinstance(preview, SyncTransaction) and 'PRIVATE_SECRET' not in preview.diff
    result = apply_preview(preview, accept_custom=True, smoke=lambda: a.validate_configuration().ok)
    assert result['status'] == 'IN SYNC' and len(result['backups']) == 2
    assert read_document(tmp_path / 'settings.json')['theme'] == 'custom'
    assert read_document(tmp_path / 'models.json')['providers']['private']['apiKey'] == 'PRIVATE_SECRET'
    assert transaction(a, model).status == 'IN SYNC'


def test_failed_pi_smoke_rolls_back_catalog_and_binding(tmp_path, model):
    a = ready(PiAdapter(), tmp_path)
    atomic_write(tmp_path / 'settings.json', {'theme': 'dark'})
    with pytest.raises(RuntimeError):
        apply_preview(transaction(a, model), smoke=lambda: False)
    assert not (tmp_path / 'models.json').exists()
    assert read_document(tmp_path / 'settings.json') == {'theme': 'dark'}


def test_transaction_rejects_stale_second_file_before_first_write(tmp_path, model):
    a = ready(PiAdapter(), tmp_path)
    preview = transaction(a, model)
    atomic_write(tmp_path / 'settings.json', {'theme': 'new'})
    with pytest.raises(ConfigurationConflict):
        apply_preview(preview)
    assert not (tmp_path / 'models.json').exists()


def test_transaction_preserves_concurrent_edit_but_rolls_back_other_file(tmp_path, model):
    a = ready(PiAdapter(), tmp_path)
    def changed():
        atomic_write(tmp_path / 'settings.json', {'human': 'edit'})
        return False
    with pytest.raises(ConfigurationConflict):
        apply_preview(transaction(a, model), smoke=changed)
    assert not (tmp_path / 'models.json').exists()
    assert read_document(tmp_path / 'settings.json') == {'human': 'edit'}


def test_openclaw_json5_preserves_other_agents_channels_and_credentials(tmp_path, model):
    a = ready(OpenClawAdapter(), tmp_path)
    (tmp_path / 'openclaw.json').write_text('''{ // operator comment
      agents: {defaults: {model: 'old/model'}, list: [
        {id: 'main', default: true, model: 'old/model'}, {id: 'other', model: 'private/model'}]},
      channels: {telegram: {botToken: 'PRIVATE_SECRET'}},
    }''')
    preview = transaction(a, model)
    assert 'PRIVATE_SECRET' not in preview.diff
    apply_preview(preview, accept_custom=True)
    doc = read_document(tmp_path / 'openclaw.json')
    assert doc['channels']['telegram']['botToken'] == 'PRIVATE_SECRET'
    assert doc['agents']['list'][1]['model'] == 'private/model'
    assert doc['agents']['list'][0]['model'] == doc['agents']['defaults']['model']
    assert doc['agents']['defaults']['model']['fallbacks'] == []
    assert any('operator comment' in p.read_text() for p in (tmp_path / 'backups').glob('*.bak'))


def test_openclaw_include_is_rejected_before_any_write(tmp_path, model):
    a = ready(OpenClawAdapter(), tmp_path)
    atomic_write(tmp_path / 'openclaw.json', {'models': {'$include': 'providers.json'}})
    with pytest.raises(ValueError, match='include'):
        transaction(a, model)
    assert not (tmp_path / 'backups').exists()


def test_json5_duplicate_keys_do_not_silently_overwrite_credentials(tmp_path):
    path = tmp_path / 'openclaw.json'; path.write_text('{providers: 1, providers: 2}')
    with pytest.raises(ConfigurationError):
        read_document(path, allow_json5=True)


def test_openclaw_home_override_does_not_reuse_ambient_config(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENCLAW_CONFIG_PATH', '/someone/else/openclaw.json')
    a = ready(OpenClawAdapter(), tmp_path)
    assert a.process_environment()['OPENCLAW_CONFIG_PATH'] == str(tmp_path / 'openclaw.json')
    assert a.process_environment()['OPENCLAW_STATE_DIR'] == str(tmp_path)


@pytest.mark.parametrize('cls', [PiAdapter, OpenClawAdapter])
def test_missing_runtime_is_optional_and_cannot_be_synced(tmp_path, monkeypatch, cls, model):
    module = __import__(cls.__module__, fromlist=['npm_installation'])
    monkeypatch.setattr(module, 'npm_installation', lambda *a: ([], None))
    a = cls()
    assert not a.detect().ok and all(f['status'] == 'NOT INSTALLED' for f in a.frontends)
    assert not a.configure_model_provider([model]).ok
    assert not a.start().ok


@pytest.mark.parametrize('wrong', [False, True])
def test_pi_smoke_uses_saved_binding_and_disables_tools(tmp_path, model, monkeypatch, wrong):
    import src.agents.pi.adapter as module
    a = ready(PiAdapter(), tmp_path / 'pi'); apply_preview(transaction(a, model))
    def run(args, **kwargs):
        assert '--no-tools' in args and '--no-extensions' in args
        assert '--provider' not in args and '--model' not in args
        assert Path(kwargs['env']['PI_CODING_AGENT_DIR']) != tmp_path / 'pi'
        message = {'type': 'message_end', 'message': {'role': 'assistant', 'model': 'wrong' if wrong else model.backend_model_id,
            'provider': provider_id(model), 'stopReason': 'stop', 'content': [{'type': 'text', 'text': 'STATION_OK'}]}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(message), stderr='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert a.smoke(model, str(tmp_path), configuration_path=tmp_path / 'pi/models.json').ok is not wrong


@pytest.mark.parametrize('wrong', [False, True])
def test_openclaw_smoke_has_no_channels_plugins_or_model_override(tmp_path, model, monkeypatch, wrong):
    import src.agents.openclaw.adapter as module
    a = ready(OpenClawAdapter(), tmp_path / 'oc'); apply_preview(transaction(a, model))
    from src.agents.base import Result, Support
    a.validate_configuration = lambda: Result(Support.SUPPORTED)
    def run(args, **kwargs):
        assert '--model' not in args and '--deliver' not in args
        doc = read_document(Path(args[args.index('--config') + 1]))
        assert Path(args[args.index('--state-dir') + 1]).is_dir()
        assert 'channels' not in doc and doc['plugins']['enabled'] is False and doc['tools']['deny'] == ['*']
        assert doc['agents']['defaults']['model']['primary'] == provider_id(model) + '/' + model.backend_model_id
        result = {'ok': True, 'status': 'ok', 'final': 'STATION_OK', 'provider': provider_id(model),
            'model': 'wrong' if wrong else model.backend_model_id, 'toolSummary': {'calls': 0}}
        return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr='')
    monkeypatch.setattr(module.subprocess, 'run', run)
    assert a.smoke(model, str(tmp_path), configuration_path=tmp_path / 'oc/openclaw.json').ok is not wrong


def test_openclaw_gateway_uses_owned_foreground_process_and_loopback(tmp_path, monkeypatch):
    import src.agents.openclaw.adapter as module
    from src.agents.base import Result, Support
    a = ready(OpenClawAdapter(), tmp_path)
    a.validate_configuration = lambda: Result(Support.SUPPORTED)
    atomic_write(tmp_path / 'openclaw.json', {'gateway': {'mode': 'local'}})
    start = Mock(return_value={'running': True}); monkeypatch.setattr(module, 'supervisor', SimpleNamespace(start=start))
    assert a.launch_frontend({'type': 'gateway', 'id': 'openclaw-gateway'}, str(tmp_path)).ok
    args = start.call_args.args[1]
    assert args[args.index('--bind') + 1] == 'loopback' and '--force' not in args and 'install' not in args
    assert start.call_args.args[0] == 'frontend:openclaw-gateway'


def test_openclaw_old_cli_cannot_run_new_local_mode(tmp_path):
    a = ready(OpenClawAdapter(), tmp_path); a.tui_help = ''; a.exec_help = ''
    assert not a.start().ok
    assert not a.smoke(ModelProfile('m', 'm', 'm.gguf'), str(tmp_path)).ok


def test_controller_stops_only_the_selected_frontend(monkeypatch):
    import src.controller as module
    stop = Mock(return_value={'success': True, 'message': 'stopped'})
    monkeypatch.setattr(module, 'supervisor', SimpleNamespace(stop=stop))
    controller = module.StationController()
    controller.frontends = {'oc': {'type': 'gateway', 'runtime_id': 'openclaw'}}
    assert controller.stop_frontend('oc')['Success']
    stop.assert_called_once_with('frontend:oc')


def test_controller_dispatches_openclaw_gateway_to_adapter(monkeypatch):
    import src.controller as module
    from src.agents.base import Result, Support
    controller = module.StationController()
    controller.frontends = {'oc': {'id': 'oc', 'type': 'gateway', 'runtime_id': 'openclaw', 'adapter_launch': True, 'status': 'INSTALLED'}}
    controller.select_frontend = lambda id: {'Success': True}
    monkeypatch.setattr(module, 'gpu_mode_manager', SimpleNamespace(get_active_model_profile=lambda: None))
    launch = Mock(return_value=Result(Support.SUPPORTED, 'started'))
    controller.adapters['openclaw'].launch_frontend = launch
    assert controller.launch_frontend('oc')['Success']
    launch.assert_called_once()
