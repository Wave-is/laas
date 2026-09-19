"""Portable locations. Importing a module never creates user directories."""
import os
from pathlib import Path

APP_NAME = 'Local Agent AI Station'
from .version import VERSION
SOURCE_DIR = Path(__file__).resolve().parent.parent

def data_dir() -> Path:
    if os.environ.get('LOCAL_AGENT_STATION_HOME'):
        return Path(os.environ['LOCAL_AGENT_STATION_HOME']).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local')) / 'LocalAgentAIStation'
    return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local/share')) / 'local-agent-ai-station'

def default_logs_dir() -> Path:
    return data_dir() / 'logs'

def logs_dir() -> Path:
    try:
        from .config import config
        custom = config.get('logs_dir')
        if custom:
            p = Path(custom)
            p.mkdir(parents=True, exist_ok=True)
            return p
    except Exception:
        pass
    p = default_logs_dir()
    p.mkdir(parents=True, exist_ok=True)
    return p

def legacy_dir() -> Path:
    return Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'HermesStation2'

def resource_path(name: str) -> Path:
    import sys
    return Path(getattr(sys, '_MEIPASS', SOURCE_DIR)) / name
