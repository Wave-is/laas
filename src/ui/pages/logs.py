"""Журналы page (in development)."""
import customtkinter as ctk
from ...i18n import tr
from ..common import MUTED


class LogsPage:
    def _build_logs(self):
        page = self.page('logs')
        card = self.card(page, tr('Журналы'), tr('Журналы сервера моделей, агентов и Station с фильтром ошибок и сбор диагностики.'))
        ctk.CTkLabel(card, text=tr('Раздел в разработке.'), text_color=MUTED, anchor='w').pack(fill='x', padx=20, pady=(0, 14))
