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
        self.station_update_status.pack(fill='x', padx=20, pady=(0, 6))

        self.station_update_progress = ctk.CTkProgressBar(card, height=8, progress_color=ACCENT)
        self.station_update_progress.set(0)

        self.station_buttons_row = self.row(card)
        self.station_btn_check = self.button(self.station_buttons_row, tr('Проверить обновления Station'),
            self._check_station_updates, primary=True, width=220)
        self.station_btn_download = self.button(self.station_buttons_row, tr('Скачать обновление'),
            self._download_station_update, width=170)
        self.station_btn_apply = self.button(self.station_buttons_row, tr('Перезапустить и обновить'),
            self._apply_station_update, primary=True, width=200)
        self.station_btn_cancel = self.button(self.station_buttons_row, tr('Отмена загрузки'),
            self._cancel_station_download, width=150)
        self.station_btn_releases = self.button(self.station_buttons_row, tr('Официальные релизы ↗'),
            self._open_station_releases, width=170)

        sw_row = self.row(card)
        self.station_auto_check_var = ctk.BooleanVar(value=config.get('app_update_check', True))
        ctk.CTkSwitch(sw_row, text=tr('Автоматически проверять обновления'), variable=self.station_auto_check_var,
            command=self._on_toggle_auto_check).pack(side='left', padx=(0, 20), pady=(4, 8))
        self.station_auto_download_var = ctk.BooleanVar(value=config.get('app_update_auto_download', False))
        ctk.CTkSwitch(sw_row, text=tr('Автоматически скачивать обновления'), variable=self.station_auto_download_var,
            command=self._on_toggle_auto_download).pack(side='left', padx=(0, 20), pady=(4, 8))

        self._station_release_url = None
        app_updates.update_manager.subscribe(self._on_station_update_event)
        self._sync_station_update_ui(app_updates.update_manager.state, app_updates.update_manager.get_status_dict())

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
    def _on_station_update_event(self, state, data):
        try:
            self.after(0, self._sync_station_update_ui, state, data)
        except Exception:
            pass

    def _sync_station_update_ui(self, state, data):
        for btn in (self.station_btn_download, self.station_btn_apply, self.station_btn_cancel):
            btn.pack_forget()

        rel = data.get('release') or {}
        if rel and rel.get('url'):
            self._station_release_url = rel['url']
        else:
            self._station_release_url = None

        if state == app_updates.UpdateState.CHECKING:
            self.station_btn_check.configure(state='disabled')
            self.station_update_status.configure(text=tr('Проверка…'), text_color=MUTED)
            self.station_update_progress.pack_forget()
        elif state == app_updates.UpdateState.AVAILABLE:
            self.station_btn_check.configure(state='normal')
            ver = rel.get('tag_name', '').lstrip('v') or rel.get('version', '')
            size_mb = (rel.get('installer_size') or 0) / (1024 * 1024)
            msg = tr('Доступна новая версия Local Agent AI Station') + f': v{ver}'
            if size_mb > 0:
                msg = msg + ' (' + tr('Размер обновления: {size:.1f} МБ', size=size_mb) + ')'
            self.station_update_status.configure(text=msg, text_color=ACCENT)
            self.station_update_progress.pack_forget()
            self.station_btn_download.pack(side='left', padx=(0, 10), pady=(5, 6))
        elif state == app_updates.UpdateState.DOWNLOADING:
            self.station_btn_check.configure(state='disabled')
            progress = data.get('progress', 0.0)
            self.station_update_progress.pack(fill='x', padx=20, pady=(0, 8))
            self.station_update_progress.set(progress)
            dl = data.get('downloaded_bytes', 0) / (1024 * 1024)
            tot = data.get('total_bytes', 1) / (1024 * 1024)
            pct = int(progress * 100)
            self.station_update_status.configure(
                text=tr('Скачано: {downloaded:.1f} из {total:.1f} МБ ({pct}%)', downloaded=dl, total=tot, pct=pct),
                text_color=MUTED)
            self.station_btn_cancel.pack(side='left', padx=(0, 10), pady=(5, 6))
        elif state == app_updates.UpdateState.READY:
            self.station_btn_check.configure(state='normal')
            self.station_update_progress.pack(fill='x', padx=20, pady=(0, 8))
            self.station_update_progress.set(1.0)
            self.station_update_status.configure(text=tr('Обновление готово к установке'), text_color=ACCENT)
            self.station_btn_apply.pack(side='left', padx=(0, 10), pady=(5, 6))
        elif state == app_updates.UpdateState.INSTALLING:
            self.station_btn_check.configure(state='disabled')
            self.station_update_status.configure(text=tr('Обновление запущено. Приложение перезапускается…'), text_color=ACCENT)
        elif state == app_updates.UpdateState.ERROR:
            self.station_btn_check.configure(state='normal')
            err = data.get('error') or ''
            self.station_update_status.configure(text=tr('Ошибка обновления: {error}', error=err), text_color=WARNING)
            self.station_update_progress.pack_forget()
        else:
            self.station_btn_check.configure(state='normal')
            self.station_update_progress.pack_forget()

    def _check_station_updates(self):
        app_updates.update_manager.check_updates(background=True)

    def _download_station_update(self):
        app_updates.update_manager.start_download()

    def _cancel_station_download(self):
        app_updates.update_manager.cancel_download()

    def _apply_station_update(self):
        success, msg = app_updates.update_manager.apply_update(silent=True)
        if success:
            if hasattr(self, 'quit_app'):
                self.after(500, self.quit_app)
        else:
            self.station_update_status.configure(text=msg, text_color=WARNING)

    def _open_station_releases(self):
        webbrowser.open(self._station_release_url or 'https://github.com/Wave-is/laas/releases')

    def _on_toggle_auto_check(self):
        val = bool(self.station_auto_check_var.get())
        config.update({'app_update_check': val})

    def _on_toggle_auto_download(self):
        val = bool(self.station_auto_download_var.get())
        config.update({'app_update_auto_download': val})

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
