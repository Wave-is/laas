"""Interface languages: English, Russian, Ukrainian.

The Russian source text is the message id: ``tr('Модель «{name}» загружена', name=model.name)``.
Translations live in ``locales/<lang>/*.json`` as ``{"<Russian text>": "<translation>"}``; every
file in the folder is merged, so separate modules keep separate catalogs. Missing translations
fall back to the Russian text. The language is read from settings when Station starts.
"""
import json
import locale
import os
from pathlib import Path

LANGUAGES = {'en': 'English', 'ru': 'Русский', 'uk': 'Українська'}
SOURCE_LANGUAGE = 'ru'


def locales_dir() -> Path:
    import sys
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parent.parent))
    return base / 'locales'


def system_language():
    """Default for a new installation: the Windows UI language when supported, otherwise English."""
    try:
        if os.name == 'nt':
            import ctypes
            name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), '')
        else:
            name = locale.getlocale()[0] or ''
    except Exception:
        name = ''
    code = name.split('_')[0].lower()
    return code if code in LANGUAGES else 'en'


_catalogs = {}


def catalog(language):
    if language not in _catalogs:
        merged = {}
        folder = locales_dir() / language
        for path in sorted(folder.glob('*.json')) if folder.is_dir() else []:
            merged.update(json.loads(path.read_text(encoding='utf-8')))
        _catalogs[language] = merged
    return _catalogs[language]


def current_language():
    forced = os.environ.get('LOCAL_AGENT_STATION_LANGUAGE')
    if forced in LANGUAGES:
        return forced
    try:
        from .config import config
        value = config.get('language')
    except Exception:
        value = None
    return value if value in LANGUAGES else SOURCE_LANGUAGE


def tr(text, **values):
    language = current_language()
    translated = text if language == SOURCE_LANGUAGE else catalog(language).get(text, text)
    return translated.format(**values) if values else translated
