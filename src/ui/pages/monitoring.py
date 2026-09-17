"""Мониторинг page (in development)."""
import customtkinter as ctk
from ...i18n import tr
from ..common import MUTED


class MonitoringPage:
    def _build_monitoring(self):
        page = self.page('monitoring')
        card = self.card(page, tr('Мониторинг'), tr('История температуры, загрузки, видеопамяти и мощности GPU, запросы к серверу моделей и предупреждения о перегреве.'))
        ctk.CTkLabel(card, text=tr('Раздел в разработке.'), text_color=MUTED, anchor='w').pack(fill='x', padx=20, pady=(0, 14))
