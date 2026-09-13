"""Absent components must still expose installation from the official product."""
from pathlib import Path
from unittest.mock import Mock
from src.controller import StationController


def test_missing_agents_keep_official_installation_actions(monkeypatch):
    import webbrowser
    controller = StationController()
    opened = Mock(return_value=True)
    monkeypatch.setattr(webbrowser, 'open', opened)
    expected = {
        'pi': 'https://github.com/earendil-works/pi/releases',
        'openclaw': 'https://github.com/openclaw/openclaw/releases',
        'qwen-code': 'https://github.com/QwenLM/qwen-code/releases',
        'hermes': 'https://github.com/NousResearch/hermes-agent/releases',
        'aider': 'https://github.com/Aider-AI/aider/releases',
    }
    for id, url in expected.items():
        controller.adapters[id].command = []
        options = controller.installation_options(id)
        assert options[0]['missing'] and options[0]['label'].startswith('Установить')
        assert controller.open_installation_page(id)['Success']
        opened.assert_called_with(url, new=2)


def test_installed_cli_does_not_hide_missing_desktop_installation():
    controller = StationController()
    adapter = controller.adapters['qwen-code']
    adapter.command = ['qwen.exe']
    adapter.frontends = [{'type': 'desktop', 'status': 'NOT INSTALLED'}]
    options = controller.installation_options('qwen-code')
    assert len(options) == 2 and options[1]['missing']
    assert 'Desktop' in options[1]['label']
    adapter.frontends[0]['status'] = 'INSTALLED'
    assert len(controller.installation_options('qwen-code')) == 1


def test_portable_hermes_desktop_is_not_reported_missing(tmp_path, monkeypatch):
    import src.agents.hermes.adapter as module
    desktop = tmp_path / 'apps/desktop/release/win-unpacked/Hermes.exe'
    desktop.parent.mkdir(parents=True); desktop.write_bytes(b'fixture')
    monkeypatch.setattr(module, 'registered_executables', lambda name: [])
    monkeypatch.setattr(module.shutil, 'which', lambda name: None)
    adapter = module.HermesAdapter(); adapter.settings = {'source_root': str(tmp_path)}
    adapter.detect()
    frontend = next(f for f in adapter.frontends if f['type'] == 'desktop')
    assert frontend['status'] == 'INSTALLED' and Path(frontend['executable']) == desktop
