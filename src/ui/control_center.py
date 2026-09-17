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
from ..i18n import LANGUAGES, current_language, tr
from .tray_controls import TrayControls
from .gpu_confirmation import GpuControls
from .service_controls import ServiceControls
from .agent_controls import AgentControls
from .startup_controls import StartupControls
from .pages.models import ModelsPage
from .pages.hardware import HardwarePage
from .pages.cluster import ClusterPage
from .pages.monitoring import MonitoringPage
from .pages.logs import LogsPage
from .pages.schedules import SchedulesPage
from .pages.maintenance import MaintenancePage
from .pages.watchdog import WatchdogSection
from .pages.wizard import FirstRunWizard

from .common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING, number


def result_message(value):
    if 'Message' not in value:
        return tr('Готово')
    text = str(value['Message'])
    return {'Frontend process started': tr('Агент запущен'), 'Terminal opened': tr('Терминал агента открыт'),
            'No driver changes required': tr('Переключение GPU не требуется: режимы уже соответствуют профилю'),
            'Agent runtime selected': tr('Основной агент выбран'), 'Frontend selected': tr('Способ запуска агента выбран'),
            'Finish and close active agent sessions before changing GPU drivers': tr('Перед переключением GPU завершите и закройте сеансы агентов')}.get(text, text)

# Stable page ids; titles are translated only for display.
# Sidebar order. Each id needs a _build_<id>() method (see src/ui/pages) and a title below.
PAGE_IDS = ('station', 'hardware', 'models', 'cluster', 'agents', 'services', 'monitoring', 'logs', 'startup', 'schedules', 'maintenance', 'settings')

def page_title(page_id):
    return {'station': tr('Станция'), 'hardware': tr('Оборудование'), 'models': tr('Модели'), 'cluster': tr('LLM-кластер'),
            'agents': tr('Агенты'), 'services': tr('Службы'), 'monitoring': tr('Мониторинг'), 'logs': tr('Журналы'),
            'startup': tr('Автозапуск'), 'schedules': tr('Расписания'), 'maintenance': tr('Обслуживание'),
            'settings': tr('Настройки')}.get(page_id, page_id)

def testing_guide_path(language=None):
    """Testing guide in the interface language, falling back to the Russian original."""
    language = language or current_language()
    suffix = {'en': 'EN', 'uk': 'UK'}.get(language)
    if suffix:
        localized = resource_path(f'docs/TESTING_GUIDE_{suffix}.md')
        if localized.is_file():
            return localized
    return resource_path('docs/TESTING_GUIDE_RU.md')

def restart_command():
    """Command line that starts Station again with the same arguments."""
    import sys
    if getattr(sys, 'frozen', False):
        return [sys.executable, *sys.argv[1:]]
    return [sys.executable, str(Path(sys.argv[0]).resolve()), *sys.argv[1:]]

class StationButton(ctk.CTkButton):
    """A disabled button must look inactive: grey fill and muted text, never a pale accent."""
    def __init__(self, *args, primary=False, **kwargs):
        self.primary = primary
        super().__init__(*args, fg_color=ACCENT if primary else EDGE, text_color=BG if primary else TEXT,
            text_color_disabled='#5d6d7e', hover_color='#71e2c2' if primary else '#364a60', **kwargs)
    def configure(self, require_redraw=False, **kwargs):
        if 'state' in kwargs:
            kwargs['fg_color'] = ACCENT if self.primary and kwargs['state'] == 'normal' else EDGE if kwargs['state'] == 'normal' else '#1f2b37'
        return super().configure(require_redraw=require_redraw, **kwargs)


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

class ControlCenter(ModelsPage, HardwarePage, ClusterPage, MonitoringPage, LogsPage, SchedulesPage, MaintenancePage, WatchdogSection, FirstRunWizard, AgentControls, StartupControls, ServiceControls, GpuControls, TrayControls, ctk.CTk):
    def __init__(self, start_minimized=False, no_tray=False, skip_startup=False):
        super().__init__()
        ctk.set_appearance_mode('dark')
        self.title(tr('{app} — центр управления', app=APP_NAME))
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
        self.frontend_states = {}
        self.dashboard_gpus = {}
        self.dashboard_gpu_ids = None
        self.tray_signature = None
        self.busy = False
        self.busy_label = ''
        self.pages = {}
        # Extension points for page modules: UI-thread telemetry callbacks, background poll callbacks
        # (called on the polling thread with the same snapshot) and extra Settings sections.
        self.telemetry_hooks = []
        self.poll_hooks = []
        self.page_show_hooks = {}
        self.controls = []
        self.gpu_widgets = {}
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        try:
            model_server.fill_missing_folders(profile_storage.model_profiles.values())
        except Exception:
            logging.getLogger(__name__).exception('Cannot fill engine/models folders')
        self._build_sidebar()
        self.body = ctk.CTkFrame(self, fg_color=BG, corner_radius=0)
        self.body.grid(row=0, column=1, sticky='nsew', padx=28, pady=22)
        self.body.grid_columnconfigure(0, weight=1)
        self.body.grid_rowconfigure(2, weight=1)
        self.heading = ctk.CTkLabel(self.body, text=tr('Ваша локальная AI-станция'), font=('Segoe UI', 27, 'bold'), anchor='w')
        self.heading.grid(row=0, column=0, sticky='ew')
        self.status_label = ctk.CTkLabel(self.body, text=tr('Обнаружение оборудования и агентов…'), text_color=MUTED, anchor='w',
            justify='left', wraplength=900)
        self.status_label.grid(row=1, column=0, sticky='ew', pady=(4, 18))
        for page_id in PAGE_IDS:
            getattr(self, '_build_' + page_id)()
        self.show_page('station')
        self.protocol('WM_DELETE_WINDOW', self.hide_to_tray)
        self.after(250, self._set_window_icon)
        self.after(100, self._drain_events)
        self.worker(self.controller.discover_agents, self._agents_discovered)
        threading.Thread(target=self._poll, daemon=True).start()
        if not no_tray:
            self._create_tray()
        if profile_storage.warnings:
            self.after(3000, lambda: self.status_label.configure(text=tr('Предупреждение: {details}', details='; '.join(profile_storage.warnings)[:380]), text_color='#f8ad88'))
        if (start_minimized or config.get('startup', {}).get('minimized', False)) and self.tray:
            self.withdraw()
        # Page modules may define _after_build_<name>(self) for work that needs the finished window.
        for name in sorted(n for n in dir(self) if n.startswith('_after_build_')):
            try:
                getattr(self, name)()
            except Exception:
                logging.getLogger(__name__).exception('After-build hook failed: %s', name)

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
        for page_id in PAGE_IDS:
            button = ctk.CTkButton(sidebar, text=page_title(page_id), anchor='w', height=42, corner_radius=7,
                fg_color='transparent', hover_color=EDGE, command=lambda name=page_id: self.show_page(name))
            button.pack(fill='x', padx=12, pady=3)
            self.nav_buttons[page_id] = button
        ctk.CTkButton(sidebar, text=tr('Меню действий'), fg_color=EDGE, command=self._show_quick_menu).pack(fill='x', padx=20, pady=(25, 0))
        ctk.CTkButton(sidebar, text=tr('Свернуть в трей'), fg_color='transparent', hover_color=EDGE, command=self.hide_to_tray).pack(fill='x', padx=20, pady=(8, 0))
        ctk.CTkLabel(sidebar, text=tr('Независимый локальный\nцентр управления') + '\n\nv' + VERSION, justify='left',
            text_color=MUTED, font=('Segoe UI', 12)).pack(side='bottom', anchor='w', padx=24, pady=25)

    def page(self, name):
        page = ctk.CTkScrollableFrame(self.body, fg_color=BG, corner_radius=0)
        page.grid_columnconfigure(0, weight=1)
        # Modern slim scrollbar with auto-hide: completely disappears when page fits window
        page._scrollbar.configure(width=8, fg_color='transparent', button_color='#243342', button_hover_color='#364a60')
        page._scrollbar.grid_remove()
        orig_set = page._scrollbar.set

        def _auto_scroll_set(first, last):
            orig_set(first, last)
            try:
                f, l = float(first), float(last)
                if f <= 0.0 and l >= 1.0:
                    if page._scrollbar.winfo_ismapped():
                        page._scrollbar.grid_remove()
                else:
                    if not page._scrollbar.winfo_ismapped():
                        page._scrollbar.grid()
            except Exception:
                pass

        page._parent_canvas.configure(yscrollcommand=_auto_scroll_set)
        self.pages[name] = page
        return page

    def show_page(self, name):
        """Show a page by its ASCII id: station, hardware, models, agents, services or settings."""
        if name not in self.pages:
            return
        if name == 'startup':
            self._refresh_startup_choices()
        if name == 'settings':
            self._refresh_helper_status()
        if name in self.page_show_hooks:
            self.page_show_hooks[name]()
        for page in self.pages.values():
            page.grid_remove()
        self.pages[name].grid(row=2, column=0, sticky='nsew')
        self.heading.configure(text=tr('Обзор станции') if name == 'station' else page_title(name))
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
        button = StationButton(parent, text=text, command=command, height=36, corner_radius=7,
            width=width, primary=primary)
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
        page = self.page('station')
        stats = ctk.CTkFrame(page, fg_color='transparent')
        stats.pack(fill='x', pady=(0, 14))
        stats.grid_columnconfigure((0, 1, 2), weight=1, uniform='stats')
        self.dashboard_stats = {}
        for column, (key, label) in enumerate((('backend', tr('СЕРВЕР МОДЕЛЕЙ · LLAMA-SWAP')), ('model', tr('ЗАГРУЖЕННАЯ МОДЕЛЬ')), ('agent', tr('АГЕНТ')))):
            tile = ctk.CTkFrame(stats, fg_color=PANEL, border_color=EDGE, border_width=1, corner_radius=12)
            tile.grid(row=0, column=column, sticky='nsew', padx=(0, 10 if column < 2 else 0))
            ctk.CTkLabel(tile, text=label, font=('Segoe UI', 10, 'bold'), text_color=MUTED, anchor='w').pack(fill='x', padx=15, pady=(12, 0))
            value = ctk.CTkLabel(tile, text=tr('Проверка…'), font=('Segoe UI', 18, 'bold'), anchor='w', wraplength=230, justify='left')
            value.pack(fill='x', padx=15, pady=(4, 1))
            detail = ctk.CTkLabel(tile, text=tr('Ожидание данных'), font=('Segoe UI', 11), text_color=MUTED, anchor='w',
                justify='left', wraplength=300)
            detail.pack(fill='x', padx=15, pady=(0, 12))
            self.dashboard_stats[key] = (value, detail)
        card = self.card(page, tr('Модель и агент'), tr('Загрузка модели при необходимости сама запускает сервер моделей llama-swap.'))
        row = self.row(card)
        self.model_combo = self.combo(row, [id for id, m in profile_storage.model_profiles.items() if id != 'none' and m.status != 'disabled'],
            config.get('selected_model_profile', config.get('active_model_profile')), 395)
        self.dashboard_model_start = self.button(row, tr('Загрузить модель'), lambda: self._run_selection(self.model_combo, gpu_mode_manager.apply_model_profile_only, tr('Загрузка модели')), True, width=150)
        self.model_combo.configure(command=lambda value: self._refresh_dashboard_buttons())
        self.dashboard_model_stop = self.button(row, tr('Выгрузить модель'), lambda: (self._notify_watchdog_stop(), self.worker(lambda: gpu_mode_manager.apply_model_profile_only('none'), label=tr('Выгрузка модели'))), width=150)
        row = self.row(card)
        self.runtime_combo = self.combo(row, list(self.controller.adapters), config.get('primary_agent_runtime'), 175)
        self.runtime_combo.configure(command=lambda value: self._select_runtime(self.runtime_combo.get()))
        self.frontend_combo = self.combo(row, [], width=208)
        self.frontend_combo.configure(command=lambda value: self._refresh_dashboard_buttons())
        self.dashboard_agent_start = self.button(row, tr('Запустить агента'), self._launch_frontend, True, width=150)
        self.dashboard_agent_stop = self.button(row, tr('Остановить агента'), lambda: self._run_selection(self.frontend_combo, self.controller.stop_frontend, tr('Остановка агента')), width=150)
        row = self.row(card)
        # Same field look as the selectors above: a read-only status box, then primary/secondary buttons.
        box = ctk.CTkFrame(row, width=395, height=36, fg_color='#111b25', border_color=EDGE, border_width=2, corner_radius=6)
        box.pack(side='left', padx=(0, 12), pady=14)
        box.pack_propagate(False)
        self.dashboard_server_dot = ctk.CTkLabel(box, text='●', text_color=MUTED, width=18)
        self.dashboard_server_dot.pack(side='left', padx=(10, 2))
        self.dashboard_server_label = ctk.CTkLabel(box, text=tr('Сервер моделей: проверка…'), anchor='w', text_color=TEXT)
        self.dashboard_server_label.pack(side='left', fill='x', expand=True)
        self.dashboard_server_start = self.button(row, tr('Запустить сервер'), lambda: self.worker(gpu_mode_manager.start_backend, label=tr('Запуск сервера моделей')), True, width=150)
        self.dashboard_server_stop = self.button(row, tr('Остановить сервер'), lambda: (self._notify_watchdog_stop(), self.worker(gpu_mode_manager.stop_backend, label=tr('Остановка сервера моделей'))), width=150)
        self.combination_label = ctk.CTkLabel(card, text=tr('Выберите модель и нажмите «Загрузить модель».'), anchor='w', justify='left', text_color=MUTED)
        self.combination_label.pack(fill='x', padx=20, pady=(0, 12))
        ctk.CTkLabel(page, text=tr('Оборудование сейчас'),anchor='w', font=('Segoe UI', 18, 'bold')).pack(fill='x', pady=(0, 8))
        self.dashboard_gpu_area = ctk.CTkFrame(page, fg_color='transparent')
        self.dashboard_gpu_area.pack(fill='x', pady=(0, 14))
        self.dashboard_gpu_area.grid_columnconfigure((0, 1, 2), weight=1, uniform='gpu')
        self.dashboard_empty = ctk.CTkLabel(self.dashboard_gpu_area, text=tr('Обнаружение GPU…'), text_color=MUTED)
        self.dashboard_empty.grid(row=0, column=0, columnspan=3)

    def _build_agents(self):
        page = self.page('agents')
        card = self.card(page, tr('Запуск и установка агентов'), tr('В карточке агента выберите способ запуска (Desktop, терминал и т. д.) и нажмите «Запустить агента». «Установить ↗» открывает официальные релизы; после установки нажмите «Найти агенты заново».'))
        row = self.row(card)
        self.button(row, tr('Найти агенты заново'), lambda: self.worker(self.controller.discover_agents, self._agents_discovered, label=tr('Поиск агентов')), True, width=180)
        self.button(row, tr('Настройки агентов (YAML)'), lambda: self.edit_document('agent_runtimes.yaml'), width=200)
        self.button(row, tr('Способы запуска (YAML)'),lambda: self.edit_document('agent_frontends.yaml'), width=200)
        self.agent_cards = ctk.CTkFrame(page, fg_color='transparent')
        self.agent_cards.pack(fill='both', expand=True)

    def _build_services(self):
        page = self.page('services')
        card = self.card(page, model_server.SERVER_TITLE,
            tr('llama-swap — сервер, к которому подключаются агенты (Qwen Code, Hermes и другие). Он слушает один порт и по запросу '
            'сам запускает llama-server из llama.cpp с нужной моделью. Конфигурацию Station создаёт из профилей моделей, '
            'поэтому обычно сервер запускается автоматически при загрузке модели.'))
        self.server_status_label = ctk.CTkLabel(card, text=tr('Проверка…'), anchor='w', font=('Segoe UI', 16, 'bold'))
        self.server_status_label.pack(fill='x', padx=20)
        self.server_detail_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.server_detail_label.pack(fill='x', padx=20, pady=(4, 0))
        row = self.row(card)
        self.button(row, tr('Запустить сервер'), lambda: self.worker(gpu_mode_manager.start_backend, label=tr('Запуск сервера моделей')), True, width=160)
        self.button(row, tr('Остановить сервер'), lambda: (self._notify_watchdog_stop(), self.worker(gpu_mode_manager.stop_backend, label=tr('Остановка сервера моделей'))), width=160)
        self.button(row, tr('Веб-панель ↗'), lambda: self._open_url(model_server.local_url() + '/ui'), width=130)
        self.button(row, tr('Скопировать адрес'), self._copy_server_address, width=160)
        row = self.row(card)
        self.button(row, tr('Журнал сервера'), self._open_server_log, width=160)
        self.button(row, tr('Конфигурация'),lambda: self._open_path(model_server.generated_config_path()), width=160)
        self._build_service_connections(page)

    def _open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def _open_path(self, path):
        path = Path(path)
        if path.exists():
            os.startfile(str(path))
        else:
            self.status_label.configure(text=tr('Файл ещё не создан: {path}', path=path), text_color='#f8ad88')

    def _open_server_log(self):
        log = supervisor.records.get('service:llama-swap', {}).get('log')
        if log and Path(log).is_file():
            os.startfile(log)
        else:
            self.status_label.configure(text=tr('Журнал сервера появится после его первого запуска через Station.'), text_color=MUTED)

    def _copy_server_address(self):
        self.clipboard_clear()
        self.clipboard_append(model_server.api_url())
        self.status_label.configure(text=tr('Скопирован адрес для агентов: {url} (OpenAI-совместимый API)', url=model_server.api_url()), text_color=ACCENT)

    def _build_startup(self):
        self._build_startup_settings(self.page('startup'))

    def _build_language_settings(self, page):
        self.interface_language = current_language()  # The language this window was built with.
        card = self.card(page, tr('Язык интерфейса'))
        row = self.row(card)
        names = {name: code for code, name in LANGUAGES.items()}
        self.language_combo = ctk.CTkComboBox(row, values=list(LANGUAGES.values()), width=240, height=36, state='readonly',
            fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL,
            command=lambda name: self._select_language(names.get(name)))
        self.language_combo.pack(side='left', padx=(0, 12), pady=14)
        self.language_combo.set(LANGUAGES[self.interface_language])
        self.language_restart = ctk.CTkFrame(card, fg_color='transparent')
        ctk.CTkLabel(self.language_restart, text=tr('Язык изменится после перезапуска Station.'), anchor='w',
            text_color='#f8ad88').pack(side='left', padx=(20, 12))
        StationButton(self.language_restart, text=tr('Перезапустить Station'), command=self._restart_app, height=36,
            corner_radius=7, width=190, primary=True).pack(side='left', pady=(0, 14))

    def _select_language(self, code):
        if code not in LANGUAGES:
            return
        config.set('language', code)
        if code == self.interface_language:
            self.language_restart.pack_forget()
        else:
            self.language_restart.pack(fill='x', pady=(0, 4))

    def _restart_app(self):
        """Start a fresh Station process, then quit; the new one waits for this PID before taking the instance lock."""
        import subprocess
        environment = dict(os.environ, LOCAL_AGENT_STATION_RESTART_WAIT=str(os.getpid()))
        environment.pop('LOCAL_AGENT_STATION_LANGUAGE', None)
        try:
            subprocess.Popen(restart_command(), env=environment, close_fds=True, cwd=os.getcwd(),
                creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0))
        except OSError as exc:
            messagebox.showerror(APP_NAME, tr('Не удалось перезапустить Station: {error}', error=exc), parent=self)
            return
        self.quit_app()

    def _build_settings(self):
        page = self.page('settings')
        self.settings_page = page
        self._build_language_settings(page)
        self._build_tray_settings(page)
        card = self.card(page, tr('Папки и сервер моделей'), tr('Данные и настройки Station: {path}', path=data_dir()))
        self.path_entries = {}
        for key, title, hint in [
                ('runtime_dir', tr('Папка движка'), tr('Программы, которые запускают модели: llama-swap.exe и llama-server.exe (llama.cpp). Встроенный движок ставится вместе со Station — оставьте поле пустым, чтобы использовать его. Другую папку указывайте, только если нужна своя сборка llama.cpp.')),
                ('models_dir', tr('Папка моделей'), tr('Где лежат файлы моделей .gguf. Если в профиле модели указано только имя файла, он ищется здесь.'))]:
            ctk.CTkLabel(card, text=title, anchor='w', font=('Segoe UI', 13, 'bold')).pack(fill='x', padx=20, pady=(8, 0))
            ctk.CTkLabel(card, text=hint, anchor='w', text_color=MUTED, wraplength=900, justify='left').pack(fill='x', padx=20)
            row = self.row(card)
            entry = ctk.CTkEntry(row, width=540)
            entry.insert(0, config.get(key, ''))
            entry.pack(side='left', fill='x', expand=True, pady=(4, 4), padx=(0, 12))
            self.path_entries[key] = entry
            ctk.CTkButton(row, text=tr('Обзор'), width=90, fg_color=EDGE,
                command=lambda e=entry: self._browse(e, True)).pack(side='left')
        self.runtime_found_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.runtime_found_label.pack(fill='x', padx=20, pady=(6, 0))
        ctk.CTkLabel(card, text=tr('Сеть'), anchor='w', font=('Segoe UI', 13, 'bold')).pack(fill='x', padx=20, pady=(12, 0))
        self.lan_var = tk.BooleanVar(value=config.get('llama_swap_lan_access') is True)
        ctk.CTkCheckBox(card, text=tr('Разрешить доступ к серверу моделей из локальной сети (без пароля)'), variable=self.lan_var,
            command=self._refresh_path_hints).pack(anchor='w', padx=20, pady=(4, 0))
        self.lan_label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color=MUTED, wraplength=900)
        self.lan_label.pack(fill='x', padx=20, pady=(2, 0))
        self._refresh_path_hints()
        row = self.row(card)
        self.button(row, tr('Сохранить'), self._save_paths, True)
        self.button(row, tr('Открыть папку данных'), lambda: self._open_path(data_dir()), width=190)
        card = self.card(page, tr('Управление режимами GPU'), tr('Переключать видеокарты между WDDM и TCC может только администратор. '
            'Служба переключения ставится установщиком Station и выполняет только эту операцию, без окна подтверждения прав. '
            'Если службы нет, при каждом переключении Windows один раз спросит права администратора.'))
        self.helper_status_label = ctk.CTkLabel(card, text=tr('Проверка службы…'), anchor='w', justify='left', wraplength=900)
        self.helper_status_label.pack(fill='x', padx=20)
        row = self.row(card)
        self.button(row, tr('Установить или восстановить службу'), self._install_helper, width=280)
        self.button(row, tr('Снова спрашивать перед переключением'), self._reset_gpu_confirmation, width=280)
        row = self.row(card)
        self.button(row, tr('GPU-профили (YAML)'), lambda: self.edit_document('hardware_profiles.yaml'), width=180)
        # Page modules add Settings cards by defining _settings_section_<name>(self, page).
        for name in sorted(n for n in dir(self) if n.startswith('_settings_section_')):
            getattr(self, name)(page)

    def _refresh_helper_status(self):
        from ..services.gpu_mode_client import gpu_service_client
        def check():
            try:
                return gpu_service_client.is_service_running(), gpu_service_client.is_service_installed()
            except Exception:
                return False, False
        running, installed = check()
        self.helper_status_label.configure(
            text=tr('Служба работает: переключение режимов GPU без запроса прав.') if running else
                 tr('Служба установлена, но не запущена. Нажмите «Установить или восстановить службу».') if installed else
                 tr('Служба не установлена. Переключение режимов будет каждый раз запрашивать права администратора.'),
            text_color=ACCENT if running else '#f8ad88')

    def _browse(self, entry, directory):
        path = filedialog.askdirectory(parent=self) if directory else filedialog.askopenfilename(parent=self)
        if path:
            entry.delete(0, 'end'); entry.insert(0, path)

    def _refresh_path_hints(self):

        swap, server = model_server.swap_executable(), model_server.llama_server_executable()
        bundled = model_server.bundled_runtime_dir()
        engine = model_server.runtime_dir()
        source = (tr('встроенный движок Station') if bundled and engine == bundled else tr('папка {path}', path=engine) if engine else tr('папка не выбрана'))
        cuda_name, cuda_path = model_server.cuda_runtime_status()
        cuda = ('\n' + tr('CUDA: {name} найден ({path})', name=cuda_name, path=cuda_path.parent) if cuda_path else
                '\n' + tr('CUDA: не найден {name} — установите NVIDIA CUDA Toolkit этой версии', name=cuda_name) if cuda_name else '')
        missing = tr('не найден')
        self.runtime_found_label.configure(
            text=tr('Используется: {source}', source=source) + f'\nllama-swap.exe: {swap or missing}\nllama-server.exe: {server or missing}' + cuda,
            text_color=MUTED if swap and server and (cuda_path or not cuda_name) else '#f8ad88')
        addresses = ', '.join(f'http://{ip}:{model_server.port()}/v1' for ip in model_server.lan_addresses()) or tr('сетевые адреса не найдены')
        self.lan_label.configure(text=tr('С этого ПК: {url}.', url=model_server.api_url()) + ' ' + (
            tr('Из сети: {addresses}. Брандмауэр Windows должен разрешать порт {port}.', addresses=addresses, port=model_server.port())
            if self.lan_var.get() else tr('Другие компьютеры подключиться не смогут.')))

    def _save_paths(self):
        try:
            values = {key: entry.get().strip() for key, entry in self.path_entries.items()}
            for key in ('runtime_dir', 'models_dir'):
                if values[key] and not Path(values[key]).is_dir():
                    raise ValueError(tr('Папка не найдена: {path}', path=values[key]))
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
            message = tr('Настройки папок сохранены.')
            if lan_changed and self.backend_online:
                message += ' ' + tr('Сетевой доступ применится после перезапуска сервера моделей (Службы → Остановить сервер → Запустить сервер).')
            self.status_label.configure(text=message, text_color=ACCENT)
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self)

    def _agents_discovered(self, result):
        if not all(id in self.controller.adapters for id in result):
            self.status_label.configure(text=result.get('Message', tr('Не удалось обнаружить агенты.')), text_color='#f8ad88')
            return
        for child in self.agent_cards.winfo_children():
            child.destroy()
        self.controls = [control for control in self.controls if control.winfo_exists()]
        self.agent_launch_widgets = {}
        states = {'SUPPORTED': tr('Установлен'), 'UNSUPPORTED': tr('Не установлен'),
                  'DEGRADED': tr('Требует внимания'), 'ERROR': tr('Ошибка')}
        installed = {'INSTALLED': tr('Установлен'), 'NOT INSTALLED': tr('Не установлен'),
                     'UNSUPPORTED BY INSTALLED VERSION': tr('Не поддерживается этой версией'),
                     'SUPPORTED (experimental)': tr('Экспериментальный режим')}
        for id, status in sorted(result.items(), key=lambda item: item[0] != config.get('primary_agent_runtime')):
            adapter = self.controller.adapters[id]
            version = (adapter.version or tr('версия неизвестна')).splitlines()[0]
            title = adapter.manifest.get('name', id)
            has_frontend = any(f.get('status') == 'INSTALLED' for f in self.controller.frontends.values() if f['runtime_id'] == id)
            state = states.get(status['status'], status['status']) if adapter.command else tr('Установлен') if has_frontend else tr('Не установлен')
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
        self.review(tr('Как проверить Local Agent AI Station'), testing_guide_path().read_text(encoding='utf-8'))

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
            self.button(row, tr('Применить показанные изменения'), lambda: (window.destroy(), self.worker(apply, callback, label=label or title)), True, width=260)
        self.button(row, tr('Отмена') if apply else tr('Закрыть'), window.destroy)


    def _launch_frontend(self, frontend_id=None):
        frontend_id = frontend_id or self.frontend_combo.get()
        frontend = self.controller.frontends.get(frontend_id)
        model = gpu_mode_manager.get_active_model_profile()
        if not frontend:
            return
        current = self.controller.frontend_status(frontend_id)
        if current['running']:
            self._agent_frontend_done({'Success': True, 'Message': (tr('{name} уже запущен из Station', name=frontend['name']) if current['owned'] else
                tr('{name} уже открыт (запущен не из Station)', name=frontend['name'])) + (f', PID {current["pid"]}' if current['pid'] else '') + '.'})
            return
        adapter = self.controller.adapters.get(frontend['runtime_id'])
        folder = None
        if frontend.get('type') in ('terminal', 'vscode'):
            # Ask which project the agent should work on; remember it for the next launch.
            folder = filedialog.askdirectory(parent=self, title=tr('Папка проекта для {name}', name=frontend['name']),
                initialdir=config.get('last_agent_folder') or str(Path.home()))
            if not folder:
                return
            config.set('last_agent_folder', str(Path(folder)))
        if model and model.id != 'none' and adapter and adapter.manifest.get('provider_sync', True):
            state = self.controller.model_binding_state(adapter.id, model)
            if state == 'READY_STALE':
                self.worker(lambda: self.controller.launch_frontend(frontend_id, folder=folder), lambda result: self._agent_frontend_done(dict(result,
                    Message=result_message(result) + '. ' + tr('Список моделей в настройках агента устарел — обновите его: Модели → Синхронизировать с агентами.'))),
                    label=tr('Запуск: {name}', name=frontend['name']))
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
                    return self.controller.launch_frontend(frontend_id, folder=folder)
                self.review(tr('Подключить модель «{model}» к {agent}', model=model.name, agent=frontend['name']),
                    tr('Файл настроек агента: {path}', path=preview.path) + '\n'
                    + tr('Будут изменены только блоки, которыми управляет Station. Перед записью создаётся резервная копия.') + '\n\n' + preview.diff, apply, self._agent_frontend_done)
                return
        self.worker(lambda: self.controller.launch_frontend(frontend_id, folder=folder), self._agent_frontend_done, label=tr('Запуск: {name}', name=frontend['name']))

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
                    raise ValueError(tr('Ожидается YAML-список профилей'))
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
                self._refresh_models_text()
                self._refresh_tray(rebuild=True)
                window.destroy()
            except Exception as exc:
                messagebox.showerror(APP_NAME, str(exc), parent=window)
        ctk.CTkButton(window, text=tr('Проверить и сохранить'), command=save, fg_color=ACCENT, text_color=BG).pack(pady=(0, 16))

    def _install_helper(self):
        import ctypes
        import csv
        import io
        import subprocess
        from ..paths import resource_path
        from ..hardware import hidden_options
        import sys
        # Installed Station keeps the reviewed script and helper in Program Files, next to the EXE.
        app_dir = Path(sys.executable).parent
        script = app_dir / 'tools/install_helper.ps1' if getattr(sys, 'frozen', False) else resource_path('src/services/install_helper.ps1')
        if not script.is_file():
            messagebox.showerror(APP_NAME, tr('Не найден сценарий установки службы: {path}. Переустановите Station.', path=script), parent=self)
            return
        system = Path(os.environ['SystemRoot']) / 'System32'
        identity = subprocess.run([str(system / 'whoami.exe'), '/user', '/fo', 'csv', '/nh'],
            capture_output=True, text=True, check=True, timeout=5, **hidden_options()).stdout
        sid = next(csv.reader(io.StringIO(identity.strip())))[1]
        # Only the reviewed, fixed installer is elevated; daily app execution is not.
        params = subprocess.list2cmdline(['-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-AllowedUserSid', sid]
            + (['-InstallPath', str(app_dir)] if getattr(sys, 'frozen', False) else []))
        powershell = system / 'WindowsPowerShell/v1.0/powershell.exe'
        result = ctypes.windll.shell32.ShellExecuteW(None, 'runas', str(powershell), params, None, 0)
        if result <= 32:
            messagebox.showerror(APP_NAME, tr('Установка службы отменена или не удалась.'), parent=self)
        else:
            self.status_label.configure(text=tr('Открыт установщик службы переключения GPU. Подтвердите запрос прав администратора.'), text_color=MUTED)

    def _run_selection(self, combo, action, label=None):
        selected = combo.get()
        self.worker(lambda: action(selected), label=label)

    def worker(self, action, callback=None, label=None):
        if self.busy:
            self.status_label.configure(text=tr('Дождитесь завершения: {action}.', action=self.busy_label), text_color=MUTED)
            return
        self.busy = True
        self.busy_label = label or tr('текущее действие')
        self._refresh_agent_launch_states()
        for control in self.controls:
            if control.winfo_exists():
                control.configure(state='disabled')
        self.status_label.configure(text=(label + '…') if label else tr('Выполняется…'), text_color=MUTED)
        def work():
            try:
                self.events.put(('result', action(), callback))
            except Exception as exc:
                logging.getLogger(__name__).exception('Action failed: %s', label)
                self.events.put(('result', {'Success': False, 'Message': ((label + ': ') if label else tr('Ошибка: ')) + str(exc)}, None))
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
                    processes = self.controller.running_executables()
                    frontend_running = {id: self.controller.frontend_status(id, processes)
                                        for id in list(self.controller.frontends)}
                    self.events.put(('telemetry', (top, backend, running, service_states, frontend_running, backend_info), None))
                for hook in list(self.poll_hooks):
                    try:
                        hook(top, backend_info, running)
                    except Exception:
                        logging.getLogger(__name__).exception('Poll hook failed')
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
                elif kind == 'call':
                    value()
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
                    self.status_label.configure(text=tr('Трей недоступен: {error}', error=value[:120]), text_color='#f8ad88')
        except queue.Empty:
            pass
        except Exception as exc:
            self.busy = False
            logging.getLogger(__name__).exception('UI event failed')
            if not self.stop_event.is_set():
                self.status_label.configure(text=tr('Ошибка: ') + str(exc)[:140], text_color='#f8ad88')
        finally:
            if not self.stop_event.is_set():
                self.after(100, self._drain_events)

    def _update_telemetry(self, top, backend, running, service_states, frontend_running=None, backend_info=None):
        self.topology = top
        self.backend_online = backend
        self.backend_info = backend_info
        self.frontend_states = frontend_running or {}
        self.frontend_running = {id: state['running'] for id, state in self.frontend_states.items()}
        self._refresh_agent_launch_states()
        self.ready_model_ids = {m.id for m in profile_storage.model_profiles.values() if m.backend_model_id in running}
        self.ready_model_names = [m.name for id, m in profile_storage.model_profiles.items() if id in self.ready_model_ids]
        self._update_hardware(top)
        for hook in self.telemetry_hooks:
            try:
                hook(top, backend_info or {}, running)
            except Exception:
                logging.getLogger(__name__).exception('Telemetry hook failed')
        self.combination_label.configure(text=(tr('Модель готова. Адрес для агентов: {url}, id модели: {ids}', url=model_server.api_url(), ids=', '.join(running)) if running else
            tr('Сервер моделей работает ({url}), модель не загружена. Выберите модель и нажмите «Загрузить модель».', url=model_server.api_url()) if backend else
            tr('Выберите модель и нажмите «Загрузить модель».')))
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
        if info and hasattr(self, 'dashboard_server_label'):
            where = (info.get('listen') or info['url'].removeprefix('http://')) if info['online'] else ''
            owner = ('Station' if info['owned'] else tr('не Station')) + (f', PID {info["pid"]}' if info.get('pid') else '')
            self.dashboard_server_label.configure(text=f'llama-swap · {where} · {owner}' if info['online'] else tr('llama-swap · остановлен'))
            self.dashboard_server_dot.configure(text_color=ACCENT if info['online'] else MUTED)
        if not info or not hasattr(self, 'server_status_label'):
            return
        self.server_status_label.configure(text=pm.describe(info), text_color=ACCENT if info['online'] else MUTED)
        lines = [tr('Адрес для агентов на этом ПК: {url}', url=info['api_url'])]
        if info['lan_urls']:
            lines.append(tr('Адреса из локальной сети: {urls}', urls=', '.join(info['lan_urls'])))
        elif info['online'] and info['owned']:
            lines.append(tr('Доступ из сети выключен (включается в «Настройки → Папки и сервер моделей»).'))
        lines.append(tr('Конфигурация: {path}', path=info['config_path']))
        if info['stale_config']:
            lines.append(tr('Внимание: сервер запущен со старой конфигурацией. Остановите и запустите его снова.'))
        lines.append(tr('Программа: {path}', path=info['executable'] or tr('llama-swap.exe не найден — укажите папку движка в настройках')))
        if info.get('loading'):
            lines.append(tr('Загружается модель: {models}', models=', '.join(info['loading'])))
        self.server_detail_label.configure(text='\n'.join(lines))

    def _update_dashboard(self, top, backend, running):
        value, detail = self.dashboard_stats['backend']
        info = self.backend_info or {}
        value.configure(text=(tr('Работает') if info.get('owned') else tr('Работает (не Station)')) if backend else tr('Остановлен'),
            text_color=ACCENT if backend else MUTED)
        if backend:
            lines = [info.get('listen') or info.get('url', model_server.local_url()).removeprefix('http://')]
            lines[0] += f' · PID {info["pid"]}' if info.get('pid') else ''
            if info.get('lan_urls'):
                lines.append(tr('Сеть: {addresses}', addresses=', '.join(u.removeprefix('http://').removesuffix('/v1') for u in info['lan_urls'])))
            detail.configure(text='\n'.join(lines))
        else:
            ready = model_server.swap_executable() and model_server.llama_server_executable()
            detail.configure(text=tr('Запустится при загрузке модели на {address}', address=model_server.local_url().removeprefix('http://')) if ready else
                tr('Не найден движок: укажите папку в «Настройках»'))
        value, detail = self.dashboard_stats['model']
        names = self.ready_model_names or running
        title = names[0] if names else tr('Не загружена')
        value.configure(text=title if len(title) < 45 else title[:42]+'…', text_color=TEXT if names else MUTED)
        loading = (self.backend_info or {}).get('loading') or []
        profile = next((m for m in profile_storage.model_profiles.values() if m.backend_model_id in running), None)
        detail.configure(text=(tr('id: {id} · контекст {context}K', id=profile.backend_model_id, context=profile.context // 1024) if profile else
            tr('Готово моделей: {count}', count=len(running))) if running else tr('Загружается: {models}', models=', '.join(loading)) if loading else tr('Выберите модель ниже'))
        value, detail = self.dashboard_stats['agent']
        fid = self.frontend_combo.get()
        frontend = self.controller.frontends.get(fid, {})
        value.configure(text=frontend.get('name', tr('Не выбран')))
        from .agent_controls import frontend_state_text
        state = self.frontend_states.get(fid, {})
        detail.configure(text=frontend_state_text(frontend, state) if frontend else tr('Выберите агента ниже'))
        value.configure(text_color=ACCENT if state.get('running') else TEXT)
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
                ctk.CTkLabel(self.dashboard_gpu_area, text=tr('GPU не обнаружены. Доступны CPU-профили и внешние серверы.'),
                    text_color=MUTED).grid(row=0, column=0, columnspan=3, sticky='w')
        for d in top.devices:
            mode, temp, memory, bar = self.dashboard_gpus[d.uuid]
            mode.configure(text=d.driver_mode + (' · ' + tr('подключён экран') if d.display_active else ''))
            temp.configure(text=f'{number(d.temp_c, "°C")}  ·  {number(d.load_percent, "%")} GPU')
            known = d.vram_total_mib and d.vram_used_mib is not None
            memory.configure(text=tr('VRAM  {used} / {total} ГБ', used=f'{d.vram_used_mib/1024:.1f}', total=f'{d.vram_total_mib/1024:.1f}') if known else tr('VRAM: измерение недоступно'))
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
            self.status_label.configure(text=tr('Дождитесь завершения текущего действия.'))
        elif action == 'model':
            if value == 'none':
                self._notify_watchdog_stop()
            else:
                self.model_combo.set(value)
            self.worker(lambda: gpu_mode_manager.apply_model_profile_only(value), label=tr('Выгрузка модели') if value == 'none' else tr('Загрузка модели'))
        elif action == 'runtime':
            self._select_runtime(value)
        elif action == 'frontend':
            self._launch_frontend(value)
        elif action == 'stop_frontend':
            self.worker(lambda: self.controller.stop_frontend(value))
        elif action == 'gpu':
            self._preview_gpu(value)
        elif action == 'install':
            self.worker(lambda: self.controller.open_installation_page(value))
        elif action == 'backend_start':
            self.worker(gpu_mode_manager.start_backend, label=tr('Запуск сервера моделей'))
        elif action == 'backend_stop':
            self._notify_watchdog_stop()
            self.worker(gpu_mode_manager.stop_backend, label=tr('Остановка сервера моделей'))
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

    def call_in_ui(self, function):
        """Run function on the Tk thread; safe to call from poll hooks and worker threads."""
        try:
            self.events.put_nowait(('call', function, None))
        except queue.Full:
            logging.getLogger(__name__).warning('UI event queue is full; call dropped')

    def _notify_watchdog_stop(self):
        """Tell watchdog a server stop is intentional so it does not auto-restart."""
        watchdog = getattr(self, 'watchdog', None)
        if watchdog is not None:
            watchdog.expect_stopped()

    def notify(self, message, title='LAAS'):
        """Tray balloon when available, otherwise the status line. Safe to call from the UI thread only."""
        if self.tray and getattr(self.tray, 'HAS_NOTIFICATION', False):
            try:
                self.tray.notify(message, title)
                return
            except Exception:
                logging.getLogger(__name__).exception('Tray notification failed')
        self.status_label.configure(text=message[:400], text_color=WARNING)

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
