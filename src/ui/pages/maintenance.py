"""Обслуживание page (in development)."""
import customtkinter as ctk
from ...i18n import tr
from ..common import MUTED


class MaintenancePage:
    def _build_maintenance(self):
        page = self.page('maintenance')
        card = self.card(page, tr('Обслуживание'), tr('Обновления движка и Station, резервные копии и перенос настроек.'))
        ctk.CTkLabel(card, text=tr('Раздел в разработке.'), text_color=MUTED, anchor='w').pack(fill='x', padx=20, pady=(0, 14))
