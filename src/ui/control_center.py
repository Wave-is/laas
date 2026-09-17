"""Responsive desktop control center. Workers communicate through a queue, never Tk calls."""
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog
import customtkinter as ctk
from ..paths import APP_NAME, VERSION, data_dir, resource_path
from ..config import config
from ..profile_storage import profile_storage
from ..hardware_topology import topology_engine
from ..gpu_modes import gpu_mode_manager
from ..controller import StationController
from ..process_manager import pm
from .. import model_server
from ..supervisor import supervisor
from ..storage import read_document, atomic_write, digest
from ..agent_sync import apply_preview
from ..tray_renderer import renderer
from ..shared_services import shared_services
from ..branding import mark_image
from .tray_controls import TrayControls
from .gpu_confirmation import GpuControls
from .service_controls import ServiceControls
from .agent_controls import AgentControls
from .startup_controls import StartupControls

BG = '#10161e'
PANEL = '#18222e'
EDGE = '#28394a'
TEXT = '#e6eef6'
MUTED = '#91a2b4'
ACCENT = '#56d6b1'

def number(value, suffix=''):
    return '—' if value is None else f'{value:g}{suffix}'

def result_message(value):
    text = str(value.get('Message', 'Готово'))
    return {'Frontend process started': 'Агент запущен', 'Terminal opened': 'Терминал агента открыт',
            'No driver changes required': 'Переключение GPU не требуется: режимы уже соответствуют профилю',
            'Agent runtime selected': 'Основной агент выбран', 'Frontend selected': 'Способ запуска агента выбран',
            'Install LocalAgentGpuModeHelper in Settings first': 'Для переключения GPU установите службу в разделе «Настройки»',
            'Finish and close active agent sessions before changing GPU drivers': 'Перед переключением GPU завершите и закройте сеансы агентов'}.get(text, text)

class ProfileCombo(ctk.CTkComboBox):
    def __init__(self, *args, resolver, values, **kwargs):
        self.resolver = resolver
        self.labels = self._labels(values)
        super().__init__(*args, values=list(self.labels.values()), **kwargs)
    def _labels(self, values):
        from collections import Counter
        labels = {id: self.resolver(id) for id in values}
        counts = Counter(labels.values())
        return {id: label if counts[label] == 1 else f'{label} ({id})' for id, label in labels.items()}
    def get(self):
        value = super().get()
        return next((id for id, label in self.labels.items() if label == value), value)
    def set(self, value):
        super().set(self.labels.get(value, value))
    def configure(self, require_redraw=False, **kwargs):
        if 'values' in kwargs:
            self.labels = self._labels(kwargs['values'])
            kwargs['values'] = list(self.labels.values())
        return super().configure(require_redraw=require_redraw, **kwargs)

class ControlCenter(AgentControls, StartupControls, ServiceControls, GpuControls, TrayControls, ctk.CTk):
    def __init__(self, start_minimized=False, no_tray=False, skip_startup=False):
        super().__init__()
        ctk.set_appearance_mode('dark')
        self.title(APP_NAME + ' — центр управления')
        self.geometry('1180x790')
        self.minsize(1080, 680)
        self.configure(fg_color=BG)
        self.events = queue.Queue(maxsize=30)
        self.stop_event = threading.Event()
        self.controller = StationController()
        self.topology = None
        self.tray = None
        self.no_tray = no_tray
        self.skip_startup = skip_startup
        self.tray_icons = []
        self.backend_online = False
        self.backend_info = None
        self.ready_model_ids = set()
        self.ready_model_names = []
        self.frontend_running = {}
        self.dashboard_gpus = {}
        self.dashboard_gpu_ids = None
        self.tray_signature = None
        self.busy = False
        self.busy_label = ''
        self.pages = {}
        self.controls = []
        self.gpu_widgets = {}
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self._build_sidebar()
        self.body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.body.grid(row=0, column=1, sticky='nsew', padx=28, pady=22)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)
        self.heading = ctk.CTkLabel(self.body, text='Ваша локальная AI-станция', font=('Segoe UI', 27, 'bold'), anchor='w')
        self.heading.grid(row=0, column=0, sticky='ew')
        self.status_label = ctk.CTkLabel(self.body, text='Обнаружение оборудования и агентов…', text_color=MUTED, anchor='w',
            justify='left', wraplength=900)
        self.status_label.grid(row=1, column=0, sticky='ew', pady=(4, 18))
        self._build_station()
        self._build_hardware()
        self._build_models()
        self._build_agents()
        self._build_services()
        self._build_settings()
        self.show_page('Станция')
        self.protocol('WM_DELETE_WINDOW', self.hide_to_tray)
        self.after(250, self._set_window_icon)
        self.after(100, self._drain_events)
        self.worker(self.controller.discover_agents, self._agents_discovered)
        threading.Thread(target=self._poll, daemon=True).start()
        if not no_tray:
            self._create_tray()
        if profile_storage.warnings:
            self.after(3000, lambda: self.status_label.configure(text='Предупреждение: ' + '; '.join(profile_storage.warnings)[:380], text_color='#f8ad88'))
        if (start_minimized or config.get('startup', {}).get('minimized', False)) and self.tray:
            self.withdraw()

    def _build_sidebar(self):
        sidebar = ctk.CTkFrame(self, width=215, fg_color='#141d27', corner_radius=0)
        sidebar.grid(row=0, column=0, sticky='nsew')
        sidebar.grid_propagate(False)
        self.brand_image = ctk.CTkImage(mark_image(128), size=(56, 56))
        brand = ctk.CTkLabel(sidebar, text='', image=self.brand_image)
        brand.pack(anchor='w', padx=24, pady=(24, 0))
        brand.bind('<Button-3>', self._show_quick_menu)
        ctk.CTkLabel(sidebar, text='LOCAL AGENT\nAI STATION', font=('Segoe UI', 16, 'bold'), justify='left').pack(anchor='w', padx=24, pady=(8, 30))
        self.nav_buttons = {}
        for label in ('Станция', 'Оборудование', 'Модели', 'Агенты', 'Службы', 'Настройки'):
            button = ctk.CTkButton(sidebar, text=label, anchor='w', height=42, corner_radius=7,
                fg_color='transparent', hover_color=EDGE, command=lambda name=label: self.show_page(name))
            button.pack(fill='x', padx=12, pady=3)
            self.nav_buttons[label] = button
        ctk.CTkButton(sidebar, text='Меню действий', fg_color=EDGE, command=self._show_quick_menu).pack(fill='x', padx=20, pady=(25, 0))
        ctk.CTkButton(sidebar, text='Свернуть в трей', fg_color='transparent', hover_color=EDGE, command=self.hide_to_tray).pack(fill='x', padx=20, pady=(8, 0))
        ctk.CTkLabel(sidebar, text='Независимый локальный\nцентр управления\n\nv' + VERSION, justify='left',
            text_color=MUTED, font=('Segoe UI', 12)).pack(side='bottom', anchor='w', padx=24, pady=25)

    def page(self, name):
        page = ctk.CTkScrollableFrame(self.body, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        self.pages[name] = page
        return page

    def show_page(self, name):
        if name == 'Настройки':
            self._refresh_startup_choices()
        for page in self.pages.values():
            page.grid_remove()
        if name in self.pages:
            self.pages[name].grid(row=2, column=0, sticky='nsew')
        self.heading.configure(text={'Станция': 'Обзор станции'}.get(name, name))
        for label, button in self.nav_buttons.items():
            button.configure(fg_color=EDGE if label == name else 'transparent', text_color=ACCENT if label == name else TEXT)

    def _set_window_icon(self):
        try:
            self.iconbitmap(str(resource_path('assets/brand/station.ico')))
        except tk.TclError:
            from PIL import ImageTk
            self.window_icon = ImageTk.PhotoImage(mark_image(64))
            self.iconphoto(True, self.window_icon)

    def card(self, parent, title, description=''):
        frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        frame.pack(fill='x', padx=1, pady=(0, 14))
        ctk.CTkLabel(frame, text=title, font=('Segoe UI', 18, 'bold'), anchor='w').pack(fill='x', padx=20, pady=(15, 2))
        if description:
            ctk.CTkLabel(frame, text=description, text_color=MUTED, wraplength=760, justify='left', anchor='w').pack(fill='x', padx=20, pady=(0, 12))
        return frame

    def button(self, parent, text, command, primary=False, width=140):
        button = ctk.CTkButton(parent, text=text, command=command, height=36, corner_radius=7,
            width=width,
            fg_color=ACCENT if primary else EDGE, text_color=BG if primary else TEXT,
            hover_color='#71e2c2' if primary else '#364a60')
        button.pack(side='left', padx=(0, 10), pady=14)
        self.controls.append(button)
        return button

    def row(self, parent):
        frame = ctk.CTkFrame(parent, fg_color='transparent')
        frame.pack(fill='x', padx=20)
        return frame

    def combo(self, parent, values, value=None, width=365):
        def label(id):
            for collection in (profile_storage.gpu_profiles, profile_storage.model_profiles, profile_storage.station_presets):
                if id in collection:
                    return collection[id].name
            if id in self.controller.adapters:
                return self.controller.adapters[id].manifest.get('name', id)
            return self.controller.frontends.get(id, {}).get('name', id)
        widget = ProfileCombo(parent, resolver=label, values=values or ['—'], width=width, height=36, state='readonly',
            fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL)
        widget.pack(side='left', padx=(0, 12), pady=14)
        widget.set(value if value in values else values[0] if values else '—')
        return widget

    def _build_station(self):
        page = self.page('Станция')
        stats = ctk.CTkFrame(page, fg_color='transparent')
        stats.pack(fill='x', pady=(0, 14))
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform='stats')
        self.dashboard_stats = {}
        for column, (key, label) in enumerate((('backend', 'СЕРВЕР МОДЕЛЕЙ · LLAMA-SWAP'), ('model', 'ЗАГРУЖЕННАЯ МОДЕЛЬ'), ('agent', 'АГЕНТ'))):
            tile = ctk.CTkFrame(stats, fg_color=PANEL, border_color=EDGE, border_width=1, corner_radius=12)
            tile.grid(row=0, column=column, sticky='nsew', padx=(0, 10 if column < 2 else 0))
            ctk.CTkLabel(tile, text=label, font=('Segoe UI', 10, 'bold'), text_color=MUTED, anchor='w').pack(fill='x', padx=15, pady=(12, 0))
            value = ctk.CTkLabel(tile, text='Проверка…', font=('Segoe UI', 18, 'bold'), anchor='w', wraplength=230, justify='left')
            value.pack(fill='x', padx=15, pady=(4, 1))
            detail = ctk.CTkLabel(tile, text='Ожидание данных', font=('Segoe UI', 11), text_color=MUTED, anchor='w',
                justify='left', wraplength=300)
            detail.pack(fill='x', padx=15, pady=(0, 12))
            self.dashboard_stats[key] = (value, detail)
        card = self.card(page, 'Модель и агент', 'Загрузка модели при необходимости сама запускает сервер моделей llama-swap.')
        row = self.row(card)
        self.model_combo = self.combo(row, [id for id, m in profile_storage.model_profiles.items() if id != 'none' and m.status != 'disabled'],
            config.get('selected_model_profile', config.get('active_model_profile')), 395)
        self.button(row, 'Загрузить модель', lambda: self._run_selection(self.model_combo, gpu_mode_manager.apply_model_profile_only, 'Загрузка модели'), True, width=150)
        self.button(row, 'Выгрузить модель', lambda: self.worker(lambda: gpu_mode_manager.apply_model_profile_only('none'), label='Выгрузка модели'), width=150)
        row = self.row(card)
        self.runtime_combo = self.combo(row, list(self.controller.adapters), config.get('primary_agent_runtime'), 175)
        self.runtime_combo.configure(command=lambda value: self._select_runtime(self.runtime_combo.get()))
        self.frontend_combo = self.combo(row, [], width=208)
        self.button(row, 'Запустить агента', self._launch_frontend, True, width=150)
        self.button(row, 'Остановить агента', lambda: self._run_selection(self.frontend_combo, self.controller.stop_frontend, 'Остановка агента'), width=150)
        self.combination_label = ctk.CTkLabel(card, text='Выберите модель и нажмите «Загрузить модель».', anchor='w', justify='left', text_color=MUTED)
        self.combination_label.pack(fill='x', padx=20, pady=(0, 12))
        ctk.CTkLabel(page, text='Оборудование сейчас', anchor='w', font=('Segoe UI', 18, 'bold')).pack(fill='x', pady=(0, 8))
        self.dashboard_gpu_area = ctk.CTkFrame(page, fg_color='transparent')
        self.dashboard_gpu_area.pack(fill='x', pady=(0, 14))
        self.dashboard_gpu_area.grid_columnconfigure((0, 1, 2), weight=1, uniform='gpu')
        self.dashboard_empty = ctk.CTkLabel(self.dashboard_gpu_area, text='Обнаружение GPU…', text_color=MUTED)
        self.dashboard_empty.grid(row=0, column=0, columnspan=3)
        card = self.card(page, 'Пресеты (GPU + модель + агент)')
        row = self.row(card)
        self.preset_combo = self.combo(row, list(profile_storage.station_presets), config.get('active_preset'), 395)
        self.button(row, 'Применить пресет', self._apply_preset)
        row = self.row(card)
        self.button(row, 'Сохранить текущее как пресет', self._save_preset, width=230)
        self.button(row, 'Инструкция по проверке', self._testing_guide, width=190)

    def _build_hardware(self):
        page = self.page('Оборудование')
        card = self.card(page, 'Профиль оборудования', 'Выберите режимы GPU. Перед переключением появится список изменений, если подтверждение не отключено. При совпадении режимов переключение не выполняется.')
        row = self.row(card)
        self.gpu_combo = self.combo(row, list(profile_storage.gpu_profiles), config.get('active_gpu_profile'))
        self.button(row, 'Применить GPU-профиль', self._preview_gpu, True, width=190)
        self.gpu_area = ctk.CTkFrame(page, fg_color='transparent')
        self.gpu_area.pack(fill='x')
        self.topology_label = ctk.CTkLabel(page, text='', text_color=MUTED, anchor='w', justify='left', wraplength=790)
        self.topology_label.pack(fill='x', pady=8)

    def _build_models(self):
        page = self.page('Модели')
        card = self.card(page, 'Реестр моделей', 'Профили хранятся отдельно от агентов. Наличие файлов и возможности модели проверяются независимо.')
        row = self.row(card)
        self.button(row, 'Редактировать профили (YAML)', lambda: self.edit_document('model_profiles.yaml'), True, width=230)
        self.button(row, 'Синхронизировать с агентами', self._sync_models, width=230)
        row = self.row(card)
        self.test_model_combo = self.combo(row, list(profile_storage.model_profiles), config.get('selected_model_profile', config.get('active_model_profile')))
        self.button(row, 'Загрузить и проверить модель', self._qualify_model, width=230)
        self.models_text = ctk.CTkTextbox(page, height=430, fg_color=PANEL, font=('Consolas', 13))
        self.models_text.pack(fill='both', expand=True)
        self._refresh_models_text()

    def _build_agents(self):
        page = self.page('Агенты')
        card = self.card(page, 'Запуск и установка агентов', 'Выберите Desktop, терминал или другой интерфейс в карточке и нажмите «Запустить». «Установить» открывает официальные релизы; после установки повторите обнаружение.')
        row = self.row(card)
        self.button(row, 'Найти агенты заново', lambda: self.worker(self.controller.discover_agents, self._agents_discovered, label='Поиск агентов'), True, width=180)
        self.button(row, 'Настройки агентов (YAML)', lambda: self.edit_document('agent_runtimes.yaml'), width=200)
        self.button(row, 'Способы запуска (YAML)', lambda: self.edit_document('agent_frontends.yaml'), width=200)
        self.agent_cards = ctk.CTkFrame(page, fg_color='transparent')
        self.agent_cards.pack(fill='both', expand=True)

    def _build_services(self):
        page = self.page('Службы')
        card = self.card(page, model_server.SERVER_TITLE,
            'llama-swap — сервер, к которому подключаются агенты (Qwen Code, Hermes и другие). Он слушает один порт и по запросу '
            'сам запускает llama-server из llama.cpp с нужной моделью. Конфигурацию Station создаёт из профилей моделей, '
            'поэтому обычно сервер запускается автоматически при загрузке модели.')
        self.server_status_label = ctk.CTkLabel(card, text='Проверка…', anchor='w', font=('Segoe UI', 16, 'bold'))
        self.server_status_label.pack(fill='x', padx=20)
        self.server_detail_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.server_detail_label.pack(fill='x', padx=20, pady=(4, 0))
        row = self.row(card)
        self.button(row, 'Запустить сервер', lambda: self.worker(gpu_mode_manager.start_backend, label='Запуск сервера моделей'), True, width=160)
        self.button(row, 'Остановить сервер', lambda: self.worker(gpu_mode_manager.stop_backend, label='Остановка сервера моделей'), width=160)
        self.button(row, 'Веб-панель ↗', lambda: self._open_url(model_server.local_url() + '/ui'), width=130)
        self.button(row, 'Скопировать адрес', self._copy_server_address, width=160)
        row = self.row(card)
        self.button(row, 'Журнал сервера', self._open_server_log, width=160)
        self.button(row, 'Конфигурация', lambda: self._open_path(model_server.generated_config_path()), width=160)
        self._build_service_connections(page)

    def _open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def _open_path(self, path):
        path = Path(path)
        if path.exists():
            os.startfile(str(path))
        else:
            self.status_label.configure(text=f'Файл ещё не создан: {path}', text_color='#f8ad88')

    def _open_server_log(self):
        log = supervisor.records.get('service:llama-swap', {}).get('log')
        if log and Path(log).is_file():
            os.startfile(log)
        else:
            self.status_label.configure(text='Журнал сервера появится после его первого запуска через Station.', text_color=MUTED)

    def _copy_server_address(self):
        self.clipboard_clear()
        self.clipboard_append(model_server.api_url())
        self.status_label.configure(text=f'Скопирован адрес для агентов: {model_server.api_url()} (OpenAI-совместимый API)', text_color=ACCENT)

    def _build_settings(self):
        page = self.page('Настройки')
        self._build_startup_settings(page)
        self._build_tray_settings(page)
        card = self.card(page, 'Папки и сервер моделей', f'Данные и настройки Station: {data_dir()}')
        self.path_entries = {}
        for key, title, hint in [
                ('runtime_dir', 'Папка движка', 'Папка, где лежат llama-swap.exe и llama-server.exe (llama.cpp). Файлы ищутся внутри автоматически.'),
                ('models_dir', 'Папка моделей', 'Где хранятся файлы .gguf. В профиле модели можно указывать путь относительно этой папки.'),
                ('workspace', 'Рабочая папка агентов', 'Папка проекта, в которой открываются агенты. Пусто — домашняя папка пользователя.')]:
            ctk.CTkLabel(card, text=title, anchor='w', font=('Segoe UI', 13, 'bold')).pack(fill='x', padx=20, pady=(8, 0))
            ctk.CTkLabel(card, text=hint, anchor='w', text_color=MUTED, wraplength=900, justify='left').pack(fill='x', padx=20)
            row = self.row(card)
            entry = ctk.CTkEntry(row, width=540)
            entry.insert(0, config.get(key, ''))
            entry.pack(side='left', fill='x', expand=True, pady=(4, 4), padx=(0, 12))
            self.path_entries[key] = entry
            ctk.CTkButton(row, text='Обзор', width=90, fg_color=EDGE,
                command=lambda e=entry: self._browse(e, True)).pack(side='left')
        self.runtime_found_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.runtime_found_label.pack(fill='x', padx=20, pady=(6, 0))
        ctk.CTkLabel(card, text='Сеть', anchor='w', font=('Segoe UI', 13, 'bold')).pack(fill='x', padx=20, pady=(12, 0))
        self.lan_var = tk.BooleanVar(value=config.get('llama_swap_lan_access') is True)
        ctk.CTkCheckBox(card, text='Разрешить доступ к серверу моделей из локальной сети (без пароля)', variable=self.lan_var,
            command=self._refresh_path_hints).pack(anchor='w', padx=20, pady=(4, 0))
        self.lan_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.lan_label.pack(fill='x', padx=20, pady=(2, 0))
        self._refresh_path_hints()
        row = self.row(card)
        self.button(row, 'Сохранить', self._save_paths, True)
        self.button(row, 'Открыть папку данных', lambda: self._open_path(data_dir()), width=190)
        card = self.card(page, 'Управление режимами GPU', 'Для переключения WDDM/TCC нужна аппаратная служба. Её установка выполняется один раз с правами администратора.')
        row = self.row(card)
        self.button(row, 'Установить службу переключения GPU', self._install_helper, width=280)
        self.button(row, 'Снова спрашивать перед переключением', self._reset_gpu_confirmation, width=280)
        row = self.row(card)
        self.button(row, 'GPU-профили (YAML)', lambda: self.edit_document('hardware_profiles.yaml'), width=180)
        self.button(row, 'Пресеты (YAML)', lambda: self.edit_document('station_presets.yaml'), width=180)

    def _browse(self, entry, directory):
        path = filedialog.askdirectory(parent=self) if directory else filedialog.askopenfilename(parent=self)
        if path:
            entry.delete(0, 'end'); entry.insert(0, path)

    def _refresh_path_hints(self):
        swap, server = model_server.swap_executable(), model_server.llama_server_executable()
        self.runtime_found_label.configure(
            text=f'Найдено — llama-swap.exe: {swap or "нет"}\nНайдено — llama-server.exe: {server or "нет"}',
            text_color=MUTED if swap and server else '#f8ad88')
        addresses = ', '.join(f'http://{ip}:{model_server.port()}/v1' for ip in model_server.lan_addresses()) or 'сетевые адреса не найдены'
        self.lan_label.configure(text=f'С этого ПК: {model_server.api_url()}. ' + (
            f'Из сети: {addresses}. Брандмауэр Windows должен разрешать порт {model_server.port()}.'
            if self.lan_var.get() else 'Другие компьютеры подключиться не смогут.'))

    def _save_paths(self):
        try:
            values = {key: entry.get().strip() for key, entry in self.path_entries.items()}
            for key in ('runtime_dir', 'models_dir', 'workspace'):
                if values[key] and not Path(values[key]).is_dir():
                    raise ValueError(f'Папка не найдена: {values[key]}')
            lan_changed = (config.get('llama_swap_lan_access') is True) != self.lan_var.get()
            values['llama_swap_lan_access'] = self.lan_var.get()
            if values['runtime_dir'] != config.get('runtime_dir'):
                # A new engine folder replaces explicit executables located elsewhere.
                for key in ('llama_swap_executable', 'llama_server_executable'):
                    old = config.get(key)
                    if old and not os.path.normcase(old).startswith(os.path.normcase(values['runtime_dir'])):
                        values[key] = ''
            config.update(values)
            self._refresh_path_hints()
            message = 'Настройки папок сохранены.'
            if lan_changed and self.backend_online:
                message += ' Сетевой доступ применится после перезапуска сервера моделей (Службы → Остановить сервер → Запустить сервер).'
            self.status_label.configure(text=message, text_color=ACCENT)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def _refresh_models_text(self):
        lines = []
        for model in profile_storage.model_profiles.values():
            if model.id == 'none':
                continue
            weights = model_server.resolve_model_file(model.weights_path)
            exists = bool(weights) and Path(weights).is_file()
            status = {'production': 'основная', 'stable': 'стабильная', 'fallback': 'запасная', 'experimental': 'экспериментальная',
                      'manual': 'ручная', 'disabled': 'отключена'}.get(model.status, model.status)
            lines.append(f'{model.name}\n  id для агентов: {model.backend_model_id} · статус: {status}\n'
                f'  Файл: {weights or "не задан"} — {"найден" if exists else "НЕ НАЙДЕН"}\n'
                f'  Контекст: {model.context:,} токенов · изображения: {"да" if model.vision else "нет"} · вычисления: {model.backend.upper()}\n'
                f'  Проверка запросом: {"пройдена" if model.qualified else "не выполнялась"}\n')
        self.set_text(self.models_text, '\n'.join(lines) or 'Добавьте модель через «Редактировать профили» и укажите путь к файлу весов.')

    def _agents_discovered(self, result):
        if not all(id in self.controller.adapters for id in result):
            self.status_label.configure(text=result.get('Message', 'Не удалось обнаружить агенты.'), text_color='#f8ad88')
            return
        for child in self.agent_cards.winfo_children():
            child.destroy()
        self.controls = [control for control in self.controls if control.winfo_exists()]
        self.agent_launch_widgets = {}
        states = {'SUPPORTED': 'Доступен', 'UNSUPPORTED': 'Недоступен',
                  'DEGRADED': 'Требует внимания', 'ERROR': 'Ошибка'}
        installed = {'INSTALLED': 'Установлен', 'NOT INSTALLED': 'Не установлен',
                     'UNSUPPORTED BY INSTALLED VERSION': 'Не поддерживается этой версией',
                     'SUPPORTED (experimental)': 'Экспериментальный режим'}
        for id, status in sorted(result.items(), key=lambda item: item[0] != config.get('primary_agent_runtime')):
            adapter = self.controller.adapters[id]
            version = (adapter.version or 'версия неизвестна').splitlines()[0]
            title = adapter.manifest.get('name', id)
            has_frontend = any(f.get('status') == 'INSTALLED' for f in self.controller.frontends.values() if f['runtime_id'] == id)
            state = states.get(status['status'], status['status']) if adapter.command else 'Доступен' if has_frontend else 'Не установлен'
            card = ctk.CTkFrame(self.agent_cards, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
            card.pack(fill='x', pady=(0, 10))
            header = ctk.CTkFrame(card, fg_color='transparent')
            header.pack(fill='x', padx=20, pady=(14, 6))
            ctk.CTkLabel(header, text=title, font=('Segoe UI', 17, 'bold'), anchor='w').pack(side='left')
            ctk.CTkLabel(header, text=state, text_color=ACCENT if status['status'] == 'SUPPORTED' else MUTED).pack(side='left', padx=14)
            for option in reversed(self.controller.installation_options(id)):
                ctk.CTkButton(header, text=option['label'], width=180, height=32,
                    fg_color=ACCENT if option['missing'] else EDGE,
                    text_color=BG if option['missing'] else TEXT,
                    command=lambda runtime=id: self.worker(lambda: self.controller.open_installation_page(runtime))).pack(side='right', padx=(8, 0))
            lines = [version] if adapter.version else []
            for frontend in self.controller.frontends.values():
                if frontend['runtime_id'] != id:
                    continue
                lines.append(f'{frontend["name"]}: {installed.get(frontend["status"], frontend["status"])}')
            if status.get('message') and adapter.command and status['status'] != 'SUPPORTED':
                lines.append(status['message'][-500:])
            ctk.CTkLabel(card, text='\n'.join(lines), text_color=MUTED, font=('Segoe UI', 12),
                anchor='w', justify='left', wraplength=760).pack(fill='x', padx=20, pady=(0, 14))
            self._build_agent_launch(card, id)
        self._refresh_frontend_choices()
        self._refresh_agent_launch_states()
        self._refresh_startup_choices()
        self._refresh_tray(rebuild=True)
        self._schedule_startup()

    def _refresh_frontend_choices(self):
        runtime = config.get('primary_agent_runtime')
        choices = [id for id, f in self.controller.frontends.items() if f['runtime_id'] == runtime]
        self.frontend_combo.configure(values=choices or ['—'])
        preferred = config.get('preferred_frontend')
        self.frontend_combo.set(preferred if preferred in choices else choices[0] if choices else '—')

    def _select_runtime(self, runtime):
        def done(result):
            self.runtime_combo.set(config.get('primary_agent_runtime'))
            self._refresh_frontend_choices()
            self._refresh_tray(rebuild=True)
        self.worker(lambda: self.controller.select_runtime(runtime), done)

    def _testing_guide(self):
        self.review('Как проверить Local Agent AI Station', resource_path('docs/TESTING_GUIDE_RU.md').read_text(encoding='utf-8'))

    def review(self, title, content, apply=None, callback=None, label=None):
        window = ctk.CTkToplevel(self)
        window.title(title); window.geometry('900x600'); window.transient(self)
        label = ctk.CTkLabel(window, text=title, font=('Segoe UI', 20, 'bold'))
        label.pack(anchor='w', padx=20, pady=15)
        text = ctk.CTkTextbox(window, font=('Consolas', 12))
        text.pack(fill='both', expand=True, padx=20, pady=(0, 10))
        text.insert('1.0', content); text.configure(state='disabled')
        row = ctk.CTkFrame(window, fg_color='transparent'); row.pack(fill='x', padx=20)
        if apply:
            self.button(row, 'Применить показанные изменения', lambda: (window.destroy(), self.worker(apply, callback, label=label or title)), True, width=260)
        self.button(row, 'Отмена' if apply else 'Закрыть', window.destroy)

    def _apply_preset(self, id=None):
        id = id or self.preset_combo.get()
        self.deiconify(); self.lift()
        preset = profile_storage.get_station_preset(id)
        if preset:
            self.review('Применить пресет', json.dumps(preset.to_dict(), indent=2, ensure_ascii=False), lambda: self.controller.apply_preset(id))

    def _save_preset(self):
        name = simpledialog.askstring(APP_NAME, 'Имя пресета:', parent=self)
        if name:
            gpu_mode_manager.save_current_as_preset(name)
            self.preset_combo.configure(values=list(profile_storage.station_presets))

    def _sync_models(self):
        previews = []
        errors = []
        for id, adapter in self.controller.adapters.items():
            try:
                model = gpu_mode_manager.get_active_model_profile()
                if not model or model.id == 'none':
                    raise ValueError('Сначала загрузите модель: после синхронизации агент проверяется тестовым запросом к ней.')
                preview = self.controller.preview_sync(id, model)
                previews.append((id, preview))
            except Exception as exc:
                errors.append(id + ': ' + str(exc))
        if not previews:
            messagebox.showinfo(APP_NAME, '\n'.join(errors) or 'Нет доступных агентов', parent=self)
            return
        states = {'IN SYNC': 'уже совпадает', 'OUT OF SYNC': 'требуется обновление', 'CUSTOM MODIFIED': 'файл изменён вручную — проверьте разницу'}
        content = '\n\n'.join(self.controller.adapters[id].manifest.get('name', id) + ' — ' + states.get(preview.status, preview.status)
            + '\n' + preview.diff for id, preview in previews)
        content += '\n\n' + '\n'.join(errors)
        def apply():
            results = {}
            workspace = data_dir() / 'qualification-workspace'
            workspace.mkdir(parents=True, exist_ok=True)
            for id, preview in previews:
                adapter = self.controller.adapters[id]
                def smoke(adapter=adapter, preview=preview, id=id):
                    check = adapter.smoke(model, str(workspace), configuration_path=preview.path)
                    if not check.ok:
                        raise RuntimeError(check.message + ': ' + str(check.data))
                    return True
                results[id] = apply_preview(preview, accept_custom=True, smoke=smoke)
            return {'Success': True, 'Message': 'Профили синхронизированы и проверены запросом к модели.', 'Details': results}
        self.review('Синхронизация моделей с агентами', content, apply, label='Синхронизация с агентами')

    def _qualify_model(self):
        selected = self.test_model_combo.get()
        model = profile_storage.get_model_profile(selected)
        if not model or model.id == 'none':
            messagebox.showinfo(APP_NAME, 'Выберите установленную модель.', parent=self)
            return
        def run():
            from ..qualification import qualify_model
            started = gpu_mode_manager.apply_model_profile_only(model.id)
            if not started.get('Success'):
                return started
            report = qualify_model(model, vision=model.vision)
            target = data_dir() / 'qualification' / (__import__('hashlib').sha256(model.id.encode()).hexdigest()[:16] + '.json')
            atomic_write(target, report)
            from copy import deepcopy
            updated = deepcopy(model)
            updated.qualified = report['passed']
            updated.tool_calling = report['checks']['tool_calling'].get('passed', False)
            profile_storage.save_model_profile(updated)
            return {'Success': report['passed'], 'Message': f'Проверка модели «{model.name}» ' + ('пройдена' if report['passed'] else 'не пройдена') + f'. Отчёт: {target}', 'Report': report}
        def done(result):
            self._refresh_models_text()
            self.status_label.configure(text=result['Message'][:400], text_color=ACCENT if result.get('Success') else '#f8ad88')
            self.review('Результаты проверки модели', json.dumps(result, ensure_ascii=False, indent=2))
        self.worker(run, done, label=f'Проверка модели «{model.name}»')

    def _launch_frontend(self, frontend_id=None):
        frontend_id = frontend_id or self.frontend_combo.get()
        frontend = self.controller.frontends.get(frontend_id)
        model = gpu_mode_manager.get_active_model_profile()
        if not frontend:
            return
        if self.controller.frontend_status(frontend_id)['running']:
            self._agent_frontend_done({'Success': True, 'Message': 'Этот интерфейс агента уже запущен Station.'})
            return
        adapter = self.controller.adapters.get(frontend['runtime_id'])
        if model and model.id != 'none' and adapter and adapter.manifest.get('provider_sync', True):
            state = self.controller.model_binding_state(adapter.id, model)
            if state == 'READY_STALE':
                self.worker(lambda: self.controller.launch_frontend(frontend_id), lambda result: self._agent_frontend_done(dict(result,
                    Message=result.get('Message', '') + '. Список моделей в настройках агента устарел — обновите его: Модели → Синхронизировать с агентами.')),
                    label='Запуск ' + frontend['name'])
                return
            try:
                preview = self.controller.preview_sync(adapter.id, model)
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=self)
                return
            if preview.status != 'IN SYNC':
                self.deiconify(); self.lift()
                def apply():
                    workspace = data_dir() / 'qualification-workspace'
                    workspace.mkdir(parents=True, exist_ok=True)
                    def smoke():
                        result = adapter.smoke(model, str(workspace), configuration_path=preview.path)
                        if not result.ok:
                            raise RuntimeError(result.message + ': ' + str(result.data))
                        return True
                    apply_preview(preview, accept_custom=True, smoke=smoke)
                    return self.controller.launch_frontend(frontend_id)
                self.review(f'Подключить модель «{model.name}» к {frontend["name"]}', f'Файл настроек агента: {preview.path}\n'
                    'Будут изменены только блоки, которыми управляет Station. Перед записью создаётся резервная копия.\n\n' + preview.diff, apply, self._agent_frontend_done)
                return
        self.worker(lambda: self.controller.launch_frontend(frontend_id), self._agent_frontend_done, label='Запуск ' + frontend['name'])

    def edit_document(self, filename):
        path = data_dir() / 'config' / filename
        expected = digest(path)
        document = read_document(path, [])
        if not path.exists():
            for name, (namefile, _, _) in profile_storage.TYPES.items():
                if namefile == filename:
                    document = [p.to_dict() for p in getattr(profile_storage, name).values()]
        import yaml
        window = ctk.CTkToplevel(self); window.title(filename); window.geometry('950x660'); window.transient(self)
        editor = ctk.CTkTextbox(window, font=('Consolas', 12)); editor.pack(fill='both', expand=True, padx=16, pady=16)
        editor.insert('1.0', yaml.safe_dump(document, allow_unicode=True, sort_keys=False))
        def save():
            try:
                from ..storage import UniqueLoader
                value = yaml.load(editor.get('1.0', 'end'), Loader=UniqueLoader)
                if not isinstance(value, list):
                    raise ValueError('Ожидается YAML-список профилей')
                from ..validation import validate_registry
                validate_registry(filename, value)
                if filename == 'model_profiles.yaml':
                    original = {row['id']: row for row in document}
                    for row in value:
                        if row != original.get(row['id']):
                            row['qualified'] = False
                atomic_write(path, value, expected_digest=expected)
                profile_storage.load_all()
                if filename == 'services.yaml':
                    self._refresh_service_cards()
                if filename in ('agent_frontends.yaml', 'agent_runtimes.yaml'):
                    self.worker(self.controller.discover_agents, self._agents_discovered)
                self.model_combo.configure(values=list(profile_storage.model_profiles))
                self.test_model_combo.configure(values=list(profile_storage.model_profiles))
                self.gpu_combo.configure(values=list(profile_storage.gpu_profiles))
                self.preset_combo.configure(values=list(profile_storage.station_presets))
                self._refresh_models_text()
                self._refresh_tray(rebuild=True)
                window.destroy()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=window)
        ctk.CTkButton(window, text='Проверить и сохранить', command=save, fg_color=ACCENT, text_color=BG).pack(pady=(0, 16))

    def _install_helper(self):
        import ctypes
        import csv
        import io
        import subprocess
        from ..paths import resource_path
        from ..hardware import hidden_options
        script = resource_path('src/services/install_helper.ps1')
        system = Path(os.environ['SystemRoot']) / 'System32'
        identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
            capture_output=True, text=True, check=True, timeout=5, **hidden_options()).stdout
        sid = next(csv.reader(io.StringIO(identity.strip())))[1]
        # Only the reviewed, fixed installer is elevated; daily app execution is not.
        params = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-AllowedUserSid', sid])
        powershell = system / 'WindowsPowerShell/v1.0/powershell.exe'
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', str(powershell), params, None, 0)
        if result <= 32:
            messagebox.showerror(APP_NAME, 'Установка службы отменена или не удалась.', parent=self)
        else:
            self.status_label.configure(text='Открыт установщик службы переключения GPU. Подтвердите запрос прав администратора.', text_color=MUTED)

    def _run_selection(self, combo, action, label=None):
        selected = combo.get()
        self.worker(lambda: action(selected), label=label)

    def worker(self, action, callback=None, label=None):
        if self.busy:
            self.status_label.configure(text=f'Дождитесь завершения: {self.busy_label}.', text_color=MUTED)
            return
        self.busy = True
        self.busy_label = label or 'текущее действие' 
        self._refresh_agent_launch_states()
        for control in self.controls:
            if control.winfo_exists():
                control.configure(state='disabled')
        self.status_label.configure(text=(label + '…') if label else 'Выполняется…', text_color=MUTED)
        def work():
            try:
                self.events.put(('result', action(), callback))
            except Exception as exc:
                logging.getLogger(__name__).exception('Action failed: %s', label)
                self.events.put(('result', {'Success': False, 'Message': ((label + ': ') if label else 'Ошибка: ') + str(exc)}, None))
        threading.Thread(target=work, daemon=True).start()

    def _poll(self):
        while not self.stop_event.is_set():
            try:
                top = topology_engine.discover_live()
                backend = pm.is_llama_swap_running()
                backend_info = pm.backend_info(backend)
                service_states = shared_services.poll(allow_restart=not self.busy and gpu_mode_manager.state == 'IDLE')
                running = []
                if backend:
                    try:
                        rows = gpu_mode_manager.engine.request('/running').get('running', [])
                        running = [r['model'] for r in rows if r.get('state') == 'ready']
                        backend_info['loading'] = [r['model'] for r in rows if r.get('state') != 'ready']
                    except Exception:
                        pass
                if not self.events.full():
                    frontend_running = {}
                    for id, frontend in list(self.controller.frontends.items()):
                        frontend_running[id] = self.controller.frontend_status(id)['running']
                    self.events.put(('telemetry', (top, backend, running, service_states, frontend_running, backend_info), None))
            except Exception as exc:
                if not self.events.full():
                    self.events.put(('poll_error', str(exc), None))
            self.stop_event.wait(max(2, config.get('poll_interval_sec', 3)))

    def _drain_events(self):
        try:
            while True:
                kind, value, callback = self.events.get_nowait()
                if kind == 'result':
                    self.busy = False
                    for control in self.controls:
                        if control.winfo_exists():
                            control.configure(state='normal')
                    if callback:
                        self.status_label.configure(text=result_message(value)[:400] if value.get('Message') else '',
                            text_color=ACCENT if value.get('Success', True) else '#f8ad88')
                        callback(value)
                    else:
                        self.status_label.configure(text=result_message(value)[:400],
                            text_color=ACCENT if value.get('Success') else '#f8ad88')
                    self._refresh_agent_launch_states()
                elif kind == 'startup_progress':
                    self.startup_banner_label.configure(text=value)
                elif kind == 'telemetry':
                    self._update_telemetry(*value)
                elif kind == 'poll_error' and not self.busy:
                    self.status_label.configure(text=value[:150], text_color='#f8ad88')
                elif kind == 'show':
                    self.deiconify(); self.lift()
                elif kind == 'quit':
                    self.quit_app()
                    return
                elif kind == 'tray_action':
                    self._dispatch_tray(*value)
                    if self.stop_event.is_set():
                        return
                elif kind == 'tray_error':
                    self.deiconify()
                    self.status_label.configure(text='Трей недоступен: ' + value[:120], text_color='#f8ad88')
        except queue.Empty:
            pass
        except Exception as exc:
            self.busy = False
            logging.getLogger(__name__).exception('UI event failed')
            if not self.stop_event.is_set():
                self.status_label.configure(text='Ошибка: ' + str(exc)[:140], text_color='#f8ad88')
        finally:
            if not self.stop_event.is_set():
                self.after(100, self._drain_events)

    def _update_telemetry(self, top, backend, running, service_states, frontend_running=None, backend_info=None):
        self.topology = top
        self.backend_online = backend
        self.backend_info = backend_info
        self.frontend_running = frontend_running or {}
        self._refresh_agent_launch_states()
        self.ready_model_ids = {m.id for m in profile_storage.model_profiles.values() if m.backend_model_id in running}
        self.ready_model_names = [m.name for id, m in profile_storage.model_profiles.items() if id in self.ready_model_ids]
        ids = tuple(d.uuid for d in top.devices)
        if tuple(self.gpu_widgets) != ids:
            for child in self.gpu_area.winfo_children():
                child.destroy()
            self.gpu_widgets = {}
            for device in top.devices:
                card = self.card(self.gpu_area, device.name, device.uuid)
                label = ctk.CTkLabel(card, text='', anchor='w', font=('Segoe UI', 14)); label.pack(fill='x', padx=20, pady=(0, 16))
                self.gpu_widgets[device.uuid] = label
        for d in top.devices:
            self.gpu_widgets[d.uuid].configure(text=f'{d.driver_mode}    {number(d.temp_c, "°C")}    Нагрузка {number(d.load_percent, "%")}    '
                f'VRAM свободно {number(d.vram_free_mib)} / {number(d.vram_total_mib)} MiB' + ('    Дисплей активен' if d.display_active else ''))
        p2p = str(top.cuda_p2p_cliques) if top.cuda_p2p_cliques else 'нет' if top.p2p_verified else 'неизвестно'
        nvlink = str(top.physical_nvlink_cliques) if top.physical_nvlink_cliques else 'нет' if top.nvlink_verified else 'неизвестно'
        self.topology_label.configure(text=f'GPU: {top.gpu_count}  ·  CUDA P2P: {p2p}  ·  NVLink: {nvlink}\nТрафик NVLink не измерялся.' +
            ('\n' + top.discovery_error if top.discovery_error else '') + ('\nВидеокарты не обнаружены: доступен только CPU.' if not top.devices else ''))
        self.combination_label.configure(text=(f'Модель готова. Адрес для агентов: {model_server.api_url()}, id модели: {", ".join(running)}' if running else
            f'Сервер моделей работает ({model_server.api_url()}), модель не загружена. Выберите модель и нажмите «Загрузить модель».' if backend else
            'Выберите модель и нажмите «Загрузить модель».'))
        self._update_server_card(backend_info)
        self._update_dashboard(top, backend, running)
        self._refresh_service_cards(service_states)
        signature = tuple((d.uuid, d.driver_mode) for d in top.devices)
        changed = signature != self.tray_signature
        if changed:
            self._update_tray_sources()
            self.tray_signature = signature
        self._preview_tray_preferences()
        self._refresh_tray(rebuild=changed)

    def _update_server_card(self, info):
        if not info or not hasattr(self, 'server_status_label'):
            return
        self.server_status_label.configure(text=pm.describe(info), text_color=ACCENT if info['online'] else MUTED)
        lines = [f'Адрес для агентов на этом ПК: {info["api_url"]}']
        if info['lan_urls']:
            lines.append('Адреса из локальной сети: ' + ', '.join(info['lan_urls']))
        elif info['online'] and info['owned']:
            lines.append('Доступ из сети выключен (включается в «Настройки → Папки и сервер моделей»).')
        lines.append(f'Конфигурация: {info["config_path"]}')
        if info['stale_config']:
            lines.append('Внимание: сервер запущен со старой конфигурацией. Остановите и запустите его снова.')
        lines.append(f'Программа: {info["executable"] or "llama-swap.exe не найден — укажите папку движка в настройках"}')
        if info.get('loading'):
            lines.append('Загружается модель: ' + ', '.join(info['loading']))
        self.server_detail_label.configure(text='\n'.join(lines))

    def _update_dashboard(self, top, backend, running):
        value, detail = self.dashboard_stats['backend']
        info = self.backend_info or {}
        value.configure(text=('Работает' if info.get('owned') else 'Работает (не Station)') if backend else 'Остановлен',
            text_color=ACCENT if backend else MUTED)
        if backend:
            lines = [info.get('listen') or info.get('url', model_server.local_url()).removeprefix('http://')]
            lines[0] += f' · PID {info["pid"]}' if info.get('pid') else ''
            if info.get('lan_urls'):
                lines.append('Сеть: ' + ', '.join(u.removeprefix('http://').removesuffix('/v1') for u in info['lan_urls']))
            detail.configure(text='\n'.join(lines))
        else:
            ready = model_server.swap_executable() and model_server.llama_server_executable()
            detail.configure(text=f'Запустится при загрузке модели на {model_server.local_url().removeprefix("http://")}' if ready else
                'Не найден движок: укажите папку в «Настройках»')
        value, detail = self.dashboard_stats['model']
        names = self.ready_model_names or running
        title = names[0] if names else 'Не загружена'
        value.configure(text=title if len(title) < 45 else title[:42]+'…', text_color=TEXT if names else MUTED)
        loading = (self.backend_info or {}).get('loading') or []
        profile = next((m for m in profile_storage.model_profiles.values() if m.backend_model_id in running), None)
        detail.configure(text=(f'id: {profile.backend_model_id} · контекст {profile.context // 1024}K' if profile else
            f'Готово моделей: {len(running)}') if running else ('Загружается: ' + ', '.join(loading)) if loading else 'Выберите модель ниже')
        value, detail = self.dashboard_stats['agent']
        fid = self.frontend_combo.get()
        frontend = self.controller.frontends.get(fid, {})
        value.configure(text=frontend.get('name', 'Не выбран'))
        detail.configure(text='Запущен из Station' if self.frontend_running.get(fid) else
            'Готов к запуску' if frontend.get('status') == 'INSTALLED' else 'Установите в разделе «Агенты»')
        ids = tuple(d.uuid for d in top.devices)
        if self.dashboard_gpu_ids != ids:
            self.dashboard_gpu_ids = ids
            for child in self.dashboard_gpu_area.winfo_children():
                child.destroy()
            self.dashboard_gpus = {}
            for index, d in enumerate(top.devices):
                tile = ctk.CTkFrame(self.dashboard_gpu_area, fg_color=PANEL, border_width=1, border_color=EDGE, corner_radius=12)
                tile.grid(row=index//3, column=index%3, sticky='nsew', padx=(0, 8), pady=(0, 8))
                name = d.name.replace('NVIDIA ', '').replace('Intel(R) ', '')
                ctk.CTkLabel(tile, text=f'{d.index} · {name}', anchor='w', font=('Segoe UI', 13, 'bold'), wraplength=240).pack(fill='x', padx=14, pady=(12, 0))
                mode = ctk.CTkLabel(tile, text='', anchor='w', text_color=MUTED, font=('Segoe UI', 11))
                mode.pack(fill='x', padx=14)
                temp = ctk.CTkLabel(tile, text='', anchor='w', font=('Segoe UI', 17, 'bold'))
                temp.pack(fill='x', padx=14, pady=4)
                memory = ctk.CTkLabel(tile, text='', anchor='w', text_color=MUTED, font=('Segoe UI', 11))
                memory.pack(fill='x', padx=14)
                bar = ctk.CTkProgressBar(tile, height=5, fg_color=EDGE, progress_color=ACCENT)
                bar.pack(fill='x', padx=14, pady=(7, 14))
                self.dashboard_gpus[d.uuid] = (mode, temp, memory, bar)
            if not ids:
                ctk.CTkLabel(self.dashboard_gpu_area, text='GPU не обнаружены. Доступны CPU-профили и внешние серверы.',
                    text_color=MUTED).grid(row=0, column=0, columnspan=3, sticky='w')
        for d in top.devices:
            mode, temp, memory, bar = self.dashboard_gpus[d.uuid]
            mode.configure(text=d.driver_mode + (' · подключён экран' if d.display_active else ''))
            temp.configure(text=f'{number(d.temp_c, "°C")}  ·  {number(d.load_percent, "%")} GPU')
            known = d.vram_total_mib and d.vram_used_mib is not None
            memory.configure(text=f'VRAM  {d.vram_used_mib/1024:.1f} / {d.vram_total_mib/1024:.1f} ГБ' if known else 'VRAM: измерение недоступно')
            bar.configure(progress_color=ACCENT if known else EDGE)
            bar.set(max(0, min(1, d.vram_used_mib/d.vram_total_mib)) if known else 0)

    def _dispatch_tray(self, action, value=None):
        if action == 'page':
            self.deiconify(); self.lift(); self.show_page(value)
        elif action == 'logs':
            if (data_dir() / 'logs').exists():
                os.startfile(str(data_dir() / 'logs'))
        elif action == 'quit':
            self.quit_app()
        elif getattr(self, 'gpu_dialog', None) and self.gpu_dialog.winfo_exists():
            self.gpu_dialog.lift()
        elif getattr(self, 'service_editor', None) and self.service_editor.winfo_exists():
            self.service_editor.lift()
        elif self.busy:
            self.status_label.configure(text='Дождитесь завершения текущего действия.')
        elif action == 'model':
            if value != 'none':
                self.model_combo.set(value)
            self.worker(lambda: gpu_mode_manager.apply_model_profile_only(value), label='Выгрузка модели' if value == 'none' else 'Загрузка модели')
        elif action == 'runtime':
            self._select_runtime(value)
        elif action == 'frontend':
            self._launch_frontend(value)
        elif action == 'stop_frontend':
            self.worker(lambda: self.controller.stop_frontend(value))
        elif action == 'gpu':
            self._preview_gpu(value)
        elif action == 'preset':
            self._apply_preset(value)
        elif action == 'save_preset':
            self.deiconify(); self.lift(); self._save_preset()
        elif action == 'install':
            self.worker(lambda: self.controller.open_installation_page(value))
        elif action == 'backend_start':
            self.worker(gpu_mode_manager.start_backend, label='Запуск сервера моделей')
        elif action == 'backend_stop':
            self.worker(gpu_mode_manager.stop_backend, label='Остановка сервера моделей')
        elif action.startswith('service_'):
            self._service_action(action.removeprefix('service_'), value)
        elif action == 'tray_style':
            config.set('tray_style', value)
            self.tray_style_choice.set(value)
            self._preview_tray_preferences()
            self._refresh_tray(rebuild=True)

    @staticmethod
    def set_text(widget, content):
        widget.configure(state='normal'); widget.delete('1.0', 'end'); widget.insert('1.0', content); widget.configure(state='disabled')

    def hide_to_tray(self):
        self.withdraw() if self.tray else self.quit_app()

    def quit_app(self):
        self.startup_cancel.set()
        self.stop_event.set()
        for job in self.tk.splitlist(self.tk.call('after', 'info')):
            self.tk.call('after', 'cancel', job)  # Widgets release their own Tcl commands on destroy.
        for icon in self.tray_icons:
            icon.stop()
        self.destroy()
