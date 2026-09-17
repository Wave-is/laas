"""Startup preferences and a cancellable, one-shot startup sequence."""
import logging
import threading
import tkinter as tk
from tkinter import messagebox
import customtkinter as ctk
from ..config import config
from ..profile_storage import profile_storage
from ..shared_services import shared_services
from ..gpu_modes import gpu_mode_manager
from ..startup import startup_settings, startup_steps, StartupRunner
from ..windows_startup import WindowsStartup
from ..storage import atomic_write
from ..paths import APP_NAME, data_dir
from ..i18n import tr

MUTED, ACCENT, EDGE = '#91a2b4', '#56d6b1', '#28394a'


class StartupControls:
    def _build_startup_settings(self, page):
        settings = startup_settings(config.get('startup'))
        self.startup_cancel = threading.Event()
        self.startup_scheduled = False
        self.startup_pending = False
        self.startup_runner = StartupRunner(self.controller, shared_services, gpu_mode_manager, profile_storage)
        card = self.card(page, tr('Запуск Station и компонентов'),
            tr('Windows запускает Station при входе в вашу учётную запись. Выбранные ниже компоненты запускаются при каждом запуске Station.'))
        self.windows_startup = WindowsStartup()
        self.windows_startup_var = tk.BooleanVar(value=False)
        self.windows_startup_check = ctk.CTkCheckBox(card, text=tr('Запускать Station при входе в Windows'),
            variable=self.windows_startup_var, command=self._toggle_windows_startup)
        self.windows_startup_check.pack(anchor='w', padx=20, pady=(8, 4))
        self.controls.append(self.windows_startup_check)
        self.windows_startup_hint = ctk.CTkLabel(card, text='', anchor='w', text_color=MUTED, wraplength=770, justify='left')
        self.windows_startup_hint.pack(fill='x', padx=20, pady=(0, 12))
        try:
            self._windows_startup_loaded(self.windows_startup.status())
        except Exception as exc:
            self.windows_startup_hint.configure(text=str(exc))
        self.startup_vars = {}
        for key, label in [('minimized', tr('Открывать Station свёрнутой в трей')),
                           ('enabled', tr('Автоматически запускать выбранные ниже компоненты')),
                           ('stop_on_error', tr('Останавливать дальнейший запуск при ошибке'))]:
            variable = tk.BooleanVar(value=settings[key])
            self.startup_vars[key] = variable
            checkbox = ctk.CTkCheckBox(card, text=label, variable=variable)
            checkbox.pack(anchor='w', padx=20, pady=6)
            self.controls.append(checkbox)
        ctk.CTkLabel(card, text=tr('Выберите компоненты ниже и нажмите «Сохранить запуск».'),
            text_color=MUTED, anchor='w').pack(fill='x', padx=20, pady=(4, 0))
        row = self.row(card)
        ctk.CTkLabel(row, text=tr('Задержка, секунд (0–300)')).pack(side='left', padx=(0, 15))
        self.startup_delay = ctk.CTkEntry(row, width=85)
        self.startup_delay.insert(0, str(settings['delay_seconds']))
        self.startup_delay.pack(side='left', pady=10)
        self.controls.append(self.startup_delay)
        ctk.CTkLabel(card, text=tr('Модель (при загрузке автоматически запустится сервер моделей llama-swap)'), anchor='w', text_color=MUTED).pack(fill='x', padx=20)
        self.startup_model = self.combo(self.row(card), list(profile_storage.model_profiles), settings['model_id'], width=560)
        ctk.CTkLabel(card, text=tr('Агенты и интерфейсы'), anchor='w', text_color=MUTED).pack(fill='x', padx=20)
        self.startup_agents_area = self.row(card)
        ctk.CTkLabel(card, text=tr('Локальные службы'), anchor='w', text_color=MUTED).pack(fill='x', padx=20, pady=(12, 0))
        self.startup_services_area = self.row(card)
        self.startup_frontend_vars, self.startup_service_vars = {}, {}
        self._refresh_startup_choices(initial=settings)
        ctk.CTkLabel(card, text='\n'.join([tr('Порядок: локальные службы → сервер моделей и модель → агенты. Режимы GPU не меняются.'),
            tr('Удалённые сервисы уже работают отдельно; их автопроверки настраиваются в «Службах».'),
            tr('Если агент ещё не настроен на выбранную модель, один раз запустите его вручную и подтвердите изменения.')]),
            text_color=MUTED, wraplength=770, justify='left', anchor='w').pack(fill='x', padx=20, pady=(12, 0))
        row = self.row(card)
        self.button(row, tr('Сохранить запуск'), self._save_startup_settings, True, width=180)
        self.button(row, tr('Отчёт последнего автозапуска'), self._show_startup_result, width=240)
        self.startup_summary = ctk.CTkLabel(card, text=tr('Изменения компонентов применяются при следующем запуске Station.'),
            text_color=MUTED, wraplength=770, justify='left', anchor='w')
        self.startup_summary.pack(fill='x', padx=20, pady=(0, 14))
        # Always reachable while the settings page is scrolled or Station starts minimized.
        self.startup_banner = ctk.CTkFrame(self.body, fg_color=EDGE)
        self.startup_banner_label = ctk.CTkLabel(self.startup_banner, text='', wraplength=680, anchor='w', justify='left')
        self.startup_banner_label.pack(side='left', fill='x', expand=True, padx=12, pady=8)
        self.startup_cancel_button = ctk.CTkButton(self.startup_banner, text=tr('Отменить запуск'), width=145,
            command=self._cancel_startup)
        self.startup_cancel_button.pack(side='right', padx=12, pady=8)

    def _windows_startup_loaded(self, state):
        self.windows_startup_var.set(state['enabled'])
        self.windows_startup_hint.configure(text=tr('Автозагрузка Windows включена для текущего пользователя.') if state['enabled'] else
            tr('Автозагрузка Windows выключена. Галочка сохраняется сразу; права администратора не нужны.'))

    def _toggle_windows_startup(self):
        requested = self.windows_startup_var.get()
        def action():
            try:
                return {'Success': True, 'State': self.windows_startup.set_enabled(requested)}
            except Exception as exc:
                try:
                    state = self.windows_startup.status()
                except Exception:
                    state = {'enabled': not requested}
                return {'Success': False, 'Message': str(exc), 'State': state}
        def done(result):
            self._windows_startup_loaded(result['State'])
            if not result['Success']:
                self.windows_startup_hint.configure(text=result['Message'])
        self.worker(action, done)

    def _refresh_startup_choices(self, initial=None):
        if not hasattr(self, 'startup_agents_area'):
            return
        settings = initial or startup_settings(config.get('startup'))
        groups = [('frontend_ids', self.startup_agents_area, 'startup_frontend_vars', self.controller.frontends),
                  ('service_ids', self.startup_services_area, 'startup_service_vars',
                   {id: p for id, p in shared_services.profiles().items() if p.get('type', 'local') == 'local'})]
        for key, area, attr, rows in groups:
            old = getattr(self, attr)
            selected = {id for id, var in old.items() if var.get()} if old else set(settings[key])
            for child in area.winfo_children():
                child.destroy()
            variables = {}
            order = sorted(rows, key=lambda id: rows[id].get('runtime_id') != config.get('primary_agent_runtime'))
            for id in dict.fromkeys([*order, *sorted(selected)]):
                row = rows.get(id, {})
                available = bool(row) and (key == 'service_ids' or row.get('status') == 'INSTALLED')
                label = row.get('name', id) if available else tr('{name} — недоступен', name=row.get('name', id))
                var = tk.BooleanVar(value=id in selected)
                check = ctk.CTkCheckBox(area, text=label, variable=var,
                    state='normal' if available or id in selected else 'disabled')
                check.pack(anchor='w', pady=5)
                variables[id] = var
            if not variables:
                ctk.CTkLabel(area, text=tr('Нет локальных служб. Добавьте их в разделе «Службы».') if key == 'service_ids' else
                    tr('Идёт обнаружение агентов…'), text_color=MUTED).pack(anchor='w')
            setattr(self, attr, variables)

    def _save_startup_settings(self):
        try:
            settings = startup_settings({**{key: var.get() for key, var in self.startup_vars.items()},
                'delay_seconds': int(self.startup_delay.get()), 'model_id': self.startup_model.get(),
                'frontend_ids': [id for id, var in self.startup_frontend_vars.items() if var.get()],
                'service_ids': [id for id, var in self.startup_service_vars.items() if var.get()]})
            if settings['model_id'] not in profile_storage.model_profiles:
                raise ValueError(tr('Выбранная модель недоступна.'))
            if settings['enabled'] and any(self.controller.frontends.get(id, {}).get('status') != 'INSTALLED' for id in settings['frontend_ids']):
                raise ValueError(tr('Уберите недоступные интерфейсы из автозапуска или установите их.'))
            services = shared_services.profiles()
            if settings['enabled'] and any(id not in services or services[id].get('type', 'local') != 'local' for id in settings['service_ids']):
                raise ValueError(tr('Уберите отсутствующие или удалённые службы из автозапуска.'))
            if settings['enabled'] and not startup_steps(settings):
                raise ValueError(tr('Выберите хотя бы один компонент или выключите автоматический запуск компонентов.'))
            config.set('startup', settings)
            self.startup_summary.configure(text=tr('Сохранено. При следующем запуске: компонентов: {count}, задержка {delay} с.',
                count=len(startup_steps(settings)), delay=settings['delay_seconds']) if settings['enabled'] else
                tr('Сохранено. При следующем запуске компоненты не запускаются.'))
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def _schedule_startup(self):
        if self.startup_scheduled:
            return
        self.startup_scheduled = True
        settings = startup_settings(config.get('startup'))
        if self.skip_startup or not startup_steps(settings):
            return
        self.startup_pending = True
        self.startup_banner.grid(row=3, column=0, sticky='ew', pady=(8, 0))
        def tick(remaining):
            if self.stop_event.is_set() or self.startup_cancel.is_set():
                return
            self.startup_banner_label.configure(text=tr('Автозапуск через {seconds} с. Компонентов: {count}.', seconds=remaining, count=len(startup_steps(settings))))
            if remaining:
                self.after(1000, lambda: tick(remaining - 1))
            elif self.busy:
                self.after(250, lambda: tick(0))
            else:
                self.startup_pending = False
                self.worker(lambda: self.startup_runner.run(settings, self.startup_cancel,
                    lambda message: self.events.put(('startup_progress', message, None))), self._startup_done, label=tr('Автозапуск компонентов'))
        tick(settings['delay_seconds'])

    def _cancel_startup(self):
        self.startup_cancel.set()
        self.startup_banner_label.configure(text=tr('Автозапуск отменён. Текущее действие завершится; следующие не начнутся.'))
        self.startup_cancel_button.configure(state='disabled')
        if self.startup_pending:
            self.startup_pending = False
            self._startup_done({'Success': False, 'Message': tr('Автозапуск отменён до запуска компонентов.'), 'Steps': []})

    def _startup_done(self, result):
        self.startup_banner.grid_remove()
        self.startup_summary.configure(text=result['Message'])
        self.status_label.configure(text=result['Message'][:400], text_color=ACCENT if result['Success'] else '#f8ad88')
        try:
            atomic_write(data_dir() / 'logs/startup-last.json', result, backup=False)
        except Exception:
            logging.exception('Cannot save startup report')
        if not result['Success']:
            self.deiconify()
            self.lift()

    def _show_startup_result(self):
        from ..storage import read_document
        report = read_document(data_dir() / 'logs/startup-last.json', {})
        lines = [report.get('Message', tr('Автоматический запуск компонентов ещё не выполнялся.'))]
        lines += [('✓ ' + row['name'] if row.get('Success') else tr('Ошибка: {name}', name=row['name'])) + '\n' + row.get('Message', '')
                  for row in report.get('Steps', [])]
        self.review(tr('Результат автоматического запуска'), '\n\n'.join(lines))
