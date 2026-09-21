"""Расписания page: time-based GPU/model switching and idle auto-unload."""
import logging
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk

from ...config import config
from ...i18n import tr
from ...paths import APP_NAME
from ...profile_storage import profile_storage
from ...gpu_modes import gpu_mode_manager
from ...schedules import ScheduleManager, format_days, ACTION_TITLES
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING

log = logging.getLogger(__name__)

TIMEOUT_OPTIONS = {
    tr('15 минут'): 15,
    tr('30 минут'): 30,
    tr('1 час'): 60,
    tr('2 часа'): 120,
    tr('4 часа'): 240,
}


class SchedulesPage:
    def _build_schedules(self):
        page = self.page('schedules')
        if not hasattr(self, 'schedule_manager'):
            self.schedule_manager = ScheduleManager(
                action_runner=self._execute_schedule_action,
                notify=lambda msg: self.call_in_ui(lambda: self.notify(msg, tr('LAAS: расписание')))
            )
            self.poll_hooks.append(self._schedules_poll)
        self.page_show_hooks['schedules'] = self._refresh_schedules_ui

        # ── Idle auto-unload card ──
        card = self.card(page, tr('Выгрузка модели при простое'),
            tr('Если сервер моделей не получает запросов заданное время, модель выгружается для освобождения видеопамяти. '
               'По умолчанию выключено, так как сервер может использоваться другими ПК по сети.'))
        
        row = self.row(card)
        self.idle_unload_var = tk.BooleanVar(value=bool(config.get('idle_unload_enabled', False)))
        ctk.CTkSwitch(row, text=tr('Выгружать модель при отсутствии запросов'),
                      variable=self.idle_unload_var, command=self._save_idle_unload_settings).pack(side='left', padx=(0, 16))

        ctk.CTkLabel(row, text=tr('Время простоя:'), text_color=MUTED).pack(side='left', padx=(0, 8))
        current_mins = int(config.get('idle_unload_timeout_minutes', 30))
        matching_label = next((k for k, v in TIMEOUT_OPTIONS.items() if v == current_mins), tr('30 минут'))
        self.idle_timeout_combo = ctk.CTkComboBox(
            row, values=list(TIMEOUT_OPTIONS.keys()), width=160, height=32, state='readonly',
            fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL,
            command=lambda _val: self._save_idle_unload_settings()
        )
        self.idle_timeout_combo.set(matching_label)
        self.idle_timeout_combo.pack(side='left')

        self.idle_status_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.idle_status_label.pack(fill='x', padx=20, pady=(4, 10))

        # ── Scheduled tasks card ──
        card2 = self.card(page, tr('Задачи по расписанию'),
            tr('Автоматическое переключение профилей оборудования или моделей в заданное время. '
               'Задачи выполняются в фоновом режиме, пока запущена Station.'))
        
        row2 = self.row(card2)
        self.button(row2, tr('Добавить задачу'), self._show_add_task_dialog, primary=True, width=170)

        self.schedules_list_frame = ctk.CTkFrame(card2, fg_color='transparent')
        self.schedules_list_frame.pack(fill='x', padx=20, pady=(8, 14))

        self._render_tasks_list()

    def _schedules_poll(self, topology, backend_info, running):
        try:
            self.schedule_manager.poll(backend_info)
        except Exception:
            log.exception('Schedules poll error')

    def _execute_schedule_action(self, action: str, target: str):
        def task():
            if action == 'gpu_profile':
                return gpu_mode_manager.apply_gpu_profile(target)
            elif action == 'model_profile':
                return gpu_mode_manager.apply_model_profile_only(target)
            elif action == 'unload_model':
                self._notify_watchdog_stop()
                return gpu_mode_manager.apply_model_profile_only('none')
            elif action == 'stop_backend':
                self._notify_watchdog_stop()
                return gpu_mode_manager.stop_backend()
        self.worker(task, label=tr('Задача по расписанию'))

    def _save_idle_unload_settings(self):
        label = self.idle_timeout_combo.get()
        mins = TIMEOUT_OPTIONS.get(label, 30)
        enabled = bool(self.idle_unload_var.get())
        config.update({
            'idle_unload_enabled': enabled,
            'idle_unload_timeout_minutes': mins,
        })
        self.status_label.configure(text=tr('Настройки выгрузки при простое сохранены.'), text_color=ACCENT)

    def _refresh_schedules_ui(self):
        self.schedule_manager.load_schedules()
        self._render_tasks_list()

    def _render_tasks_list(self):
        for child in self.schedules_list_frame.winfo_children():
            child.destroy()

        tasks = self.schedule_manager.tasks
        if not tasks:
            ctk.CTkLabel(self.schedules_list_frame, text=tr('Задач пока нет. Нажмите «Добавить задачу», чтобы создать расписание.'),
                         text_color=MUTED, anchor='w').pack(fill='x', pady=6)
            return

        for task in tasks:
            task_id = task.get('id')
            item_frame = ctk.CTkFrame(self.schedules_list_frame, fg_color='#111b25', border_color=EDGE,
                                      border_width=1, corner_radius=8)
            item_frame.pack(fill='x', pady=4)

            top_row = ctk.CTkFrame(item_frame, fg_color='transparent')
            top_row.pack(fill='x', padx=12, pady=8)

            enabled_var = tk.BooleanVar(value=bool(task.get('enabled', True)))
            cb = ctk.CTkCheckBox(top_row, text=task.get('time', '00:00'), font=('Segoe UI', 15, 'bold'),
                                 variable=enabled_var,
                                 command=lambda tid=task_id, var=enabled_var: self.schedule_manager.toggle_task(tid, var.get()))
            cb.pack(side='left', padx=(0, 14))

            days_text = format_days(task.get('days', []))
            ctk.CTkLabel(top_row, text=days_text, text_color=MUTED, font=('Segoe UI', 12)).pack(side='left', padx=(0, 14))

            action_type = task.get('action')
            action_desc = ACTION_TITLES.get(action_type, lambda: action_type)()
            target = task.get('target', '')
            if action_type == 'gpu_profile':
                p = profile_storage.get_gpu_profile(target)
                target_label = p.name if p else target
                action_text = f'{action_desc}: {target_label}'
            elif action_type == 'model_profile':
                m = profile_storage.get_model_profile(target)
                target_label = m.name if m else target
                action_text = f'{action_desc}: {target_label}'
            else:
                action_text = action_desc

            ctk.CTkLabel(top_row, text=action_text, anchor='w', font=('Segoe UI', 13)).pack(side='left', fill='x', expand=True)

            del_btn = ctk.CTkButton(top_row, text=tr('Удалить'), width=80, height=28, fg_color='transparent',
                                    border_color=EDGE, border_width=1, hover_color='#364a60',
                                    command=lambda tid=task_id: self._delete_schedule_task(tid))
            del_btn.pack(side='right')

    def _delete_schedule_task(self, task_id: str):
        if self.schedule_manager.remove_task(task_id):
            self._render_tasks_list()
            self.status_label.configure(text=tr('Задача удалена.'), text_color=MUTED)

    def _show_add_task_dialog(self):
        AddTaskDialog(self, on_saved=self._render_tasks_list)


class AddTaskDialog:
    """Dialog to create a new scheduled action."""

    def __init__(self, app, on_saved=None):
        self.app = app
        self.on_saved = on_saved
        self.window = ctk.CTkToplevel(app)
        self.window.title(tr('Добавить задачу по расписанию'))
        self.window.geometry('540x480')
        self.window.configure(fg_color=BG)
        self.window.transient(app)
        self.window.after(150, self.window.lift)

        ctk.CTkLabel(self.window, text=tr('Новая задача'), font=('Segoe UI', 18, 'bold'), anchor='w').pack(fill='x', padx=24, pady=(20, 10))

        # Time
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(fill='x', padx=24, pady=6)
        ctk.CTkLabel(row, text=tr('Время (ЧЧ:ММ):'), width=140, anchor='w', text_color=MUTED).pack(side='left')
        self.time_entry = ctk.CTkEntry(row, width=120, height=32, fg_color='#111b25', border_color=EDGE)
        self.time_entry.insert(0, '08:00')
        self.time_entry.pack(side='left')

        # Days
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(fill='x', padx=24, pady=6)
        ctk.CTkLabel(row, text=tr('Дни недели:'), width=140, anchor='w', text_color=MUTED).pack(side='left')
        self.days_mode = ctk.CTkSegmentedButton(
            row, values=[tr('Каждый день'), tr('Будни'), tr('Выходные')],
            selected_color=EDGE, selected_hover_color=EDGE, unselected_color='#111b25'
        )
        self.days_mode.set(tr('Каждый день'))
        self.days_mode.pack(side='left')

        # Action
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(fill='x', padx=24, pady=6)
        ctk.CTkLabel(row, text=tr('Действие:'), width=140, anchor='w', text_color=MUTED).pack(side='left')
        self.action_combo = ctk.CTkComboBox(
            row, values=[tr('Переключить GPU-профиль'), tr('Загрузить модель'), tr('Выгрузить модель'), tr('Остановить сервер моделей')],
            width=280, height=32, state='readonly', fg_color='#111b25', border_color=EDGE,
            button_color=EDGE, dropdown_fg_color=PANEL, command=self._action_changed
        )
        self.action_combo.set(tr('Переключить GPU-профиль'))
        self.action_combo.pack(side='left')

        # Target (GPU profile or Model profile)
        self.target_row = ctk.CTkFrame(self.window, fg_color='transparent')
        self.target_row.pack(fill='x', padx=24, pady=6)
        self.target_label = ctk.CTkLabel(self.target_row, text=tr('Профиль:'), width=140, anchor='w', text_color=MUTED)
        self.target_label.pack(side='left')
        self.target_combo = ctk.CTkComboBox(
            self.target_row, values=['—'], width=280, height=32, state='readonly',
            fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL
        )
        self.target_combo.pack(side='left')
        self._action_changed()

        # Name
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(fill='x', padx=24, pady=6)
        ctk.CTkLabel(row, text=tr('Название:'), width=140, anchor='w', text_color=MUTED).pack(side='left')
        self.name_entry = ctk.CTkEntry(row, placeholder_text=tr('Автоматически'), width=280, height=32, fg_color='#111b25', border_color=EDGE)
        self.name_entry.pack(side='left')

        # Buttons
        row_btn = ctk.CTkFrame(self.window, fg_color='transparent')
        row_btn.pack(fill='x', padx=24, pady=(24, 16))
        ctk.CTkButton(row_btn, text=tr('Сохранить'), command=self._save, width=130, height=34,
                      fg_color=ACCENT, text_color=BG, hover_color='#71e2c2').pack(side='left', padx=(0, 10))
        ctk.CTkButton(row_btn, text=tr('Отмена'), command=self.window.destroy, width=100, height=34,
                      fg_color=EDGE, text_color=TEXT).pack(side='left')

    def _action_changed(self, _val=None):
        act = self.action_combo.get()
        if act == tr('Переключить GPU-профиль'):
            self.target_row.pack(fill='x', padx=24, pady=6)
            self.target_label.configure(text=tr('GPU-профиль:'))
            profiles = list(profile_storage.gpu_profiles.keys())
            self.target_combo.configure(values=profiles or ['—'])
            if profiles:
                self.target_combo.set(profiles[0])
        elif act == tr('Загрузить модель'):
            self.target_row.pack(fill='x', padx=24, pady=6)
            self.target_label.configure(text=tr('Модель:'))
            models = [k for k, m in profile_storage.model_profiles.items() if k != 'none']
            self.target_combo.configure(values=models or ['—'])
            if models:
                self.target_combo.set(models[0])
        else:
            self.target_row.pack_forget()

    def _save(self):
        time_text = self.time_entry.get().strip()
        days_mode = self.days_mode.get()
        if days_mode == tr('Будни'):
            days = [0, 1, 2, 3, 4]
        elif days_mode == tr('Выходные'):
            days = [5, 6]
        else:
            days = list(range(7))

        act_text = self.action_combo.get()
        if act_text == tr('Переключить GPU-профиль'):
            action = 'gpu_profile'
            target = self.target_combo.get()
        elif act_text == tr('Загрузить модель'):
            action = 'model_profile'
            target = self.target_combo.get()
        elif act_text == tr('Выгрузить модель'):
            action = 'unload_model'
            target = 'none'
        else:
            action = 'stop_backend'
            target = ''

        name = self.name_entry.get().strip()
        try:
            self.app.schedule_manager.add_task(
                name=name, time_str=time_text, days=days, action=action, target=target
            )
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.window)
            return

        if self.on_saved:
            self.on_saved()
        self.window.destroy()
