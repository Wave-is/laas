"""
i18n.py
Localization and Multi-language support (RU, UK, EN) with live switching.
"""
import json
import pathlib
from .config import config

LOCALES_DIR = pathlib.Path(__file__).parent.parent / "locales"

class I18nManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(I18nManager, cls).__new__(cls)
            cls._instance._locales = {}
            cls._instance._subscribers = []
            cls._instance._load_all()
        return cls._instance

    def _load_all(self):
        for lang in ["en", "ru", "uk"]:
            p = LOCALES_DIR / f"{lang}.json"
            if p.exists():
                try:
                    self._locales[lang] = json.loads(p.read_text(encoding="utf-8"))
                except Exception:
                    self._locales[lang] = {}

    @property
    def current_language(self) -> str:
        return config.get("language", "ru")

    def set_language(self, lang: str):
        if lang in ["en", "ru", "uk"] and lang != self.current_language:
            config.set("language", lang)
            self._notify_subscribers()

    def subscribe(self, callback):
        if callback not in self._subscribers:
            self._subscribers.append(callback)

    def unsubscribe(self, callback):
        if callback in self._subscribers:
            self._subscribers.remove(callback)

    def _notify_subscribers(self):
        for cb in self._subscribers:
            try:
                cb()
            except Exception:
                pass

    def t(self, key: str, default: str = None) -> str:
        lang = self.current_language
        text = self._locales.get(lang, {}).get(key)
        if text is None:
            text = self._locales.get("en", {}).get(key)
        if text is None:
            return default if default is not None else key
        return text

i18n = I18nManager()
t = i18n.t
