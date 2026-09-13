"""Service forms, persistence, local ownership and real loopback-only HTTP checks."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from types import SimpleNamespace
import threading
from unittest.mock import Mock
import pytest
import requests
from src.service_profiles import profile_from_form, service_actions, status_text, http_url
from src.shared_services import SharedServices
from src.storage import atomic_write, digest, ConfigurationConflict
from src.validation import validate_registry
import src.shared_services as module


def remote(**extra):
    return {'id': 'worker', 'name': 'Image server', 'type': 'remote', 'kind': 'http',
        'url': 'http://127.0.0.1:8000', 'health_url': 'http://127.0.0.1:8000/health',
        'monitor_enabled': False, **extra}


@pytest.fixture
def services(tmp_path, monkeypatch):
    manager = SharedServices(tmp_path / 'services.yaml')
    supervisor = Mock()
    supervisor.status.return_value = {'running': False, 'owned': False, 'pid': None}
    monkeypatch.setattr(module, 'supervisor', supervisor)
    return manager, supervisor


def test_comfy_form_derives_check_url_without_starting_or_polling(services, monkeypatch):
    manager, supervisor = services
    network = Mock(side_effect=AssertionError('Saving must not contact server'))
    monkeypatch.setattr(module.requests, 'get', network)
    profile = profile_from_form(name='ComfyUI', kind='comfyui', location='remote', url='http://example.test:8188/')
    assert profile['health_url'] == 'http://example.test:8188/system_stats'
    assert profile['monitor_enabled'] is False
    manager.save(profile, '')
    assert manager.poll()[profile['id']]['health'] == 'UNKNOWN'
    network.assert_not_called()
    supervisor.start.assert_not_called()
    supervisor.stop.assert_not_called()


def test_http_form_keeps_explicit_health_path_and_advanced_fields():
    existing = remote(token_reference='worker/token', custom_note='keep')
    result = profile_from_form(existing, name='Worker', kind='http', location='remote',
        url='http://example.test:8000', health_url='http://example.test:8000/health')
    assert result['id'] == existing['id'] and result['token_reference'] == 'worker/token'
    assert result['health_url'].endswith(':8000/health') and result['custom_note'] == 'keep'


@pytest.mark.parametrize('url', ['localhost:8188', 'file:///tmp/x', 'http://u:p@example.test',
    'http://host:99999', 'http://host:abc', 'http://host/a b'])
def test_bad_service_addresses_rejected(url):
    with pytest.raises(ValueError):
        http_url(url)


def test_local_form_preserves_argument_boundaries(tmp_path):
    exe = tmp_path / 'runtime exe.exe'
    exe.write_bytes(b'test')
    profile = profile_from_form(name='Local', location='local', kind='http', executable=str(exe),
        working_directory=str(tmp_path), arguments='C:\\My Comfy\\main.py\n--listen\n127.0.0.1')
    assert profile['arguments'] == ['C:\\My Comfy\\main.py', '--listen', '127.0.0.1']
    assert profile['url'] == '' and 'start' in service_actions(profile)


def test_concurrent_edit_and_backup_preserve_other_profiles(services):
    manager, _ = services
    manager.save(remote(), '')
    original, expected = manager.edit_snapshot('worker')
    manager.save(remote(name='Edited'), expected)
    assert list(manager.path.parent.glob('backups/*'))
    with pytest.raises(ConfigurationConflict):
        manager.save(dict(original, name='Stale editor'), expected)
    assert manager.profiles()['worker']['name'] == 'Edited'
    manager.save(remote(id='second'), digest(manager.path))
    manager.remove('second', digest(manager.path))
    assert list(manager.profiles()) == ['worker']


def test_remote_stop_cannot_stop_a_local_process(services):
    manager, supervisor = services
    manager.save(remote(), '')
    assert not manager.stop('worker')['Success']
    supervisor.stop.assert_not_called()
    assert 'start' not in service_actions(remote()) and 'stop' not in service_actions(remote())
    assert {'check', 'open', 'copy', 'edit'} == set(service_actions(remote()))


def test_running_local_profile_cannot_be_changed_or_removed(services):
    manager, supervisor = services
    manager.save(remote(type='local'), '')
    supervisor.status.return_value = {'running': True, 'owned': True}
    with pytest.raises(ValueError, match='остановите'):
        manager.save(remote(type='local', name='Changed'), digest(manager.path))
    with pytest.raises(ValueError, match='остановите'):
        manager.remove('worker', digest(manager.path))


def test_reserved_backend_id_cannot_collide():
    with pytest.raises(ValueError):
        validate_registry('services.yaml', [remote(id='llama-swap')])


@contextmanager
def http_fixture(payload, status=200):
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(payload)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_real_http_manual_check_does_not_enable_background_poll(services):
    manager, supervisor = services
    with http_fixture(b'{"status":"ok"}') as (url, calls):
        manager.save(remote(url=url, health_url=url + '/health'), '')
        manager.poll()
        assert not calls
        assert manager.check('worker')['Success']
        manager.poll()
        assert calls == ['/health']
        state = manager.status('worker')
        title, detail = status_text(state)
        assert title == 'Проверки выключены' and 'Последняя проверка' in detail
    supervisor.start.assert_not_called()


@pytest.mark.parametrize('payload, status, ready', [(b'{"system":{},"devices":[]}', 200, True),
    (b'<html>Login</html>', 200, False), (b'{}', 200, False), (b'{}', 503, False)])
def test_comfy_verifies_response_shape_over_real_http(services, payload, status, ready):
    manager, _ = services
    with http_fixture(payload, status) as (url, calls):
        manager.save(remote(kind='comfyui', url=url, health_url=url + '/system_stats'), '')
        assert manager.check('worker')['Success'] is ready
        assert calls == ['/system_stats']


def test_enabled_monitor_is_cached_for_thirty_seconds_and_invalidated(services, monkeypatch):
    manager, _ = services
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    probe = Mock(return_value={'health': 'READY'})
    monkeypatch.setattr(manager, '_probe', probe)
    manager.save(remote(monitor_enabled=True), '')
    manager.poll()
    manager.poll()
    assert probe.call_count == 1
    clock[0] += 31
    manager.poll()
    assert probe.call_count == 2
    manager.save(remote(monitor_enabled=False, health_url='http://127.0.0.1:9999/health'), digest(manager.path))
    assert manager.status('worker')['health'] == 'UNKNOWN'
    assert probe.call_count == 2


def test_timeout_has_actionable_message(services, monkeypatch):
    manager, _ = services
    manager.save(remote(), '')
    monkeypatch.setattr(module.requests, 'get', Mock(side_effect=requests.Timeout()))
    result = manager.check('worker')
    assert not result['Success'] and 'занят' in result['Message']


def test_local_start_and_stop_use_only_supervisor_identity(services, tmp_path):
    manager, supervisor = services
    exe = tmp_path / 'python.exe'
    exe.write_bytes(b'fixture')
    profile = profile_from_form(name='Local', kind='http', location='local', executable=str(exe), arguments='arg with spaces')
    manager.save(profile, '')
    supervisor.start.return_value = {'running': True}
    supervisor.stop.return_value = {'success': True}
    assert manager.start(profile['id'])['Success']
    supervisor.start.assert_called_once_with('service:' + profile['id'], [str(exe), 'arg with spaces'], cwd='', env={})
    assert manager.stop(profile['id'])['Success']
    supervisor.stop.assert_called_once_with('service:' + profile['id'])
