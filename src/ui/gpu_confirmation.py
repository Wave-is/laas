"""GPU confirmation shared by the hardware page and both tray menus."""
import logging
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from ..config import config
from ..gpu_modes import gpu_mode_manager
from ..paths import APP_NAME, resource_path


TCC_WARNING = ('Отключите все мониторы от карт, переводимых в TCC. Подключите их '
    'к другой видеокарте или встроенной графике, остающейся в WDDM. '
    'В режиме TCC видеовыходы этих карт не работают. Не подключайте к ним мониторы, '
    'пока не вернёте WDDM.')


def change_lines(preview):
    details = {c['gpu_stable_id']: c for c in preview.get('Changes', [])}
    lines = []
    for entry in preview['Plan']:
        d = details.get(entry['gpu_stable_id'], {})
        name = f"GPU {d['index']} · {d['name']}" if d else entry['gpu_stable_id']
        current = d.get('current_mode', 'UNKNOWN')
        current = 'режим неизвестен' if current == 'UNKNOWN' else current
        line = f"{name}\n{current} → {entry['target_mode']}"
        pending = d.get('pending_mode', 'UNKNOWN')
        if pending not in ('UNKNOWN', d.get('current_mode')):
            line += f' · отложенный режим: {pending}'
        lines.append(line)
    return lines


class GpuConfirmDialog(ctk.CTkToplevel):
    def __init__(self, parent, preview, on_confirm):
        super().__init__(parent)
        self.on_confirm = on_confirm
        self.title('Переключение режимов GPU')
        self.geometry('680x600')
        self.minsize(640, 550)
        self.configure(fg_color='#10161e')
        self.transient(parent)
        self.protocol('WM_DELETE_WINDOW', self.destroy)
        self.bind('<Escape>', lambda event: self.destroy())
        self.after(250, self._set_icon)
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)
        ctk.CTkLabel(self, text='Переключить режимы GPU?', font=('Segoe UI', 23, 'bold'),
            anchor='w').grid(row=0, column=0, sticky='ew', padx=24, pady=(20, 4))
        ctk.CTkLabel(self, text=preview.get('ProfileName', preview['Profile']),
            font=('Segoe UI', 14), text_color='#91a2b4', anchor='w').grid(
                row=1, column=0, sticky='ew', padx=24, pady=(0, 12))
        body = ctk.CTkScrollableFrame(self, fg_color='#18222e', corner_radius=10)
        body.grid(row=2, column=0, sticky='nsew', padx=24)
        for line in change_lines(preview):
            ctk.CTkLabel(body, text=line, font=('Segoe UI', 15), justify='left',
                anchor='w', wraplength=570).pack(fill='x', padx=14, pady=10)
        if any(p['target_mode'] == 'TCC' for p in preview['Plan']):
            ctk.CTkLabel(body, text='Важно: мониторы и TCC', font=('Segoe UI', 16, 'bold'),
                text_color='#f8bd80', anchor='w').pack(fill='x', padx=14, pady=(14, 4))
            ctk.CTkLabel(body, text=TCC_WARNING, font=('Segoe UI', 14), justify='left',
                text_color='#f8bd80', anchor='w', wraplength=570).pack(fill='x', padx=14, pady=(0, 10))
        notes = ['Перед переключением завершите задачи агентов. Station выгрузит модель и остановит сервер моделей llama-swap, если запускала его сама. '
                 'Если драйвер потребует перезагрузку, приложение сообщит об этом.']
        notes.extend(preview.get('Warnings', []))
        ctk.CTkLabel(body, text='\n\n'.join(notes), font=('Segoe UI', 13), justify='left',
            text_color='#91a2b4', anchor='w', wraplength=570).pack(fill='x', padx=14, pady=10)
        self.dont_show = tk.BooleanVar(self, value=False)
        ctk.CTkCheckBox(self, text='Больше не показывать', variable=self.dont_show,
            font=('Segoe UI', 14)).grid(row=3, column=0, sticky='w', padx=24, pady=(18, 6))
        ctk.CTkLabel(self, text='Вернуть это окно: Настройки → Управление режимами GPU → «Снова спрашивать перед переключением»',
            text_color='#91a2b4', font=('Segoe UI', 12), anchor='w').grid(
                row=4, column=0, sticky='ew', padx=24, pady=(0, 12))
        row = ctk.CTkFrame(self, fg_color='transparent')
        row.grid(row=5, column=0, sticky='ew', padx=24, pady=(0, 24))
        ctk.CTkButton(row, text='Отмена', fg_color='#28394a', height=38,
            command=self.destroy).pack(side='left')
        ctk.CTkButton(row, text='Переключить', fg_color='#56d6b1', text_color='#10161e',
            height=38, command=self._confirm).pack(side='right')
        self.grab_set()
        self.focus_set()

    def _set_icon(self):
        if self.winfo_exists():
            try:
                self.iconbitmap(str(resource_path('assets/brand/station.ico')))
            except tk.TclError:
                pass

    def _confirm(self):
        try:
            self.on_confirm(self.dont_show.get())
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
            return
        self.destroy()


class GpuControls:
    def _preview_gpu(self, id=None):
        if getattr(self, 'gpu_dialog', None) and self.gpu_dialog.winfo_exists():
            self.gpu_dialog.lift()
            return
        id = id or self.gpu_combo.get()
        def preview():
            try:
                return gpu_mode_manager.preview_gpu_plan(id)
            except Exception as exc:
                return {'Success': False, 'Message': str(exc)}
        self.worker(preview, self._gpu_plan_ready)

    def _gpu_plan_ready(self, preview):
        if 'Plan' not in preview:
            self._gpu_completed(preview)
            return
        if not preview['Plan'] or config.get('suppress_gpu_switch_warning'):
            self._apply_gpu_plan(preview)
            return
        self.deiconify()
        self.lift()
        self.gpu_dialog = GpuConfirmDialog(self, preview,
            lambda suppress: self._apply_gpu_plan(preview, suppress))

    def _apply_gpu_plan(self, preview, suppress=False):
        if self.busy:
            raise RuntimeError(f'Дождитесь завершения: {self.busy_label}.')
        if suppress:
            config.set('suppress_gpu_switch_warning', True)
        self.worker(lambda: gpu_mode_manager.apply_gpu_profile_only(
            preview['Profile'], expected_plan=preview['Plan']), self._gpu_completed, label='Переключение режимов GPU')

    def _gpu_completed(self, result):
        from .control_center import result_message
        message = result_message(result)
        warnings = result.get('Warnings', [])
        if warnings:
            message += '. ' + '; '.join(warnings)
        self.status_label.configure(text=message[:400],
            text_color='#56d6b1' if result.get('Success') and not warnings else '#f8bd80')
        self.gpu_combo.set(config.get('active_gpu_profile'))
        self._refresh_tray(rebuild=True)
        # A no-op from the hidden tray needs feedback without opening the dashboard.
        if self.state() == 'withdrawn':
            if self.tray and self.tray.HAS_NOTIFICATION:
                try:
                    self.tray.notify(message, APP_NAME)
                    return
                except Exception:
                    logging.getLogger(__name__).exception('GPU tray notification failed')
            self.deiconify()
            self.lift()

    def _reset_gpu_confirmation(self):
        try:
            config.set('suppress_gpu_switch_warning', False)
            self.status_label.configure(text='Подтверждение переключения GPU снова включено', text_color='#56d6b1')
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)
