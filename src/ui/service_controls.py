"""Add/edit services without YAML; actions reflect who runs the process."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox
import webbrowser
import customtkinter as ctk
from ..paths import APP_NAME, resource_path
from ..shared_services import shared_services
from ..service_profiles import KINDS, LOCATIONS, profile_from_form, service_actions, status_text, http_url
from ..supervisor import supervisor

BG, PANEL, EDGE, MUTED, ACCENT = '#10161e', '#18222e', '#28394a', '#91a2b4', '#56d6b1'
ACTION_LABELS = {'start': 'Запустить', 'stop': 'Остановить', 'check': 'Проверить',
    'open': 'Открыть адрес ↗', 'copy': 'Скопировать адрес', 'edit': 'Настроить', 'log': 'Журнал'}


def window_icon(window):
    if window.winfo_exists():
        try:
            window.iconbitmap(str(resource_path('assets/brand/station.ico')))
        except tk.TclError:
            pass


class ServiceEditor(ctk.CTkToplevel):
    def __init__(self, parent, original, on_save, on_remove=None):
        super().__init__(parent)
        self.original, self.on_save, self.on_remove = original, on_save, on_remove
        self.title('Настройка сервиса' if original else 'Добавить сервис')
        self.geometry('760x740')
        self.minsize(690, 580)
        self.configure(fg_color=BG)
        self.transient(parent)
        self.after(250, lambda: window_icon(self))
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        ctk.CTkLabel(self, text=self.title(), font=('Segoe UI', 24, 'bold'), anchor='w').grid(
            row=0, column=0, sticky='ew', padx=24, pady=(20, 12))
        body = ctk.CTkScrollableFrame(self, fg_color=PANEL)
        body.grid(row=1, column=0, sticky='nsew', padx=24)
        self.name = self.field(body, 'Название', original.get('name', ''), 'Например: ComfyUI на втором ПК')
        self.caption(body, 'Что подключаем')
        self.kind = ctk.CTkOptionMenu(body, values=list(KINDS.values()), command=lambda _: self._update_mode())
        self.kind.set(KINDS[original.get('kind', 'http' if original else 'comfyui')])
        self.kind.pack(fill='x', padx=16, pady=(0, 12))
        self.caption(body, 'Где и как работает сервис')
        self.location = ctk.CTkOptionMenu(body, values=list(LOCATIONS.values()), command=lambda _: self._update_mode())
        self.location.set(LOCATIONS[original.get('type', 'remote')])
        self.location.pack(fill='x', padx=16, pady=(0, 8))
        self.mode_hint = ctk.CTkLabel(body, text='', text_color=MUTED, wraplength=640, anchor='w', justify='left')
        self.mode_hint.pack(fill='x', padx=16, pady=(0, 12))
        self.url = self.field(body, 'Адрес сервиса', original.get('url', ''), 'http://127.0.0.1:8188')
        self.health_box = ctk.CTkFrame(body, fg_color='transparent')
        self.health_box.pack(fill='x')
        self.health = self.field(self.health_box, 'Адрес проверки (необязательно)', original.get('health_url', ''),
            'Например: http://127.0.0.1:8000/health')
        self.comfy_hint = ctk.CTkLabel(body, text='ComfyUI: доступность проверяется по /system_stats, без генерации.',
            text_color=MUTED, anchor='w', justify='left', wraplength=640)
        self.comfy_hint.pack(fill='x', padx=16, pady=(0, 12))
        self.local = ctk.CTkFrame(body, fg_color='transparent')
        self.local.pack(fill='x')
        self.exe = self.field(self.local, 'Программа для запуска', original.get('executable', ''), 'C:\\ComfyUI\\python_embeded\\python.exe')
        ctk.CTkButton(self.local, text='Выбрать программу…', fg_color=EDGE,
            command=lambda: self.browse(self.exe)).pack(anchor='w', padx=16, pady=(0, 12))
        self.cwd = self.field(self.local, 'Рабочая папка (необязательно)', original.get('working_directory', ''), 'C:\\ComfyUI')
        ctk.CTkButton(self.local, text='Выбрать папку…', fg_color=EDGE,
            command=lambda: self.browse(self.cwd, True)).pack(anchor='w', padx=16, pady=(0, 12))
        self.caption(self.local, 'Параметры запуска — каждый на отдельной строке, без кавычек')
        self.args = ctk.CTkTextbox(self.local, height=110, font=('Segoe UI', 13))
        self.args.pack(fill='x', padx=16, pady=(0, 4))
        self.args.insert('1.0', '\n'.join(original.get('arguments', [])))
        ctk.CTkLabel(self.local, text='Пример для Python: первая строка C:\\ComfyUI\\main.py, затем --listen, затем 127.0.0.1.',
            text_color=MUTED, wraplength=640, justify='left', anchor='w').pack(fill='x', padx=16, pady=(0, 12))
        self.restart = tk.BooleanVar(self, value=original.get('restart_policy') == 'on_failure')
        ctk.CTkCheckBox(self.local, text='Перезапустить при сбое (до 3 попыток)', variable=self.restart).pack(anchor='w', padx=16, pady=(0, 12))
        self.monitor = tk.BooleanVar(self, value=original.get('monitor_enabled', bool(original)))
        self.monitor_check = ctk.CTkCheckBox(body, text='Проверять доступность автоматически, каждые 30 секунд', variable=self.monitor)
        self.monitor_check.pack(anchor='w', padx=16, pady=(12, 4))
        ctk.CTkLabel(body, text='Оставьте выключенным, если сервер занят. Кнопка «Проверить» выполнит одну проверку по вашему нажатию.',
            text_color=MUTED, wraplength=640, anchor='w', justify='left').pack(fill='x', padx=16, pady=(0, 16))
        self.error = ctk.CTkLabel(self, text='', text_color='#f8ad88', wraplength=700, anchor='w', justify='left')
        self.error.grid(row=2, column=0, sticky='ew', padx=24, pady=8)
        row = ctk.CTkFrame(self, fg_color='transparent')
        row.grid(row=3, column=0, sticky='ew', padx=24, pady=(0, 20))
        ctk.CTkButton(row, text='Отмена', fg_color=EDGE, command=self.destroy).pack(side='left')
        if on_remove:
            ctk.CTkButton(row, text='Убрать из Station', fg_color=EDGE, command=self._remove).pack(side='left', padx=10)
        ctk.CTkButton(row, text='Сохранить', fg_color=ACCENT, text_color=BG, command=self._save).pack(side='right')
        self._update_mode()
        self.grab_set()
        self.bind('<Escape>', lambda _: self.destroy())

    @staticmethod
    def caption(parent, text):
        ctk.CTkLabel(parent, text=text, anchor='w', text_color=MUTED).pack(fill='x', padx=16, pady=(8, 4))

    def field(self, parent, label, value, example):
        self.caption(parent, label)
        field = ctk.CTkEntry(parent, height=36, placeholder_text=example)
        field.pack(fill='x', padx=16, pady=(0, 12))
        if value:
            field.insert(0, value)
        return field

    @staticmethod
    def key(choices, value):
        return next(key for key, label in choices.items() if label == value)

    def _update_mode(self):
        remote = self.key(LOCATIONS, self.location.get()) == 'remote'
        comfy = self.key(KINDS, self.kind.get()) == 'comfyui'
        self.mode_hint.configure(text='Station проверяет подключение. Запускайте и останавливайте сервис на том компьютере.' if remote else
            'Station запускает выбранную программу и может остановить созданный ею процесс.')
        self.health_box.pack_forget()
        self.comfy_hint.pack_forget()
        self.local.pack_forget()
        if comfy:
            self.comfy_hint.pack(fill='x', padx=16, pady=(0, 12), before=self.monitor_check)
        else:
            self.health_box.pack(fill='x', before=self.monitor_check)
        if not remote:
            self.local.pack(fill='x', before=self.monitor_check)

    def browse(self, entry, directory=False):
        path = filedialog.askdirectory(parent=self) if directory else filedialog.askopenfilename(parent=self)
        if path:
            entry.delete(0, 'end')
            entry.insert(0, path)

    def _save(self):
        try:
            profile = profile_from_form(self.original, name=self.name.get(),
                location=self.key(LOCATIONS, self.location.get()), kind=self.key(KINDS, self.kind.get()),
                url=self.url.get(), health_url=self.health.get(), executable=self.exe.get(), working_directory=self.cwd.get(),
                arguments=self.args.get('1.0', 'end'), monitor=self.monitor.get(), restart=self.restart.get())
            self.on_save(profile)
            self.destroy()
        except Exception as exc:
            self.error.configure(text=str(exc))

    def _remove(self):
        if messagebox.askyesno(APP_NAME, 'Убрать подключение из Station? Программа и файлы сервиса останутся на месте.', parent=self):
            try:
                self.on_remove()
                self.destroy()
            except Exception as exc:
                self.error.configure(text=str(exc))


class ServiceControls:
    def _build_service_connections(self, page):
        card = self.card(page, 'ComfyUI и другие сервисы',
            'Добавьте адрес уже работающего сервиса или программу для запуска на этом ПК. Сервисы доступны независимо от выбранного агента.')
        row = self.row(card)
        self.button(row, 'Добавить сервис', self._edit_service, True, width=160)
        self.button(row, 'Как пользоваться', self._service_guide, width=170)
        ctk.CTkLabel(card, text='1. Добавьте сервис → 2. Проверьте подключение → 3. Откройте его адрес.\n'
            'Для генерации в ComfyUI откройте workflow в его интерфейсе. Для генерации из агента настройте его инструмент с адресом сервиса.',
            text_color=MUTED, font=('Segoe UI', 13), wraplength=790, justify='left', anchor='w').pack(fill='x', padx=20, pady=(0, 18))
        self.service_cards = ctk.CTkFrame(page, fg_color='transparent')
        self.service_cards.pack(fill='x')
        self.service_widgets, self.service_states = {}, {}
        self.service_signature = None
        self._refresh_service_cards()

    def _edit_service(self, id=None):
        if getattr(self, 'service_editor', None) and self.service_editor.winfo_exists():
            self.service_editor.lift()
            return
        original, expected = shared_services.edit_snapshot(id)
        def saved(profile):
            shared_services.save(profile, expected)
            self._refresh_service_cards()
            self._refresh_tray(rebuild=True)
            self.status_label.configure(text='Сервис сохранён. Автопроверки включены.' if profile['monitor_enabled'] else
                'Подключение сохранено. Нажмите «Проверить», когда сервер будет свободен.', text_color=ACCENT)
        def removed():
            shared_services.remove(id, expected)
            self._refresh_service_cards()
            self._refresh_tray(rebuild=True)
        self.deiconify()
        self.lift()
        self.service_editor = ServiceEditor(self, original, saved, removed if original else None)

    def _refresh_service_cards(self, states=None):
        import json
        profiles = shared_services.profiles()
        signature = json.dumps(profiles, sort_keys=True, ensure_ascii=False)
        if signature != self.service_signature:
            self.service_signature = signature
            self.service_states = {}
            for child in self.service_cards.winfo_children():
                child.destroy()
            self.service_widgets = {}
            # Drop destroyed card buttons from the global busy-state list.
            self.controls = [control for control in self.controls if control.winfo_exists()]
            if not profiles:
                card = self.card(self.service_cards, 'Сервисов пока нет',
                    'Нажмите «Добавить сервис». Пример: ComfyUI → «Уже работает / другой компьютер» → '
                    'http://192.168.1.10:8188 → «Сохранить». Адрес замените своим.')
            for id, p in profiles.items():
                remote = p.get('type', 'local') == 'remote'
                card = self.card(self.service_cards, p.get('name', id),
                    ('Работает отдельно · ' if remote else 'Запускается на этом ПК · ') + KINDS[p.get('kind', 'http')])
                if p.get('url') or p.get('health_url'):
                    ctk.CTkLabel(card, text=p.get('url') or p['health_url'], anchor='w', text_color=ACCENT,
                        wraplength=790, justify='left').pack(fill='x', padx=20, pady=(0, 6))
                state_label = ctk.CTkLabel(card, text='Ещё не проверен', anchor='w', font=('Segoe UI', 17, 'bold'))
                state_label.pack(fill='x', padx=20)
                detail = ctk.CTkLabel(card, text='', anchor='w', justify='left', wraplength=790, text_color=MUTED)
                detail.pack(fill='x', padx=20)
                actions = service_actions(p)
                buttons = {}
                for i, action in enumerate(actions):
                    if i % 4 == 0:
                        row = self.row(card)
                    buttons[action] = self.button(row, ACTION_LABELS[action],
                        lambda action=action, id=id: self._service_action(action, id), action in ('start', 'check'), width=165)
                self.service_widgets[id] = (state_label, detail, buttons)
                if remote:
                    ctk.CTkLabel(card, text='Запуск и остановка выполняются на том компьютере. «Проверить» не запускает генерацию.',
                        text_color=MUTED, wraplength=790, anchor='w', justify='left').pack(fill='x', padx=20, pady=(0, 14))
        if states is not None:
            self.service_states.update(states)
        for id, p in profiles.items():
            state = self.service_states.get(id, {'remote': p.get('type') == 'remote',
                'monitor_paused': not p.get('monitor_enabled', True), 'message': 'Нажмите «Проверить» для проверки подключения.'})
            label, detail, buttons = self.service_widgets[id]
            title, message = status_text(state)
            color = '#f8bd80' if state.get('health') in ('ERROR', 'UNAVAILABLE') else ACCENT if state.get('health') == 'READY' else MUTED
            label.configure(text=title, text_color=color)
            detail.configure(text=message)
            for action, button in buttons.items():
                enabled = not self.busy
                if action == 'stop':
                    enabled = enabled and state.get('owned', False)
                if action == 'start':
                    enabled = enabled and not state.get('running', False) and state.get('health') != 'READY'
                button.configure(state='normal' if enabled else 'disabled')

    def _service_action(self, action, id):
        if action == 'edit':
            self._edit_service(id)
        elif action in ('check', 'start', 'stop'):
            def done(result):
                state = result.get('State')
                if state:
                    self.service_states[id] = state
                self.status_label.configure(text=result.get('Message', 'Готово')[:150], text_color=ACCENT if result.get('Success') else '#f8bd80')
                self._refresh_service_cards()
                self._refresh_tray(rebuild=True)
            self.worker(lambda: getattr(shared_services, action)(id), done)
        elif action == 'log':
            log = supervisor.records.get('service:' + id, {}).get('log')
            if log and os.path.isfile(log):
                os.startfile(log)
            else:
                self.status_label.configure(text='Журнал появится после первого запуска сервиса через Station.')
        else:
            url = http_url(shared_services.profiles()[id]['url'])
            if action == 'open':
                webbrowser.open(url)
            elif action == 'copy':
                self.clipboard_clear()
                self.clipboard_append(url)
                self.status_label.configure(text='Адрес скопирован. Его можно указать в инструменте генерации вашего агента.')

    def _service_guide(self):
        window = ctk.CTkToplevel(self)
        window.title('Сервисы: подключение и примеры')
        window.geometry('820x650')
        window.transient(self)
        window.after(250, lambda: window_icon(window))
        text = ctk.CTkTextbox(window, font=('Segoe UI', 15), wrap='word')
        text.pack(fill='both', expand=True, padx=20, pady=20)
        text.insert('1.0', resource_path('docs/SERVICES_GUIDE_RU.md').read_text(encoding='utf-8'))
        text.configure(state='disabled')
        ctk.CTkButton(window, text='Закрыть', command=window.destroy).pack(pady=(0, 20))
