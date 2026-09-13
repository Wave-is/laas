import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from src.i18n import i18n, t

def test_locales_completeness():
    required_keys = [
        "app_title", "status_tab", "models_tab", "hardware_tab", "settings_tab",
        "btn_switch_tcc_wddm", "tcc_warning_body", "station_mode", "btn_unload_vram"
    ]
    for lang in ["en", "ru", "uk"]:
        i18n.set_language(lang)
        for k in required_keys:
            val = t(k)
            assert val != k, f"Missing translation for '{k}' in language '{lang}'"
