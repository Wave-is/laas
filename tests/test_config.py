import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from src.config import config

def test_config_get_set():
    orig_lang = config.get("language")
    config.set("language", "en")
    assert config.get("language") == "en"
    config.set("language", orig_lang)
    assert config.get("language") == orig_lang
