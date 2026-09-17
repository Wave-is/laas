"""Обслуживание page: engine updates, Station updates, backup/restore."""
import logging
import webbrowser
from pathlib import Path

import customtkinter as ctk

from ...i18n import tr
from ...paths import VERSION
from ... import app_updates, backup, engine_updates, model_server
from ...config import config
from ..common import MUTED, ACCENT, WARNING

log = logging.getLogger(__name__)


class MaintenancePage:
    def _build_maintenance(self):
        page = self.page('maintenance')

        # ── Engine updates ──
        card = self.card(page, tr('Движок (llama.cpp и llama-swap)'),
            tr('Обновление серверных программ, которые запускают модели. Station не скачивает и не устанавливает ничего без нажатия кнопки. Работающий сервер моделей не затрагивается.'))
        self.engine_status = ctk.CTkLabel(card, text=tr('Нажмите «Проверить обновления», чтобы узнать текущие и доступные версии.'),
            anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.engine_status.pack(fill='x', padx=20, pady=(0, 4))
        self.engine_detail = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900,
            font=('Consolas', 12))
        self.engine_detail.pack(fill='x', padx=20, pady=(0, 8))
        row = self.row(card)
        self.button(row, tr('Проверить обновления'), self._check_engine_updates, primary=True, width=200)
        self.button(row, tr('Установить обновления'), self._install_engine_updates, width=200)
        self.button(row, tr('Откатить версию'), self._rollback_engine, width=160)
        row = self.row(card)
        self.button(row, tr('Страница llama.cpp ↗'), lambda: webbrowser.open(engine_updates.RELEASES_PAGE[engine_updates.LLAMA]), width=180)
        self.button(row, tr('Страница llama-swap ↗'), lambda: webbrowser.open(engine_updates.RELEASES_PAGE[engine_updates.SWAP]), width=180)
        self._engine_updates = None

        # ── Station updates ──
        card = self.card(page, tr('Обновления Station'),
            tr('Проверка новых версий Local Agent AI Station на GitHub. Текущая версия: {version}.', version=VERSION))
        self.station_update_status = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.station_update_status.pack(fill='x', padx=20, pady=(0, 8))
        row = self.row(card)
        self.button(row, tr('Проверить обновления Station'), self._check_station_updates, primary=True, width=230)
        self._station_release_url = None

        # ── Backups ──
        card = self.card(page, tr('Резервные копии и перенос'),
            tr('Экспортируйте настройки Station и профили моделей в ZIP-файл. Импорт восстанавливает конфигурацию из ранее созданного архива, сохраняя локальные пути и секреты.'))
        self.backup_status = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.backup_status.pack(fill='x', padx=20, pady=(0, 8))
        row = self.row(card)
        self.button(row, tr('Экспортировать настройки'), self._export_backup, primary=True, width=200)
        self.button(row, tr('Импортировать из архива'), self._import_backup, width=200)
        self._refresh_backup_list()

    # ── Engine ──
    def _check_engine_updates(self):
        def action():
            current = engine_updates.installed()
            updates = engine_updates.check_updates(current)
            return current, updates
        def done(result):
            current, updates = result
            lines = []
            for component, info in current.items():
                lines.append(f'{component}: {info.get("label") or tr("не установлен")} ({info.get("executable") or "—"})')
            self.engine_detail.configure(text='\n'.join(lines))
            available = {c: u for c, u in updates.items() if u.get('available')}
            self._engine_updates = updates
            if available:
                names = ', '.join(f'{c} {u["available"]["label"]}' for c, u in available.items())
                self.engine_status.configure(text=tr('Доступны обновления: {names}. Нажмите «Установить обновления».', names=names),
                    text_color=ACCENT)
            else:
                self.engine_status.configure(text=tr('Установлена последняя версия движка.'), text_color=ACCENT)
        self.worker(action, done, label=tr('Проверка обновлений движка'))

    def _install_engine_updates(self):
        updates = self._engine_updates
        if not updates:
            self.status_label.configure(text=tr('Сначала нажмите «Проверить обновления».'), text_color=WARNING)
            return
        available = {c: u for c, u in updates.items() if u.get('available')}
        if not available:
            self.status_label.configure(text=tr('Обновлений нет.'), text_color=MUTED)
            return
        current = engine_updates.installed()
        def action():
            return engine_updates.install_updates(current, updates, list(available))
        def done(result):
            self._engine_updates = None
            self.engine_status.configure(text=result.get('Message', tr('Готово')),
                text_color=ACCENT if result.get('Success') else WARNING)
        self.worker(action, done, label=tr('Установка обновлений движка'))

    def _rollback_engine(self):
        runtime = model_server.runtime_dir()
        bundled = model_server.bundled_runtime_dir()
        choices = engine_updates.rollback_choices(runtime, bundled)
        if not choices:
            self.status_label.configure(text=tr('Нет доступных версий для отката.'), text_color=MUTED)
            return
        content = '\n'.join(f'{c["label"]} — {c["path"]}' for c in choices)
        def apply():
            target = choices[0]['path']
            engine_updates.select_runtime(str(target), update_config=config.update)
            return {'Success': True, 'Message': tr('Движок переключён на: {path}. Перезапустите сервер моделей.', path=target)}
        self.review(tr('Откат версии движка'), content, apply, label=tr('Откат движка'))

    # ── Station updates ──
    def _check_station_updates(self):
        def action():
            return app_updates.check()
        def done(result):
            self.station_update_status.configure(text=result['Message'],
                text_color=ACCENT if result['Success'] else WARNING)
            release = result.get('Release')
            if release and release.get('url'):
                self._station_release_url = release['url']
                self.station_update_status.configure(
                    text=result['Message'] + '\n' + tr('Нажмите на ссылку: {url}', url=release['url']),
                    cursor='hand2')
                self.station_update_status.bind('<Button-1>', lambda e: webbrowser.open(self._station_release_url))
            else:
                self._station_release_url = None
                self.station_update_status.unbind('<Button-1>')
        self.worker(action, done, label=tr('Проверка обновлений Station'))

    # ── Backups ──
    def _refresh_backup_list(self):
        try:
            backups = backup.list_backups(5)
        except Exception:
            backups = []
        if backups:
            lines = [tr('Последние резервные копии:')]
            for b in backups:
                lines.append(f'  {b["stamp"]}  {b["size"]}')
            self.backup_status.configure(text='\n'.join(lines))
        else:
            self.backup_status.configure(text=tr('Резервных копий пока нет.'))

    def _export_backup(self):
        def action():
            adapters = dict(getattr(self.controller, 'adapters', {}) or {})
            path = backup.export_backup(include_agents=True, adapters=adapters)
            return {'Success': True, 'Message': tr('Резервная копия создана: {path}', path=path), 'path': str(path)}
        def done(result):
            self._refresh_backup_list()
            if result.get('path'):
                self._open_path(Path(result['path']).parent)
        self.worker(action, done, label=tr('Экспорт настроек'))

    def _import_backup(self):
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self, title=tr('Выберите архив резервной копии'),
            filetypes=[(tr('ZIP-архивы'), '*.zip')])
        if not path:
            return
        adapters = dict(getattr(self.controller, 'adapters', {}) or {})
        try:
            preview = backup.preview_import(path, adapters=adapters)
        except Exception as exc:
            self.status_label.configure(text=tr('Ошибка: ') + str(exc)[:200], text_color=WARNING)
            return
        description = backup.describe_preview(preview)
        def apply():
            backup.apply_import(preview)
            return {'Success': True, 'Message': tr('Настройки восстановлены из резервной копии. Перезапустите Station для применения.')}
        self.review(tr('Импорт резервной копии'), description, apply, label=tr('Импорт резервной копии'))
