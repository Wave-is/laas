"""Unit tests for UpdateManager and OTA update capabilities."""
import hashlib
from pathlib import Path
import tempfile
import urllib.request
import pytest

from src.app_updates import UpdateManager, compare, parse_semver, check


def test_semver_and_compare():
    assert compare('0.2.0-beta.9', '0.2.0-beta.10') == -1
    assert compare('0.2.0-beta.10', '0.2.0-beta.9') == 1
    assert compare('0.2.0', '0.2.0') == 0


def test_update_manager_initial_state():
    mgr = UpdateManager()
    assert mgr.state == UpdateManager.STATE_IDLE
    assert mgr.release_info is None
    assert mgr.installer_path is None
    assert mgr.progress == 0.0


def test_update_manager_check_updates(monkeypatch):
    mgr = UpdateManager()
    events = []
    mgr.subscribe(lambda state, data: events.append((state, data)))

    fake_releases = [
        {
            'tag_name': 'v0.2.0-beta.99',
            'name': 'Release 0.2.0-beta.99',
            'html_url': 'https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.99',
            'draft': False,
            'published_at': '2026-09-20T12:00:00Z',
            'body': 'Notes for beta 99',
            'assets': [
                {
                    'name': 'LocalAgentAIStation-0.2.0-beta.99-Setup-x64.exe',
                    'size': 1024,
                    'browser_download_url': 'https://example.com/setup.exe',
                },
                {
                    'name': 'SHA256SUMS.txt',
                    'size': 120,
                    'browser_download_url': 'https://example.com/SHA256SUMS.txt',
                },
            ],
        }
    ]

    monkeypatch.setattr('src.app_updates.fetch_json', lambda url, timeout=15: fake_releases)

    from src.config import config
    monkeypatch.setattr(config, 'get', lambda key, default=None: False if key == 'app_update_auto_download' else config._data.get(key, default))

    mgr.check_updates(background=False)
    assert mgr.state == UpdateManager.STATE_AVAILABLE
    assert mgr.release_info is not None
    assert mgr.release_info['version'] == '0.2.0-beta.99'
    assert mgr.release_info['installer_url'] == 'https://example.com/setup.exe'
    assert len(events) >= 2

    # Now test auto_download=True
    monkeypatch.setattr(config, 'get', lambda key, default=None: True if key == 'app_update_auto_download' else config._data.get(key, default))
    mgr2 = UpdateManager()
    monkeypatch.setattr(mgr2, 'start_download', lambda: setattr(mgr2, 'state', UpdateManager.STATE_DOWNLOADING))
    mgr2.check_updates(background=False)
    assert mgr2.state == UpdateManager.STATE_DOWNLOADING


def test_update_manager_apply_update_script(tmp_path, monkeypatch):
    mgr = UpdateManager()
    installer = tmp_path / 'LocalAgentAIStation-Setup-x64.exe'
    installer.write_bytes(b'dummy_installer')
    mgr.installer_path = installer

    executed_cmds = []

    def fake_popen(args, **kwargs):
        executed_cmds.append(args)
        class FakeProc:
            pid = 9999
        return FakeProc()

    monkeypatch.setattr('subprocess.Popen', fake_popen)

    success, msg = mgr.apply_update(silent=True)
    assert success
    assert mgr.state == UpdateManager.STATE_INSTALLING
    script = tmp_path / 'apply_update.ps1'
    assert script.is_file()
    content = script.read_text(encoding='utf-8')
    assert '/SILENT' in content
    assert '/CLOSEAPPLICATIONS' in content
    assert '/CURRENTUSER' in content
    assert str(installer) in content
    assert len(executed_cmds) == 1
    assert 'powershell.exe' in executed_cmds[0][0]


def test_update_manager_download_success(tmp_path, monkeypatch):
    mgr = UpdateManager()
    dummy_installer_bytes = b'fake_exe_content_12345'
    correct_hash = hashlib.sha256(dummy_installer_bytes).hexdigest()

    mgr.release_info = {
        'version': '0.2.0-beta.11',
        'installer_name': 'setup.exe',
        'installer_url': 'https://example.com/setup.exe',
        'installer_size': len(dummy_installer_bytes),
        'checksums_url': 'https://example.com/SHA256SUMS.txt',
    }

    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path))

    class FakeResponse:
        def __init__(self, data):
            self.data = data
            self.headers = {'Content-Length': str(len(data))}
            self.pos = 0

        def read(self, size=-1):
            if self.pos >= len(self.data):
                return b''
            chunk = self.data[self.pos:self.pos + size] if size > 0 else self.data[self.pos:]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=30):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        if 'SHA256SUMS' in url:
            return FakeResponse(f'{correct_hash}  setup.exe\n'.encode('utf-8'))
        return FakeResponse(dummy_installer_bytes)

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)

    mgr.start_download()
    if mgr._download_thread:
        mgr._download_thread.join(timeout=5)

    assert mgr.state == UpdateManager.STATE_READY
    assert mgr.installer_path is not None
    assert mgr.installer_path.is_file()
    assert mgr.installer_path.read_bytes() == dummy_installer_bytes


def test_update_manager_download_checksum_mismatch(tmp_path, monkeypatch):
    mgr = UpdateManager()
    dummy_installer_bytes = b'fake_exe_content_12345'
    wrong_hash = '0000000000000000000000000000000000000000000000000000000000000000'

    mgr.release_info = {
        'version': '0.2.0-beta.11',
        'installer_name': 'setup.exe',
        'installer_url': 'https://example.com/setup.exe',
        'installer_size': len(dummy_installer_bytes),
        'checksums_url': 'https://example.com/SHA256SUMS.txt',
    }

    monkeypatch.setattr('tempfile.gettempdir', lambda: str(tmp_path))

    class FakeResponse:
        def __init__(self, data):
            self.data = data
            self.headers = {'Content-Length': str(len(data))}
            self.pos = 0

        def read(self, size=-1):
            if self.pos >= len(self.data):
                return b''
            chunk = self.data[self.pos:self.pos + size] if size > 0 else self.data[self.pos:]
            self.pos += len(chunk)
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    def fake_urlopen(req, timeout=30):
        url = req.full_url if hasattr(req, 'full_url') else str(req)
        if 'SHA256SUMS' in url:
            return FakeResponse(f'{wrong_hash}  setup.exe\n'.encode('utf-8'))
        return FakeResponse(dummy_installer_bytes)

    monkeypatch.setattr('urllib.request.urlopen', fake_urlopen)

    mgr.start_download()
    if mgr._download_thread:
        mgr._download_thread.join(timeout=5)

    assert mgr.state == UpdateManager.STATE_ERROR
    assert 'не совпадает' in mgr.error_message


def test_ui_sidebar_and_maintenance_state_transitions():
    from src.app_updates import UpdateState
    from unittest.mock import MagicMock

    # Mock ControlCenter sidebar state handling
    class FakeControlCenter:
        def __init__(self):
            self.sidebar_update_btn = MagicMock()
            self.packed = False
            self.sidebar_update_btn.pack = lambda **kw: setattr(self, 'packed', True)
            self.sidebar_update_btn.pack_forget = lambda: setattr(self, 'packed', False)

    from src.ui.control_center import ControlCenter

    cc = FakeControlCenter()

    # AVAILABLE
    data_avail = {'release': {'tag_name': 'v0.2.0-beta.11'}}
    ControlCenter._handle_update_state_change(cc, UpdateState.AVAILABLE, data_avail)
    assert cc.packed
    args, kwargs = cc.sidebar_update_btn.configure.call_args
    assert '0.2.0-beta.11' in kwargs['text']
    assert '📥' in kwargs['text']

    # DOWNLOADING
    data_dl = {'progress': 0.45}
    ControlCenter._handle_update_state_change(cc, UpdateState.DOWNLOADING, data_dl)
    assert cc.packed
    args, kwargs = cc.sidebar_update_btn.configure.call_args
    assert '45%' in kwargs['text']
    assert '⏳' in kwargs['text']

    # READY
    ControlCenter._handle_update_state_change(cc, UpdateState.READY, data_avail)
    assert cc.packed
    args, kwargs = cc.sidebar_update_btn.configure.call_args
    assert '🚀' in kwargs['text']

    # IDLE
    ControlCenter._handle_update_state_change(cc, UpdateState.IDLE, {})
    assert not cc.packed


def test_check_ignores_legacy_3_0_0_alpha():
    releases = [
        {
            'tag_name': 'v3.0.0-alpha.1',
            'name': 'Release 3.0.0-alpha.1',
            'html_url': 'https://github.com/Wave-is/laas/releases/tag/v3.0.0-alpha.1',
            'draft': False,
            'assets': [
                {'name': 'LocalAgentAIStation-3.0.0-alpha.1-Setup-x64.exe', 'browser_download_url': 'http://example.com'},
            ],
        },
        {
            'tag_name': 'v0.2.0-beta.11',
            'name': 'Release 0.2.0-beta.11',
            'html_url': 'https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.11',
            'draft': False,
            'assets': [
                {'name': 'LocalAgentAIStation-0.2.0-beta.11-Setup-x64.exe', 'browser_download_url': 'http://example.com'},
            ],
        }
    ]
    res = check(current='0.2.0-beta.10', fetch=lambda url: releases)
    assert res['Success']
    assert res['Release'] is not None
    assert res['Release']['version'] == '0.2.0-beta.11'



