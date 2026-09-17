"""Журналы page: log viewer with an error filter and the diagnostics bundle."""
import logging
import os
import threading
import time
from pathlib import Path

import customtkinter as ctk

from ... import diagnostics
from ...i18n import tr
from ..common import EDGE, MUTED, PANEL, WARNING

AUTO_REFRESH_MS = 3000


def _size(value):
    if value >= 1024 * 1024:
        return f'{value / 1024 / 1024:.1f} MB'
    if value >= 1024:
        return f'{value / 1024:.0f} KB'
    return f'{value} B'


class LogsPage:
    def _build_logs(self):
        page = self.page('logs')
        card = self.card(page, tr('Журналы'), tr('Журналы сервера моделей, агентов и Station с фильтром ошибок и сбор диагностики.'))
        self.log_sources = []
        self.log_raw = ''
        self.log_state = {'path': None, 'signature': None, 'loading': False, 'rendered_path': None, 'search_job': None}
        self.log_filter_all = tr('Все строки')
        self.log_filter_errors = tr('Только ошибки и предупреждения')

        row = self.row(card)
        ctk.CTkLabel(row, text=tr('Источник'), text_color=MUTED).pack(side='left', padx=(0, 8))
        self.log_source_box = ctk.CTkComboBox(row, values=['—'], width=340, height=36, state='readonly',
            fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL,
            command=lambda _value: self._refresh_log_view(force=True))
        self.log_source_box.pack(side='left', padx=(0, 12), pady=(0, 8))
        self.log_filter_box = ctk.CTkComboBox(row, values=[self.log_filter_all, self.log_filter_errors], width=290, height=36,
            state='readonly', fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL,
            command=lambda _value: self._render_log())
        self.log_filter_box.pack(side='left', pady=(0, 8))
        self.log_filter_box.set(self.log_filter_all)

        row = self.row(card)
        self.log_search = ctk.CTkEntry(row, placeholder_text=tr('Поиск по тексту'), width=340, height=36,
            fg_color='#111b25', border_color=EDGE)
        self.log_search.pack(side='left', padx=(0, 12))
        self.log_search.bind('<KeyRelease>', lambda _event: self._schedule_log_search())
        self.log_auto_refresh = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(row, text=tr('Обновлять каждые 3 с'), variable=self.log_auto_refresh).pack(side='left')

        row = self.row(card)
        self.button(row, tr('Обновить'), lambda: (self._refresh_log_sources(), self._refresh_log_view(force=True)), width=120)
        self.button(row, tr('Открыть файл'), self._open_selected_log, width=130)
        self.button(row, tr('Открыть папку журналов'), lambda: self._open_path(diagnostics.logs_dir()), width=200)
        self.button(row, tr('Собрать диагностику'), self._collect_diagnostics, primary=True, width=190)

        self.log_text = ctk.CTkTextbox(card, height=420, font=('Consolas', 12), wrap='none', fg_color='#0c1218',
            border_color=EDGE, border_width=1)
        self.log_text.pack(fill='x', padx=20, pady=(0, 6))
        self.log_text.tag_config('error', foreground=WARNING)
        self.log_text.configure(state='disabled')
        # The page itself scrolls; keep the wheel inside the log while the pointer is over it.
        inner = getattr(self.log_text, '_textbox', self.log_text)
        inner.bind('<MouseWheel>', self._log_wheel)
        self.log_info = ctk.CTkLabel(card, text='', text_color=MUTED, anchor='w', justify='left', wraplength=860)
        self.log_info.pack(fill='x', padx=20, pady=(0, 14))

        self.page_show_hooks['logs'] = self._logs_page_shown
        self.after(AUTO_REFRESH_MS, self._logs_tick)

    # ------------------------------------------------------------ sources
    def _log_names(self):
        frontends, agents = {}, {}
        controller = getattr(self, 'controller', None)
        if controller is not None:
            frontends = dict(getattr(controller, 'frontends', {}) or {})
            for ident, adapter in dict(getattr(controller, 'adapters', {}) or {}).items():
                try:
                    agents[ident] = adapter.manifest.get('name', ident)
                except Exception:
                    agents[ident] = ident
        return frontends, agents

    def _refresh_log_sources(self):
        frontends, agents = self._log_names()
        try:
            sources = diagnostics.log_sources(frontends=frontends, agents=agents)
        except Exception:
            logging.getLogger(__name__).exception('Cannot list logs')
            sources = []
        counts = {}
        for source in sources:
            counts[source['name']] = counts.get(source['name'], 0) + 1
        for source in sources:
            source['label'] = source['name'] if counts[source['name']] == 1 else f"{source['name']} ({Path(source['path']).name})"
        self.log_sources = sources
        labels = [source['label'] for source in sources] or [tr('Журналов пока нет')]
        self.log_source_box.configure(values=labels)
        current = self.log_state['path']
        chosen = next((s for s in sources if s['path'] == current), sources[0] if sources else None)
        self.log_source_box.set(chosen['label'] if chosen else labels[0])

    def _selected_log(self):
        label = self.log_source_box.get()
        return next((Path(s['path']) for s in self.log_sources if s['label'] == label), None)

    def _logs_page_shown(self):
        self._refresh_log_sources()
        self._refresh_log_view(force=True)

    # ------------------------------------------------------------ reading
    def _refresh_log_view(self, force=False):
        path = self._selected_log()
        if path is None:
            self.log_raw = ''
            self.log_state.update(path=None, signature=None)
            self._render_log()
            self.log_info.configure(text=tr('Журналы появятся после запуска Station, сервера моделей или агентов.'))
            return
        try:
            stat = path.stat()
            signature = (str(path), stat.st_size, stat.st_mtime_ns)
        except OSError:
            signature = (str(path), None, None)
        # A read whose UI callback was dropped (full event queue) must not block refreshes forever.
        loading = self.log_state['loading'] and time.monotonic() - self.log_state['loading'] < 15
        if loading or (not force and signature == self.log_state['signature']):
            return
        self.log_state['loading'] = time.monotonic()

        def read():
            try:
                text, error = diagnostics.read_tail(path), None
            except OSError as exc:
                text, error = '', str(exc)
            self.call_in_ui(lambda: self._log_loaded(path, signature, text, error))
        threading.Thread(target=read, daemon=True).start()

    def _log_loaded(self, path, signature, text, error):
        self.log_state['loading'] = False
        if self._selected_log() != path:
            self._refresh_log_view(force=True)
            return
        self.log_raw = text
        self.log_state.update(path=str(path), signature=signature)
        self._render_log()
        if error:
            self.log_info.configure(text=tr('Не удалось прочитать журнал: {error}', error=error), text_color=WARNING)

    def _schedule_log_search(self):
        if self.log_state['search_job']:
            self.after_cancel(self.log_state['search_job'])
        self.log_state['search_job'] = self.after(300, self._render_log)

    def _render_log(self):
        self.log_state['search_job'] = None
        errors_only = self.log_filter_box.get() == self.log_filter_errors
        query = self.log_search.get()
        total = self.log_raw.count('\n') + (1 if self.log_raw and not self.log_raw.endswith('\n') else 0)
        lines = diagnostics.filter_lines(self.log_raw, errors_only, query)
        text = self.log_text
        same_source = self.log_state['rendered_path'] == self.log_state['path']
        first, last = text.yview()
        at_bottom = not same_source or last >= 0.999
        text.configure(state='normal')
        text.delete('1.0', 'end')
        run, run_error = [], False
        for line in lines:
            error = diagnostics.is_error_line(line)
            if run and error != run_error:
                text.insert('end', '\n'.join(run) + '\n', 'error' if run_error else None)
                run = []
            run_error = error
            run.append(line)
        if run:
            text.insert('end', '\n'.join(run) + '\n', 'error' if run_error else None)
        text.configure(state='disabled')
        if at_bottom:
            text.see('end')
        else:
            text.yview_moveto(first)
        self.log_state['rendered_path'] = self.log_state['path']
        path = self.log_state['path']
        if path:
            try:
                length = os.path.getsize(path)
            except OSError:
                length = 0
            info = tr('Показано строк: {shown} из {total} · размер файла {size} · {path}', shown=len(lines), total=total, size=_size(length), path=path)
            if length > diagnostics.VIEW_LIMIT:
                info += '\n' + tr('Показаны последние 2 МБ журнала. Полный файл: «Открыть файл».')
            self.log_info.configure(text=info, text_color=MUTED)

    def _log_wheel(self, event):
        self.log_text.yview_scroll(int(-event.delta / 120) or (-1 if event.delta > 0 else 1), 'units')
        return 'break'

    def _logs_tick(self):
        try:
            visible = self.pages['logs'].winfo_ismapped() and self.state() != 'withdrawn'
            if self.log_auto_refresh.get() and visible:
                self._refresh_log_view()
        except Exception:
            logging.getLogger(__name__).exception('Log auto-refresh failed')
        self.after(AUTO_REFRESH_MS, self._logs_tick)

    # ------------------------------------------------------------ actions
    def _open_selected_log(self):
        path = self._selected_log()
        if path is None:
            self.status_label.configure(text=tr('Выберите журнал.'), text_color=MUTED)
            return
        self._open_path(path)

    def _collect_diagnostics(self):
        frontends, _agents = self._log_names()
        status = dict(getattr(self.controller, 'agent_status', {}) or {}) or None

        def action():
            path = diagnostics.collect_diagnostics(agent_status=status, frontends=frontends)
            return {'Success': True, 'Message': tr('Диагностика сохранена: {path}', path=path), 'path': str(path)}
        self.worker(action, self._diagnostics_collected, label=tr('Сбор диагностики'))

    def _diagnostics_collected(self, result):
        if result.get('path'):
            self._open_path(Path(result['path']).parent)
