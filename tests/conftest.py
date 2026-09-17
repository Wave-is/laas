"""Never let tests write to the operator's settings or start real agents."""
import os
from pathlib import Path
import sys
import tempfile
import atexit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
_sandbox = tempfile.TemporaryDirectory(prefix='station-tests-')
os.environ['LOCAL_AGENT_STATION_HOME'] = _sandbox.name
os.environ['APPDATA'] = str(Path(_sandbox.name) / 'legacy-appdata')
atexit.register(_sandbox.cleanup)

# Tests assert the Russian source texts.
os.environ['LOCAL_AGENT_STATION_LANGUAGE'] = 'ru'
