"""Расписания page (in development)."""
import customtkinter as ctk
from ...i18n import tr
from ..common import MUTED


class SchedulesPage:
    def _build_schedules(self):
        page = self.page('schedules')
        card = self.card(page, tr('Расписания'), tr('Переключение режимов GPU и другие действия по расписанию.'))
        ctk.CTkLabel(card, text=tr('Раздел в разработке.'), text_color=MUTED, anchor='w').pack(fill='x', padx=20, pady=(0, 14))
