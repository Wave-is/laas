from pathlib import Path
from unittest.mock import MagicMock
from src.config import config, DEFAULT_SETTINGS
from src.paths import default_logs_dir, logs_dir
from src.model_server import default_models_dir, models_dir
from src.ui.pages.monitoring import alert_settings, DEFAULT_THRESHOLD
from src.ui.agent_controls import frontend_action_state


def test_default_settings_threshold_and_tray():
    # Overheat alert threshold must default to 90
    assert DEFAULT_SETTINGS['monitoring_alert_threshold_c'] == 90
    assert DEFAULT_THRESHOLD == 90
    enabled, threshold = alert_settings()
    assert threshold == 90.0

    # Tray style must default to dual_tile, not two_icons
    assert DEFAULT_SETTINGS['tray_style'] == 'dual_tile'


def test_default_models_folder():
    expected_drive = 'D:\\LLM' if Path('D:/').exists() else 'C:\\LLM'
    assert DEFAULT_SETTINGS['models_dir'] == ''
    assert str(default_models_dir()) == expected_drive
    assert str(models_dir()) == expected_drive


def test_logs_dir_configurable(tmp_path):
    # When empty, returns default_logs_dir()
    config.set('logs_dir', '')
    assert logs_dir() == default_logs_dir()

    # When set to custom folder, resolves custom path
    custom = tmp_path / 'custom_logs'
    config.set('logs_dir', str(custom))
    try:
        assert logs_dir() == custom
        assert custom.is_dir()
    finally:
        config.set('logs_dir', '')


def test_agent_frontend_auto_selection():
    # If preferred frontend is NOT installed, but terminal IS installed:
    frontends = {
        'qwen-desktop': {'status': 'NOT INSTALLED', 'runtime_id': 'qwen-code'},
        'qwen-terminal': {'status': 'INSTALLED', 'runtime_id': 'qwen-code'},
    }
    choices = ['qwen-desktop', 'qwen-terminal']
    preferred = 'qwen-desktop'

    def is_installed(fid):
        return frontends.get(fid, {}).get('status') in ('INSTALLED', 'SUPPORTED (experimental)')

    if preferred in choices and is_installed(preferred):
        selected = preferred
    else:
        selected = next((id for id in choices if is_installed(id)), preferred)

    # Must select qwen-terminal because qwen-desktop is not installed!
    assert selected == 'qwen-terminal'
    # And frontend_action_state for installed frontend must allow starting
    actions = frontend_action_state(frontends['qwen-terminal'], running=False)
    assert actions['start'] is True
