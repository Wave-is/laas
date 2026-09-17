"""Model server watchdog: Settings section, background checks and event history."""
import logging
import tkinter as tk
import customtkinter as ctk
from ...config import config
from ...i18n import tr
from ...watchdog import create_watchdog
from ..common import MUTED

EVENT_TITLES = {
    'crash': lambda: tr('Сбой'),
    'hang': lambda: tr('Завис'),
    'restarted': lambda: tr('Перезапущен'),
    'restart_failed': lambda: tr('Ошибка перезапуска'),
    'gave_up': lambda: tr('Автоперезапуск остановлен'),
}


def describe_event(entry):
    if not entry:
        return tr('Сбоев сервера моделей не было.')
    title = EVENT_TITLES.get(entry.get('event'), lambda: entry.get('event', ''))()
    return tr('Последнее событие ({time}): {title}. {message}', time=entry.get('time', '').replace('T', ' '),
        title=title, message=entry.get('message', ''))


class WatchdogSection:
    def _settings_section_watchdog(self, page):
        if getattr(self, 'watchdog', None) is None:
            self.watchdog = create_watchdog(is_busy=lambda: self.busy,
                notify=lambda message: self.call_in_ui(lambda: self._watchdog_notify(message)))
            self.poll_hooks.append(self._watchdog_poll)
            self.telemetry_hooks.append(lambda *_: self._refresh_watchdog_status())
        card = self.card(page, tr('Автоперезапуск сервера моделей'), tr('Если сервер моделей, запущенный Station, неожиданно завершится, '
            'Station запустит его снова с той же моделью. Не больше 3 попыток за 10 минут. '
            'Сервер, остановленный вами или запущенный не Station, не перезапускается.'))
        self.watchdog_var = tk.BooleanVar(value=config.get('watchdog_enabled', True) is not False)
        ctk.CTkCheckBox(card, text=tr('Перезапускать сервер моделей после сбоя'), variable=self.watchdog_var,
            command=lambda: config.update({'watchdog_enabled': bool(self.watchdog_var.get())})).pack(anchor='w', padx=20, pady=(4, 0))
        self.watchdog_status_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.watchdog_status_label.pack(fill='x', padx=20, pady=(4, 0))
        row = self.row(card)
        self.button(row, tr('Журнал перезапусков'), self._show_watchdog_history, width=200)
        self._refresh_watchdog_status()

    def _watchdog_poll(self, topology, backend_info, running):
        self.watchdog.poll(backend_info)

    def _watchdog_notify(self, message):
        self.notify(message)
        self._refresh_watchdog_status()

    def _refresh_watchdog_status(self):
        label = getattr(self, 'watchdog_status_label', None)
        if label is not None and label.winfo_exists():
            label.configure(text=describe_event(self.watchdog.last_event))

    def _show_watchdog_history(self):
        try:
            entries = self.watchdog.history(200)
        except OSError as exc:
            logging.getLogger(__name__).exception('Cannot read watchdog history')
            entries, error = [], str(exc)
        else:
            error = ''
        lines = [f"{e.get('time', '').replace('T', ' ')}  {EVENT_TITLES.get(e.get('event'), lambda: e.get('event', ''))()}"
                 + (f"  [{e['model']}]" if e.get('model') not in (None, '', 'none') else '') + f"\n    {e.get('message', '')}"
                 for e in reversed(entries)]
        text = '\n'.join(lines) if lines else (error or tr('Событий пока нет. Здесь появятся сбои и перезапуски сервера моделей.'))
        self.review(tr('Журнал перезапусков сервера моделей'), text)
