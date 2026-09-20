"""OTA Update Dialog for Local Agent AI Station."""
import logging
import webbrowser
from pathlib import Path
import tkinter as tk
import customtkinter as ctk

from ..i18n import tr
from ..paths import APP_NAME, VERSION, resource_path
from ..app_updates import update_manager, UpdateState
from .common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING

log = logging.getLogger(__name__)


class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title(tr('Обновление Station'))
        self.geometry('640x540')
        self.minsize(580, 480)
        self.configure(fg_color=BG)
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self._on_close)
        self.bind('<Escape>', lambda e: self._on_close())
        self.after(200, self._set_icon)

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        # Header
        ctk.CTkLabel(self, text=tr('Доступна новая версия Local Agent AI Station'),
            font=('Segoe UI', 18, 'bold'), anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(20, 4))

        self.info_label = ctk.CTkLabel(self, text='', font=('Segoe UI', 12), text_color=MUTED, anchor='w', justify='left')
        self.info_label.grid(row=1, column=0, sticky='ew', padx=24, pady=(0, 4))

        self.git_warning_label = ctk.CTkLabel(self,
            text=tr('Запущено из исходного кода (Git). Для обновления используйте git pull или скачайте инсталлятор вручную.'),
            font=('Segoe UI', 11), text_color=WARNING, anchor='w', justify='left', wraplength=590)

        # Changelog
        ctk.CTkLabel(self, text=tr('Что нового:'), font=('Segoe UI', 13, 'bold'), anchor='w').grid(
            row=2, column=0, sticky='ew', padx=24, pady=(6, 4))

        self.notes_box = ctk.CTkScrollableFrame(self, fg_color=PANEL, border_color=EDGE, border_width=1, corner_radius=8)
        self.notes_box.grid(row=3, column=0, sticky='nsew', padx=24, pady=(0, 10))

        self.notes_label = ctk.CTkLabel(self.notes_box, text='', font=('Segoe UI', 12), justify='left',
            anchor='nw', wraplength=560)
        self.notes_label.pack(fill='both', expand=True, padx=12, pady=10)

        # Progress section
        self.progress_frame = ctk.CTkFrame(self, fg_color='transparent')
        self.progress_frame.grid(row=4, column=0, sticky='ew', padx=24, pady=(0, 10))
        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=8, progress_color=ACCENT)
        self.progress_bar.pack(fill='x', pady=(0, 4))
        self.progress_label = ctk.CTkLabel(self.progress_frame, text='', font=('Segoe UI', 11), text_color=MUTED, anchor='w')
        self.progress_label.pack(fill='x')

        # Buttons
        btn_row = ctk.CTkFrame(self, fg_color='transparent')
        btn_row.grid(row=5, column=0, sticky='ew', padx=24, pady=(0, 20))

        self.btn_close = ctk.CTkButton(btn_row, text=tr('Позже'), fg_color=EDGE, hover_color='#364a60',
            text_color=TEXT, height=36, width=100, command=self._on_close)
        self.btn_close.pack(side='left')

        self.btn_releases = ctk.CTkButton(btn_row, text=tr('Официальные релизы ↗'), fg_color='transparent',
            hover_color=EDGE, text_color=MUTED, height=36, width=160, command=self._open_releases)
        self.btn_releases.pack(side='left', padx=(10, 0))

        self.btn_action = ctk.CTkButton(btn_row, text='', height=36, width=190,
            fg_color=ACCENT, text_color=BG, hover_color='#71e2c2', font=('Segoe UI', 12, 'bold'),
            command=self._on_action_click)
        self.btn_action.pack(side='right')

        self._subscribed = True
        update_manager.subscribe(self._on_update_event)

        self._render(update_manager.state, update_manager.get_status_dict())
        self.grab_set()

    def _set_icon(self):
        try:
            icon_path = resource_path('assets/app.ico')
            if icon_path.is_file():
                self.iconbitmap(str(icon_path))
        except Exception:
            pass

    def _on_update_event(self, state, data):
        try:
            self.after(0, self._render, state, data)
        except Exception:
            pass

    def _render(self, state, data):
        rel = data.get('release') or {}
        version = rel.get('tag_name', '').lstrip('v') or rel.get('version', '')
        published = (rel.get('published_at') or '')[:10]
        size_bytes = rel.get('installer_size', 0)
        size_mb = size_bytes / (1024 * 1024) if size_bytes else 0.0

        if version:
            info_text = tr('Версия: {version} (выпущена: {published})', version=version, published=published)
            if size_mb > 0:
                info_text += '  ·  ' + tr('Размер обновления: {size:.1f} МБ', size=size_mb)
            self.info_label.configure(text=info_text)

        body = rel.get('body') or ''
        self.notes_label.configure(text=body if body.strip() else '—')

        if update_manager.is_running_from_source():
            self.git_warning_label.grid(row=1, column=0, sticky='ew', padx=24, pady=(24, 0))
        else:
            self.git_warning_label.grid_forget()

        if state == UpdateState.AVAILABLE:
            self.progress_frame.grid_remove()
            self.btn_action.configure(text=tr('Скачать обновление'), fg_color=ACCENT, text_color=BG, state='normal')
        elif state == UpdateState.DOWNLOADING:
            self.progress_frame.grid()
            progress = data.get('progress', 0.0)
            self.progress_bar.set(progress)
            dl = data.get('downloaded_bytes', 0) / (1024 * 1024)
            tot = data.get('total_bytes', 1) / (1024 * 1024)
            pct = int(progress * 100)
            self.progress_label.configure(text=tr('Скачано: {downloaded:.1f} из {total:.1f} МБ ({pct}%)', downloaded=dl, total=tot, pct=pct), text_color=MUTED)
            self.btn_action.configure(text=tr('Отмена загрузки'), fg_color=EDGE, text_color=TEXT, state='normal')
        elif state == UpdateState.READY:
            self.progress_frame.grid()
            self.progress_bar.set(1.0)
            self.progress_label.configure(text=tr('Обновление готово к установке'), text_color=ACCENT)
            self.btn_action.configure(text=tr('Перезапустить и обновить'), fg_color='#22c55e', text_color='#ffffff', state='normal')
        elif state == UpdateState.INSTALLING:
            self.progress_frame.grid()
            self.progress_label.configure(text=tr('Обновление запущено. Приложение перезапускается…'), text_color=ACCENT)
            self.btn_action.configure(text=tr('⏳ Установка…'), fg_color='#eab308', text_color='#000000', state='disabled')
        elif state == UpdateState.ERROR:
            self.progress_frame.grid()
            err = data.get('error') or ''
            self.progress_label.configure(text=tr('Ошибка обновления: {error}', error=err), text_color=WARNING)
            self.btn_action.configure(text=tr('Скачать обновление'), fg_color=ACCENT, text_color=BG, state='normal')
        else:
            self.progress_frame.grid_remove()
            self.btn_action.configure(text=tr('Скачать обновление'), fg_color=ACCENT, text_color=BG, state='normal')

    def _on_action_click(self):
        state = update_manager.state
        if state in (UpdateState.AVAILABLE, UpdateState.ERROR, UpdateState.IDLE):
            update_manager.start_download()
        elif state == UpdateState.DOWNLOADING:
            update_manager.cancel_download()
        elif state == UpdateState.READY:
            success, msg = update_manager.apply_update(silent=True)
            if success:
                self._on_close()
                if hasattr(self.parent, 'quit_app'):
                    self.parent.after(500, self.parent.quit_app)
            else:
                self.progress_label.configure(text=msg, text_color=WARNING)

    def _open_releases(self):
        url = (update_manager.release_info or {}).get('url') or 'https://github.com/Wave-is/laas/releases'
        webbrowser.open(url)

    def _on_close(self):
        if self._subscribed:
            update_manager.unsubscribe(self._on_update_event)
            self._subscribed = False
        self.destroy()
