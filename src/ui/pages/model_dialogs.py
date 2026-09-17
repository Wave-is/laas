"""Model page windows: add/edit a model, scan the models folder, move it, quick chat, Hugging Face download.

Every window owns a small queue drained on the Tk thread (``Pump``), so worker threads never call Tk
and progress updates are never dropped. File and network work always runs in threads.
"""
import json
import logging
import os
import queue
import threading
import time
import tkinter as tk
import urllib.request
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ...config import config
from ...i18n import tr
from ...paths import APP_NAME
from ...profile_storage import profile_storage
from ... import gguf, hf_download, model_library, model_server
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING

log = logging.getLogger(__name__)
ERROR = '#ff8a8a'
CONTEXT_CHOICES = ('4096', '8192', '16384', '32768', '65536', '131072', '262144')
VERDICT_COLORS = {gguf.FITS: ACCENT, gguf.TIGHT: WARNING, gguf.NO_FIT: ERROR, gguf.UNKNOWN: MUTED}


def status_names():
    return {'production': tr('основная'), 'stable': tr('стабильная'), 'fallback': tr('запасная'),
            'experimental': tr('экспериментальная'), 'manual': tr('ручная'), 'disabled': tr('отключена')}


def gpu_names(count):
    names = {model_library.GPU_ALL: tr('Все подходящие GPU'), model_library.GPU_ONE: tr('Одна GPU с наибольшей памятью')}
    for n in range(2, max(2, count) + 1):
        names[str(n)] = tr('Не меньше {count} GPU', count=n)
    return names


def thousands(value):
    return f'{value:,}'.replace(',', ' ') if isinstance(value, int) else '—'


def running_rows(timeout=3):
    """Rows of GET /running of the local model server; [] when it is not running."""
    try:
        with urllib.request.urlopen(model_server.local_url() + '/running', timeout=timeout) as response:
            return json.loads(response.read() or b'{}').get('running', []) or []
    except Exception:
        return []


def nvidia_count(app):
    topology = getattr(app, 'topology', None)
    return len([d for d in topology.devices if d.vendor == 'NVIDIA']) if topology else 0


class Pump:
    def __init__(self, window, interval=80):
        self.window, self.interval, self.queue = window, interval, queue.Queue()
        self.window.after(self.interval, self._tick)

    def put(self, function):
        self.queue.put(function)

    def _tick(self):
        try:
            if not self.window.winfo_exists():
                return
        except tk.TclError:
            return
        try:
            while True:
                function = self.queue.get_nowait()
                try:
                    function()
                except Exception:
                    log.exception('Dialog update failed')
        except queue.Empty:
            pass
        try:
            self.window.after(self.interval, self._tick)
        except tk.TclError:
            pass


def thread(target):
    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    return worker


def window(app, title, geometry):
    top = ctk.CTkToplevel(app)
    top.title(title)
    top.geometry(geometry)
    top.configure(fg_color=BG)
    top.transient(app)
    top.after(150, top.lift)
    return top


def button(parent, text, command, primary=False, width=150):
    widget = ctk.CTkButton(parent, text=text, command=command, width=width, height=34, corner_radius=7,
                           fg_color=ACCENT if primary else EDGE, text_color=BG if primary else TEXT,
                           text_color_disabled='#5d6d7e', hover_color='#71e2c2' if primary else '#364a60')
    widget.pack(side='left', padx=(0, 10), pady=8)
    return widget


def set_enabled(widget, enabled, primary=False):
    widget.configure(state='normal' if enabled else 'disabled',
                     fg_color=(ACCENT if primary else EDGE) if enabled else '#1f2b37')


def heading(parent, text):
    ctk.CTkLabel(parent, text=text, font=('Segoe UI', 20, 'bold'), anchor='w').pack(fill='x', padx=20, pady=(16, 4))


def field_row(parent, label, width=170):
    row = ctk.CTkFrame(parent, fg_color='transparent')
    row.pack(fill='x', padx=20, pady=3)
    ctk.CTkLabel(row, text=label, width=width, anchor='w', text_color=MUTED).pack(side='left')
    return row


def entry(parent, value='', width=420, **kwargs):
    widget = ctk.CTkEntry(parent, width=width, height=32, fg_color='#111b25', border_color=EDGE, **kwargs)
    if value:
        widget.insert(0, value)
    widget.pack(side='left', padx=(0, 8))
    return widget


def combo(parent, values, value, width=260, command=None, state='readonly'):
    widget = ctk.CTkComboBox(parent, values=list(values), width=width, height=32, state=state, command=command,
                             fg_color='#111b25', border_color=EDGE, button_color=EDGE, dropdown_fg_color=PANEL)
    widget.set(value)
    widget.pack(side='left', padx=(0, 8))
    return widget


# ============================================================ add / edit model

class ModelDialog:
    """Add a model from a .gguf file or edit an existing profile."""

    def __init__(self, app, profile=None, weights_path=None, on_saved=None):
        self.app, self.base, self.on_saved = app, profile, on_saved
        self.models_dir = model_server.models_dir()
        self.info = None
        self.weights_bytes = 0
        self.id_touched = bool(profile)
        self.name_touched = bool(profile)
        title = tr('Изменить модель «{name}»', name=profile.name) if profile else tr('Добавить модель')
        self.window = window(app, title, '860x800')
        self.pump = Pump(self.window)
        heading(self.window, title)
        body = ctk.CTkScrollableFrame(self.window, fg_color=BG)
        body.pack(fill='both', expand=True, padx=4)

        row = field_row(body, tr('Файл модели (.gguf)'))
        self.weights_entry = entry(row, width=470)
        button(row, tr('Выбрать…'), self._pick_weights, width=110)
        self.info_label = ctk.CTkLabel(body, text=tr('Выберите файл, чтобы прочитать метаданные GGUF.'), text_color=MUTED,
                                       anchor='w', justify='left', wraplength=780, font=('Consolas', 12))
        self.info_label.pack(fill='x', padx=20, pady=(2, 10))

        row = field_row(body, tr('Название'))
        self.name_entry = entry(row, profile.name if profile else '', width=470)
        self.name_entry.bind('<KeyRelease>', self._name_changed)
        row = field_row(body, tr('id для агентов'))
        self.id_entry = entry(row, profile.id if profile else '', width=300)
        self.id_entry.bind('<KeyRelease>', lambda event: setattr(self, 'id_touched', True))
        if profile:
            self.id_entry.configure(state='disabled')
            ctk.CTkLabel(row, text=tr('id нельзя изменить: на него ссылаются агенты'), text_color=MUTED).pack(side='left')

        row = field_row(body, tr('Контекст, токенов'))
        self.context_combo = combo(row, CONTEXT_CHOICES, str(profile.context) if profile else '32768', width=150,
                                   command=lambda value: self._update_estimate(), state='normal')
        self.context_combo.bind('<KeyRelease>', lambda event: self._update_estimate())
        self.trained_label = ctk.CTkLabel(row, text='', text_color=MUTED)
        self.trained_label.pack(side='left')

        row = field_row(body, tr('KV-кэш'))
        self.kv_button = ctk.CTkSegmentedButton(row, values=list(gguf.KV_TYPES), command=lambda value: self._update_estimate(),
                                                selected_color=EDGE, selected_hover_color=EDGE, unselected_color='#111b25')
        self.kv_button.set(profile.kv_type if profile and profile.kv_type in gguf.KV_TYPES else 'f16')
        self.kv_button.pack(side='left', padx=(0, 10))
        ctk.CTkLabel(row, text=tr('q8_0 почти без потерь и вдвое меньше f16; q4_0 — вчетверо меньше'), text_color=MUTED).pack(side='left')

        self.gpu_labels = gpu_names(nvidia_count(app))
        current_gpu = model_library.gpu_choice(profile) if profile else model_library.GPU_ALL
        if current_gpu not in self.gpu_labels:
            self.gpu_labels[current_gpu] = tr('Не меньше {count} GPU', count=current_gpu)
        row = field_row(body, tr('Видеокарты'))
        self.gpu_combo = combo(row, self.gpu_labels.values(), self.gpu_labels[current_gpu], width=300,
                               command=lambda value: self._update_estimate())

        row = field_row(body, tr('Изображения'))
        self.vision_var = tk.BooleanVar(value=bool(profile and profile.vision))
        ctk.CTkSwitch(row, text=tr('Модель принимает изображения (нужен mmproj)'), variable=self.vision_var,
                      command=self._update_estimate).pack(side='left')
        row = field_row(body, tr('Файл mmproj'))
        mmproj = model_library.resolve(profile.mmproj_path, self.models_dir) if profile and profile.mmproj_path else None
        self.mmproj_entry = entry(row, str(mmproj) if mmproj else '', width=470)
        self.mmproj_entry.bind('<FocusOut>', lambda event: self._update_estimate())
        button(row, tr('Выбрать…'), self._pick_mmproj, width=110)

        row = field_row(body, tr('Статус'))
        self.status_labels = status_names()
        self.status_combo = combo(row, self.status_labels.values(), self.status_labels.get(profile.status if profile else 'manual'), width=220)

        estimate_card = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=10, border_color=EDGE, border_width=1)
        estimate_card.pack(fill='x', padx=20, pady=14)
        self.verdict_label = ctk.CTkLabel(estimate_card, text=tr('Оценка видеопамяти'), font=('Segoe UI', 17, 'bold'), anchor='w')
        self.verdict_label.pack(fill='x', padx=16, pady=(10, 0))
        self.estimate_label = ctk.CTkLabel(estimate_card, text=tr('Выберите файл модели.'), text_color=MUTED, anchor='w',
                                           justify='left', wraplength=760)
        self.estimate_label.pack(fill='x', padx=16, pady=(2, 12))

        self.error_label = ctk.CTkLabel(self.window, text='', text_color=ERROR, anchor='w', justify='left', wraplength=800)
        self.error_label.pack(fill='x', padx=20)
        actions = ctk.CTkFrame(self.window, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=(0, 10))
        self.save_button = button(actions, tr('Проверить и сохранить'), self._save, True, width=220)
        button(actions, tr('Отмена'), self.window.destroy)

        start = weights_path or (model_library.resolve(profile.weights_path, self.models_dir) if profile else None)
        if start:
            self._load_weights(str(start), initial=bool(profile))

    # ---- file selection
    def _pick_weights(self):
        folder = self.models_dir if self.models_dir and self.models_dir.is_dir() else None
        path = filedialog.askopenfilename(parent=self.window, title=tr('Файл модели .gguf'), initialdir=str(folder) if folder else None,
                                          filetypes=[(tr('Модели GGUF'), '*.gguf'), (tr('Все файлы'), '*.*')])
        if path:
            self._load_weights(path)

    def _pick_mmproj(self):
        current = self.weights_entry.get().strip()
        folder = Path(current).parent if current else self.models_dir
        path = filedialog.askopenfilename(parent=self.window, title=tr('Файл mmproj .gguf'), initialdir=str(folder) if folder else None,
                                          filetypes=[(tr('Модели GGUF'), '*.gguf'), (tr('Все файлы'), '*.*')])
        if path:
            self.mmproj_entry.delete(0, 'end')
            self.mmproj_entry.insert(0, str(Path(path)))
            self.vision_var.set(True)
            self._update_estimate()

    def _load_weights(self, path, initial=False):
        self.weights_entry.delete(0, 'end')
        self.weights_entry.insert(0, str(Path(path)))
        self.info_label.configure(text=tr('Чтение метаданных…'), text_color=MUTED)

        def work():
            error, info, size, projectors = None, None, 0, []
            try:
                if not Path(path).is_file():
                    raise FileNotFoundError(tr('Файл не найден: {path}', path=path))
                info = gguf.read_gguf(path)
                size = gguf.weights_size(path)
                projectors = model_library.find_mmproj(path)
            except Exception as exc:
                error = str(exc)
            self.pump.put(lambda: self._weights_loaded(path, info, size, projectors, error, initial))
        thread(work)

    def _weights_loaded(self, path, info, size, projectors, error, initial):
        self.info, self.weights_bytes = info, size
        if error:
            self.info_label.configure(text=error, text_color=ERROR)
            self._update_estimate()
            return
        if info.is_mmproj:
            self.info_label.configure(text=tr('Это файл mmproj (проектор изображений), а не модель. Выберите файл весов.'), text_color=ERROR)
        else:
            s = info.summary()
            self.info_label.configure(text_color=TEXT, text=tr(
                'Архитектура: {arch} · параметров: {params} · размер файла: {size}\n'
                'Обучена на контексте: {trained} токенов · слоёв: {layers} · голов внимания: {heads} (KV: {kv_heads}) · размер головы: {head_dim}',
                arch=s['architecture'] or '—', params=gguf.format_parameters(s['parameter_count']) + (' (' + s['size_label'] + ')' if s['size_label'] else ''),
                size=hf_download.format_bytes(size), trained=thousands(s['context_length']), layers=s['block_count'] or '—',
                heads=s['head_count'] or '—', kv_heads=s['head_count_kv'] or '—', head_dim=s['key_length'] or '—'))
        if info.context_length:
            self.trained_label.configure(text=tr('максимум модели: {tokens}', tokens=thousands(info.context_length)))
        if not initial:
            existing = set(profile_storage.model_profiles)
            if not self.name_touched:
                self.name_entry.delete(0, 'end')
                self.name_entry.insert(0, model_library.display_name(path))
            if not self.id_touched and not self.base:
                self.id_entry.delete(0, 'end')
                self.id_entry.insert(0, model_library.slugify(model_library.display_name(path), existing))
            if not self.base:
                self.context_combo.set(str(model_library.default_context(info)))
            self.mmproj_entry.delete(0, 'end')
            if projectors:
                self.mmproj_entry.insert(0, str(projectors[0]))
            self.vision_var.set(bool(projectors))
        self._update_estimate()

    def _name_changed(self, event=None):
        self.name_touched = True
        if not self.id_touched and not self.base:
            self.id_entry.delete(0, 'end')
            self.id_entry.insert(0, model_library.slugify(self.name_entry.get(), set(profile_storage.model_profiles)))

    # ---- estimate
    def _gpu_choice(self):
        label = self.gpu_combo.get()
        return next((key for key, text in self.gpu_labels.items() if text == label), model_library.GPU_ALL)

    def _context(self):
        text = self.context_combo.get().replace(' ', '').replace(',', '')
        return int(text) if text.isdigit() else None

    def _draft(self, estimate=None):
        weights = self.weights_entry.get().strip()
        mmproj = self.mmproj_entry.get().strip() or None
        status = next((key for key, text in self.status_labels.items() if text == self.status_combo.get()), 'manual')
        model_id = self.base.id if self.base else self.id_entry.get().strip()
        return model_library.build_profile(
            id=model_id, name=self.name_entry.get(), weights_path=weights, models_dir=self.models_dir,
            mmproj_path=mmproj, context=self._context() or 0, kv_type=self.kv_button.get() or 'f16',
            gpu=self._gpu_choice(), vision=self.vision_var.get(), status=status,
            quant=gguf.quant_from_name(weights, self.info) if weights else '', estimate=estimate, base=self.base)

    def _estimate(self):
        context = self._context()
        if not self.weights_bytes or not context:
            return None, None
        draft = self._draft()
        from ...gpu_modes import gpu_mode_manager
        devices = model_library.assigned_devices(draft, self.app.topology, gpu_mode_manager.get_active_gpu_profile())
        mmproj = self.mmproj_entry.get().strip()
        mmproj_bytes = 0
        if self.vision_var.get() and mmproj:
            try:
                mmproj_bytes = os.path.getsize(mmproj)
            except OSError:
                pass
        estimate = gguf.estimate_vram(self.weights_bytes, self.info, context, self.kv_button.get() or 'f16', mmproj_bytes, max(1, len(devices)))
        return estimate, gguf.fit_verdict(estimate, devices)

    def _update_estimate(self):
        try:
            estimate, fit = self._estimate()
        except Exception as exc:
            self.estimate_label.configure(text=str(exc), text_color=ERROR)
            return
        if not estimate:
            self.verdict_label.configure(text=tr('Оценка видеопамяти'), text_color=TEXT)
            self.estimate_label.configure(text=tr('Выберите файл модели и укажите контекст.'), text_color=MUTED)
            return
        self.verdict_label.configure(text=fit.label() + ' — ' + fit.details(), text_color=VERDICT_COLORS[fit.verdict])
        kv = tr('KV-кэш: {kv} GiB', kv=gguf.gib(estimate.kv_mib)) if estimate.kv_mib is not None else tr('KV-кэш: нет данных в метаданных')
        lines = [tr('Веса: {weights} GiB', weights=gguf.gib(estimate.weights_mib)) + ' · ' +
                 (tr('mmproj: {mmproj} GiB', mmproj=gguf.gib(estimate.mmproj_mib)) + ' · ' if estimate.mmproj_mib else '') +
                 kv + ' · ' + tr('буферы и CUDA: {overhead} GiB', overhead=gib_text(estimate.overhead_mib))]
        topology = self.app.topology
        if topology is None:
            lines.append(tr('Видеокарты ещё не опрошены.'))
        lines.append(tr('Оценка приблизительная; точный расход покажет загрузка модели.'))
        self.estimate_label.configure(text='\n'.join(lines), text_color=MUTED)

    # ---- save
    def _save(self):
        self.error_label.configure(text='')
        try:
            if not self.weights_entry.get().strip():
                raise ValueError(tr('Выберите файл модели .gguf'))
            if self.info is not None and self.info.is_mmproj:
                raise ValueError(tr('Это файл mmproj (проектор изображений), а не модель. Выберите файл весов.'))
            if self._context() is None:
                raise ValueError(tr('Контекст должен быть целым числом'))
            if self.info and self.info.context_length and self._context() > self.info.context_length:
                if not messagebox.askyesno(APP_NAME, tr('Контекст больше, чем модель поддерживает ({tokens}). Всё равно сохранить?',
                                                        tokens=thousands(self.info.context_length)), parent=self.window):
                    return
            if not self.base and self.id_entry.get().strip() in profile_storage.model_profiles:
                raise ValueError(tr('Профиль с id «{id}» уже есть', id=self.id_entry.get().strip()))
            estimate, fit = self._estimate()
            profile = self._draft(estimate)
            if not profile.vision:
                pass
            elif not Path(model_library.resolve(profile.mmproj_path, self.models_dir) or '').is_file():
                raise ValueError(tr('Файл mmproj не найден: {path}', path=profile.mmproj_path))
            model_library.validate_profile(profile, list(profile_storage.model_profiles.values()))
            if fit and fit.verdict == gguf.NO_FIT and not messagebox.askyesno(
                    APP_NAME, tr('По оценке модель не помещается в видеопамять ({details}). Всё равно сохранить профиль?',
                                 details=fit.details()), parent=self.window):
                return
            profile_storage.save_model_profile(profile)
        except Exception as exc:
            self.error_label.configure(text=str(exc))
            return
        self.window.destroy()
        if self.on_saved:
            self.on_saved(profile)


def gib_text(mib):
    return gguf.gib(mib)


# ============================================================ scan folder

class ScanDialog:
    def __init__(self, app, on_add):
        self.app, self.on_add = app, on_add
        self.window = window(app, tr('Модели без профиля'), '900x620')
        self.pump = Pump(self.window)
        self.cancel = threading.Event()
        self.window.protocol('WM_DELETE_WINDOW', self._close)
        heading(self.window, tr('Модели без профиля'))
        folder = model_server.models_dir()
        self.folder = folder
        self.summary = ctk.CTkLabel(self.window, text=tr('Поиск файлов .gguf в {folder}…', folder=folder), text_color=MUTED, anchor='w',
                                    justify='left', wraplength=840)
        self.summary.pack(fill='x', padx=20, pady=(0, 8))
        self.list = ctk.CTkScrollableFrame(self.window, fg_color=BG)
        self.list.pack(fill='both', expand=True, padx=12)
        actions = ctk.CTkFrame(self.window, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=(0, 10))
        button(actions, tr('Обновить'), self.refresh)
        button(actions, tr('Закрыть'), self._close)
        self.refresh()

    def _close(self):
        self.cancel.set()
        self.window.destroy()

    def refresh(self):
        for child in self.list.winfo_children():
            child.destroy()
        if not self.folder or not self.folder.is_dir():
            self.summary.configure(text=tr('Папка моделей не задана или не найдена. Укажите её в «Настройки → Папки и сервер моделей».'), text_color=WARNING)
            return
        profiles = list(profile_storage.model_profiles.values())

        def work():
            try:
                found = model_library.scan_models(self.folder, profiles, self.folder, self.cancel)
                self.pump.put(lambda: self._show(found))
            except model_library.Cancelled:
                pass
            except Exception as exc:
                message = str(exc)
                self.pump.put(lambda: self.summary.configure(text=message, text_color=ERROR))
        thread(work)

    def _show(self, found):
        if not found:
            self.summary.configure(text=tr('В папке {folder} все модели уже добавлены.', folder=self.folder), text_color=ACCENT)
            return
        self.summary.configure(text=tr('Найдено файлов без профиля: {count} (папка {folder}). Файлы mmproj и части разбитых моделей, кроме первой, не показаны.',
                                       count=len(found), folder=self.folder), text_color=MUTED)
        for item in found:
            row = ctk.CTkFrame(self.list, fg_color=PANEL, corner_radius=8, border_color=EDGE, border_width=1)
            row.pack(fill='x', pady=4, padx=4)
            text = ctk.CTkFrame(row, fg_color='transparent')
            text.pack(side='left', fill='x', expand=True, padx=12, pady=6)
            ctk.CTkLabel(text, text=item.path.name, font=('Segoe UI', 14, 'bold'), anchor='w').pack(fill='x')
            relative = model_library.store_path(item.path, self.folder)
            detail = relative + ' · ' + hf_download.format_bytes(item.size_bytes)
            if item.parts > 1:
                detail += ' · ' + tr('частей: {count}', count=item.parts)
            ctk.CTkLabel(text, text=detail, text_color=MUTED, anchor='w').pack(fill='x')
            add = ctk.CTkButton(row, text=tr('Добавить'), width=120, height=32, fg_color=ACCENT, text_color=BG, hover_color='#71e2c2',
                                command=lambda path=item.path, row=row: self.on_add(path, lambda profile, row=row: row.destroy() if row.winfo_exists() else None))
            add.pack(side='right', padx=12)


# ============================================================ move models folder

class MoveDialog:
    def __init__(self, app, on_done):
        self.app, self.on_done = app, on_done
        self.source = model_server.models_dir()
        self.plan = None
        self.cancel = threading.Event()
        self.running = False
        self.window = window(app, tr('Перенести папку моделей'), '860x640')
        self.window.protocol('WM_DELETE_WINDOW', self._close)
        self.pump = Pump(self.window)
        heading(self.window, tr('Перенести папку моделей'))
        ctk.CTkLabel(self.window, text=tr('Файлы копируются и проверяются, затем пути в профилях и папка моделей в настройках обновляются. '
                                          'Оригиналы удаляются только после отдельного подтверждения.'),
                     text_color=MUTED, anchor='w', justify='left', wraplength=800).pack(fill='x', padx=20)
        row = field_row(self.window, tr('Текущая папка'), 140)
        ctk.CTkLabel(row, text=str(self.source or tr('не задана')), anchor='w').pack(side='left')
        row = field_row(self.window, tr('Новая папка'), 140)
        self.destination_entry = entry(row, width=500)
        button(row, tr('Выбрать…'), self._pick, width=110)
        self.text = ctk.CTkTextbox(self.window, font=('Consolas', 12), fg_color=PANEL, height=260)
        self.text.pack(fill='both', expand=True, padx=20, pady=10)
        self.progress = ctk.CTkProgressBar(self.window, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(fill='x', padx=20)
        self.progress_label = ctk.CTkLabel(self.window, text='', text_color=MUTED, anchor='w')
        self.progress_label.pack(fill='x', padx=20)
        actions = ctk.CTkFrame(self.window, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=(0, 10))
        self.check_button = button(actions, tr('Проверить'), self._check)
        self.start_button = button(actions, tr('Перенести'), self._start, True)
        set_enabled(self.start_button, False, True)
        self.cancel_button = button(actions, tr('Закрыть'), self._close)
        self._write(tr('Выберите новую папку и нажмите «Проверить».') if self.source else
                    tr('Папка моделей не задана. Укажите её в «Настройки → Папки и сервер моделей».'))

    def _write(self, content):
        self.text.configure(state='normal')
        self.text.delete('1.0', 'end')
        self.text.insert('1.0', content)
        self.text.configure(state='disabled')

    def _pick(self):
        path = filedialog.askdirectory(parent=self.window, title=tr('Новая папка моделей'))
        if path:
            self.destination_entry.delete(0, 'end')
            self.destination_entry.insert(0, str(Path(path)))
            self._check()

    def _check(self):
        destination = self.destination_entry.get().strip()
        if not self.source or not destination:
            return
        set_enabled(self.start_button, False, True)
        self._write(tr('Проверка файлов и свободного места…'))
        profiles = list(profile_storage.model_profiles.values())

        def work():
            try:
                plan = model_library.plan_move(self.source, destination, profiles, self.source, running_rows())
                self.pump.put(lambda: self._planned(plan))
            except Exception as exc:
                message = str(exc)
                self.pump.put(lambda: self._write(message))
        thread(work)

    def _planned(self, plan):
        self.plan = plan
        lines = [tr('Из: {source}', source=plan.source), tr('В: {destination}', destination=plan.destination),
                 tr('Файлов: {count} · объём: {size} · свободно на диске: {free}', count=len(plan.files),
                    size=hf_download.format_bytes(plan.total_bytes), free=hf_download.format_bytes(plan.free_bytes)), '']
        if plan.profiles:
            lines.append(tr('Будут обновлены пути в профилях:'))
            for profile in plan.profiles:
                lines.append('  ' + profile.name + ': ' + profile.weights_path + (' · ' + profile.mmproj_path if profile.mmproj_path else ''))
        else:
            lines.append(tr('Пути в профилях менять не нужно (относительные пути следуют за папкой моделей).'))
        lines.append(tr('Папка моделей в настройках станет: {destination}', destination=plan.destination))
        if plan.problems:
            lines += ['', tr('Перенос невозможен:')] + ['  • ' + p for p in plan.problems]
        self._write('\n'.join(lines))
        set_enabled(self.start_button, plan.ok and bool(plan.files), True)

    def _start(self):
        plan = self.plan
        if not plan or not plan.ok:
            return
        self.running = True
        set_enabled(self.start_button, False, True)
        set_enabled(self.check_button, False)
        self.cancel_button.configure(text=tr('Отменить копирование'))
        started = time.monotonic()

        def progress(done, total, name):
            elapsed = max(time.monotonic() - started, 1e-6)
            speed = done / elapsed
            eta = (total - done) / speed if speed > 0 else None
            self.pump.put(lambda: self._progress(done, total, name, speed, eta))

        def work():
            try:
                busy = model_library.running_paths_in(running_rows(), plan.source)
                if busy:
                    raise RuntimeError(tr('Сервер моделей сейчас использует файлы из этой папки: {models}. Перенос возможен после выгрузки модели.',
                                          models=', '.join(busy)))
                model_library.copy_files(plan, progress, self.cancel)
                model_library.apply_move(plan, profile_storage, config)
                self.pump.put(self._copied)
            except model_library.Cancelled:
                self.pump.put(lambda: self._failed(tr('Копирование отменено; скопированные файлы удалены, настройки не изменены.')))
            except Exception as exc:
                log.exception('Models folder move failed')
                message = str(exc)
                self.pump.put(lambda: self._failed(tr('Перенос не выполнен: {error}. Скопированные файлы удалены, настройки не изменены.', error=message)))
        thread(work)

    def _progress(self, done, total, name, speed, eta):
        self.progress.set(done / total if total else 1)
        self.progress_label.configure(text=tr('{done} из {total} · {speed}/с · осталось {eta} · {file}', done=hf_download.format_bytes(done),
                                              total=hf_download.format_bytes(total), speed=hf_download.format_bytes(int(speed)),
                                              eta=hf_download.format_eta(eta), file=name))

    def _failed(self, message):
        self.running = False
        self.cancel = threading.Event()
        self.progress.set(0)
        self.progress_label.configure(text=message, text_color=ERROR)
        self.cancel_button.configure(text=tr('Закрыть'))
        set_enabled(self.check_button, True)

    def _copied(self):
        self.running = False
        self.progress.set(1)
        self.cancel_button.configure(text=tr('Закрыть'))
        self.progress_label.configure(text=tr('Файлы скопированы и проверены. Профили и папка моделей обновлены.'), text_color=ACCENT)
        self.on_done()
        self._write(tr('Готово. Новая папка моделей: {destination}\n\nОригиналы оставлены в {source}. '
                       'Новые пути используются при следующей загрузке модели.', destination=self.plan.destination, source=self.plan.source))
        row = ctk.CTkFrame(self.window, fg_color='transparent')
        row.pack(fill='x', padx=20, pady=(0, 10))
        button(row, tr('Оставить оригиналы'), self._close, True, width=200)
        button(row, tr('Удалить оригиналы…'), self._delete_originals, width=200)

    def _delete_originals(self):
        plan = self.plan
        if not messagebox.askyesno(APP_NAME, tr('Удалить {count} файлов ({size}) из {source}? Копии в {destination} проверены. Это действие нельзя отменить.',
                                                count=len(plan.files), size=hf_download.format_bytes(plan.total_bytes),
                                                source=plan.source, destination=plan.destination),
                                   icon='warning', default='no', parent=self.window):
            return

        def work():
            busy = model_library.running_paths_in(running_rows(), plan.source)
            if busy:
                message = tr('Оригиналы не удалены: сервер моделей использует файлы из старой папки ({models}).', models=', '.join(busy))
            else:
                removed = model_library.delete_originals(plan)
                message = tr('Удалено файлов: {count}.', count=removed)
            self.pump.put(lambda: self.progress_label.configure(text=message, text_color=MUTED))
        thread(work)

    def _close(self):
        if self.running:
            if not messagebox.askyesno(APP_NAME, tr('Отменить копирование? Уже скопированные файлы будут удалены.'), parent=self.window):
                return
            self.cancel.set()
            return
        self.window.destroy()


# ============================================================ quick chat

class ChatDialog:
    def __init__(self, app):
        self.app = app
        self.cancel = threading.Event()
        self.busy = False
        self.models = []
        self.window = window(app, tr('Спросить модель'), '900x720')
        self.window.protocol('WM_DELETE_WINDOW', self._close)
        self.pump = Pump(self.window)
        heading(self.window, tr('Спросить модель'))
        ctk.CTkLabel(self.window, text=tr('Сервер моделей общий: запрос выполняет уже загруженная модель и занимает её, пока идёт ответ. '
                                          'Другие агенты и компьютеры в сети в это время ждут. История не сохраняется.'),
                     text_color=WARNING, anchor='w', justify='left', wraplength=840).pack(fill='x', padx=20)
        row = field_row(self.window, tr('Модель'), 120)
        self.model_combo = combo(row, [tr('Поиск загруженных моделей…')], tr('Поиск загруженных моделей…'), width=420)
        button(row, tr('Обновить'), self._load_models, width=110)
        row = field_row(self.window, tr('Макс. токенов'), 120)
        self.max_tokens = combo(row, ('256', '1024', '4096', '16384'), '1024', width=120)
        self.prompt = ctk.CTkTextbox(self.window, height=110, font=('Segoe UI', 13), fg_color='#111b25', border_color=EDGE, border_width=1)
        self.prompt.pack(fill='x', padx=20, pady=(8, 4))
        self.prompt.bind('<Control-Return>', lambda event: (self._send(), 'break')[1])
        actions = ctk.CTkFrame(self.window, fg_color='transparent')
        actions.pack(fill='x', padx=20)
        self.send_button = button(actions, tr('Отправить (Ctrl+Enter)'), self._send, True, width=200)
        self.stop_button = button(actions, tr('Стоп'), self._stop, width=100)
        set_enabled(self.stop_button, False)
        self.stats_label = ctk.CTkLabel(actions, text='', text_color=MUTED, anchor='w')
        self.stats_label.pack(side='left', padx=10)
        self.output = ctk.CTkTextbox(self.window, font=('Segoe UI', 13), fg_color=PANEL, wrap='word')
        self.output.pack(fill='both', expand=True, padx=20, pady=(4, 16))
        self._load_models()

    def _load_models(self):
        profiles = {m.backend_model_id: m.name for m in profile_storage.model_profiles.values()}
        known_ready = [m.backend_model_id for m in profile_storage.model_profiles.values() if m.id in getattr(self.app, 'ready_model_ids', set())]

        def work():
            rows = running_rows()
            ready = [(r['model'], r.get('name') or profiles.get(r['model'], r['model'])) for r in rows
                     if r.get('state') == 'ready' and r.get('model')]
            if not rows:
                ready = [(model, profiles.get(model, model)) for model in known_ready]
            self.pump.put(lambda: self._models_loaded(ready))
        thread(work)

    def _models_loaded(self, ready):
        self.models = ready
        if not ready:
            label = tr('Нет загруженных моделей — загрузите модель на странице «Станция»')
            self.model_combo.configure(values=[label])
            self.model_combo.set(label)
            set_enabled(self.send_button, False, True)
            return
        labels = [name + ' (' + model + ')' if name != model else model for model, name in ready]
        self.model_combo.configure(values=labels)
        if self.model_combo.get() not in labels:
            self.model_combo.set(labels[0])
        set_enabled(self.send_button, not self.busy, True)

    def _selected_model(self):
        label = self.model_combo.get()
        for model, name in self.models:
            if label in (model, name + ' (' + model + ')'):
                return model
        return None

    def _send(self):
        model = self._selected_model()
        question = self.prompt.get('1.0', 'end').strip()
        if self.busy or not model or not question:
            return
        self.busy = True
        self.cancel = threading.Event()
        cancel = self.cancel
        set_enabled(self.send_button, False, True)
        set_enabled(self.stop_button, True)
        self.output.delete('1.0', 'end')
        self.output.insert('end', '> ' + question + '\n\n')
        self.stats_label.configure(text=tr('Ожидание первого токена…'), text_color=MUTED)
        max_tokens = int(self.max_tokens.get())
        buffer, lock = [], threading.Lock()

        def flush():
            with lock:
                text = ''.join(buffer)
                buffer.clear()
            if text and self.output.winfo_exists():
                self.output.insert('end', text)
                self.output.see('end')

        def on_token(text):
            with lock:
                buffer.append(text)
                pending = len(buffer) == 1
            if pending:
                self.pump.put(flush)

        def work():
            try:
                # Never trigger a model swap: ask only a model that is loaded right now.
                rows = running_rows()
                if not any(r.get('model') == model and r.get('state') == 'ready' for r in rows):
                    raise RuntimeError(tr('Модель «{model}» больше не загружена. Запрос не отправлен, чтобы не запускать загрузку модели.', model=model))
                stats = model_library.stream_chat(model_server.api_url(), model, [{'role': 'user', 'content': question}],
                                                  on_token, cancel, max_tokens=max_tokens)
                self.pump.put(flush)
                self.pump.put(lambda: self._finished(stats, cancel.is_set()))
            except Exception as exc:
                message = str(exc)
                self.pump.put(flush)
                self.pump.put(lambda: self._finished(None, False, message))
        thread(work)

    def _finished(self, stats, stopped, error=None):
        self.busy = False
        set_enabled(self.send_button, bool(self.models), True)
        set_enabled(self.stop_button, False)
        if error:
            self.stats_label.configure(text=tr('Ошибка: ') + error[:160], text_color=ERROR)
            return
        parts = [tr('первый токен: {seconds} с', seconds=f'{stats.first_token_s:.2f}') if stats.first_token_s is not None else tr('ответ пуст'),
                 tr('скорость: {speed} токенов/с', speed=f'{stats.tokens_per_s:.1f}') if stats.tokens_per_s else '',
                 tr('токенов: {count}', count=stats.tokens), tr('всего: {seconds} с', seconds=f'{stats.total_s:.1f}')]
        if stopped:
            parts.append(tr('остановлено'))
        self.stats_label.configure(text=' · '.join(p for p in parts if p), text_color=ACCENT)

    def _stop(self):
        self.cancel.set()

    def _close(self):
        self.cancel.set()
        self.window.destroy()


# ============================================================ Hugging Face download

class HfDialog:
    def __init__(self, app, on_add):
        self.app, self.on_add = app, on_add
        self.files = []
        self.cancel = threading.Event()
        self.running = False
        self.window = window(app, tr('Скачать модель с Hugging Face'), '900x640')
        self.window.protocol('WM_DELETE_WINDOW', self._close)
        self.pump = Pump(self.window)
        heading(self.window, tr('Скачать модель с Hugging Face'))
        ctk.CTkLabel(self.window, text=tr('Загрузка продолжается с места остановки (файл .part). Контрольная сумма SHA256 проверяется, если её публикует Hugging Face.'),
                     text_color=MUTED, anchor='w', justify='left', wraplength=840).pack(fill='x', padx=20)
        row = field_row(self.window, tr('Репозиторий'), 140)
        self.repo_entry = entry(row, width=440, placeholder_text='unsloth/Qwen3-8B-GGUF')
        self.repo_entry.bind('<Return>', lambda event: self._list())
        self.list_button = button(row, tr('Показать файлы'), self._list, width=150)
        row = field_row(self.window, tr('Файл'), 140)
        self.file_combo = combo(row, ['—'], '—', width=600, command=lambda value: self._selection_changed())
        row = field_row(self.window, tr('Папка'), 140)
        folder = model_server.models_dir()
        self.folder_entry = entry(row, str(folder) if folder else '', width=500)
        button(row, tr('Выбрать…'), self._pick_folder, width=110)
        row = field_row(self.window, tr('Токен (необязательно)'), 140)
        self.token_entry = entry(row, width=300, show='•')
        self.save_token_var = tk.BooleanVar(value=False)
        ctk.CTkCheckBox(row, text=tr('Сохранить в диспетчере учётных данных Windows'), variable=self.save_token_var).pack(side='left')
        self.stored_token = None
        self.token_hint = ctk.CTkLabel(self.window, text='', text_color=MUTED, anchor='w')
        self.token_hint.pack(fill='x', padx=20)
        thread(lambda: self._token_loaded(hf_download.load_token()))

        self.detail_label = ctk.CTkLabel(self.window, text='', text_color=MUTED, anchor='w', justify='left', wraplength=840)
        self.detail_label.pack(fill='x', padx=20, pady=(10, 4))
        self.progress = ctk.CTkProgressBar(self.window, progress_color=ACCENT)
        self.progress.set(0)
        self.progress.pack(fill='x', padx=20, pady=4)
        self.progress_label = ctk.CTkLabel(self.window, text='', anchor='w', justify='left', wraplength=840)
        self.progress_label.pack(fill='x', padx=20)
        actions = ctk.CTkFrame(self.window, fg_color='transparent')
        actions.pack(fill='x', padx=20, pady=10, side='bottom')
        self.download_button = button(actions, tr('Скачать'), self._download, True)
        set_enabled(self.download_button, False, True)
        self.cancel_button = button(actions, tr('Закрыть'), self._close)
        self.add_button = button(actions, tr('Добавить модель'), lambda: None, width=170)
        self.add_button.pack_forget()

    def _token_loaded(self, token):
        def apply():
            self.stored_token = token
            if token:
                self.token_hint.configure(text=tr('Токен Hugging Face найден в диспетчере учётных данных и будет использован.'))
        self.pump.put(apply)

    def _token(self):
        typed = self.token_entry.get().strip()
        return typed or self.stored_token

    def _pick_folder(self):
        path = filedialog.askdirectory(parent=self.window, title=tr('Папка для загрузки'), initialdir=self.folder_entry.get() or None)
        if path:
            self.folder_entry.delete(0, 'end')
            self.folder_entry.insert(0, str(Path(path)))

    def _list(self):
        try:
            repo = hf_download.normalize_repo(self.repo_entry.get())
        except ValueError as exc:
            self.detail_label.configure(text=str(exc), text_color=ERROR)
            return
        token = self._token()
        if self.token_entry.get().strip() and self.save_token_var.get():
            try:
                hf_download.save_token(self.token_entry.get().strip())
                self.token_hint.configure(text=tr('Токен сохранён в диспетчере учётных данных Windows.'))
            except Exception as exc:
                self.token_hint.configure(text=tr('Не удалось сохранить токен: {error}', error=exc), text_color=ERROR)
        self.detail_label.configure(text=tr('Получение списка файлов {repo}…', repo=repo), text_color=MUTED)
        set_enabled(self.list_button, False)

        def work():
            try:
                files = hf_download.list_gguf_files(repo, token)
                self.pump.put(lambda: self._listed(repo, files))
            except Exception as exc:
                message = str(exc)
                self.pump.put(lambda: (self.detail_label.configure(text=message, text_color=ERROR), set_enabled(self.list_button, True)))
        thread(work)

    def _label(self, item):
        return item.name + '  ·  ' + hf_download.format_bytes(item.size)

    def _listed(self, repo, files):
        set_enabled(self.list_button, True)
        self.repo, self.files = repo, files
        if not files:
            self.detail_label.configure(text=tr('В репозитории нет файлов .gguf.'), text_color=WARNING)
            self.file_combo.configure(values=['—'])
            self.file_combo.set('—')
            set_enabled(self.download_button, False, True)
            return
        # Later parts of split models are downloaded together with the first part.
        shown = [f for f in files if not model_library.is_secondary_split(f.name)]
        self.shown = shown
        labels = [self._label(f) for f in shown]
        self.file_combo.configure(values=labels)
        self.file_combo.set(labels[0])
        self._selection_changed()

    def _selected(self):
        label = self.file_combo.get()
        item = next((f for f in getattr(self, 'shown', []) if self._label(f) == label), None)
        if not item:
            return []
        match = gguf.SPLIT_PATTERN.search(item.name)
        if not match:
            return [item]
        prefix = item.name[:match.start()]
        parts = [f for f in self.files if f.name.startswith(prefix) and gguf.SPLIT_PATTERN.search(f.name)]
        return sorted(parts, key=lambda f: f.name)

    def _selection_changed(self):
        items = self._selected()
        if not items:
            set_enabled(self.download_button, False, True)
            return
        total = sum(f.size or 0 for f in items)
        text = tr('Файлов: {count} · объём: {size}', count=len(items), size=hf_download.format_bytes(total))
        text += ' · ' + (tr('SHA256 будет проверена') if all(f.sha256 for f in items) else tr('SHA256 не опубликована — проверяется только размер'))
        if any(model_library.is_mmproj_name(f.name) for f in items):
            text += '\n' + tr('Это файл mmproj (проектор изображений): скачайте его в ту же папку, что и модель.')
        self.detail_label.configure(text=text, text_color=MUTED)
        set_enabled(self.download_button, not self.running, True)

    def _download(self):
        items = self._selected()
        folder = self.folder_entry.get().strip()
        if not items or not folder or self.running:
            return
        try:
            targets = [(item, hf_download.safe_target(folder, item.name)) for item in items]
        except ValueError as exc:
            self.progress_label.configure(text=str(exc), text_color=ERROR)
            return
        needed = sum(max(0, (item.size or 0) - (t.with_name(t.name + '.part').stat().st_size if t.with_name(t.name + '.part').exists() else 0))
                     for item, t in targets if not t.exists())
        free = model_library.free_space(folder)
        if free is not None and needed > free:
            self.progress_label.configure(text=tr('Недостаточно места: нужно {need}, свободно {free}.', need=hf_download.format_bytes(needed),
                                                  free=hf_download.format_bytes(free)), text_color=ERROR)
            return
        self.running = True
        self.cancel = threading.Event()
        cancel = self.cancel
        token = self._token()
        repo = self.repo
        set_enabled(self.download_button, False, True)
        set_enabled(self.list_button, False)
        self.add_button.pack_forget()
        self.cancel_button.configure(text=tr('Остановить'))
        total_all = sum(item.size or 0 for item in items)

        def work():
            done_before = 0
            try:
                for index, (item, target) in enumerate(targets):
                    if target.exists() and item.size is not None and target.stat().st_size == item.size:
                        done_before += item.size
                        continue

                    def progress(p, index=index, item=item, base=done_before):
                        self.pump.put(lambda: self._progress(p, index, len(targets), item, base, total_all))
                    hf_download.download(hf_download.file_url(repo, item.name), target, size=item.size, sha256=item.sha256,
                                         token=token, progress=progress, cancel=cancel)
                    done_before += item.size or 0
                first = targets[0][1]
                self.pump.put(lambda: self._finished(first, None))
            except hf_download.Cancelled:
                self.pump.put(lambda: self._finished(None, tr('Загрузка остановлена. Нажмите «Скачать», чтобы продолжить с места остановки.')))
            except Exception as exc:
                log.exception('Hugging Face download failed')
                message = str(exc)
                self.pump.put(lambda: self._finished(None, tr('Ошибка загрузки: {error}', error=message)))
        thread(work)

    def _progress(self, p, index, count, item, base, total_all):
        overall = base + p.done
        self.progress.set(overall / total_all if total_all else 0)
        if p.stage == 'verify':
            text = tr('Проверка SHA256: {name} · {done} из {total}', name=item.name, done=hf_download.format_bytes(p.done), total=hf_download.format_bytes(p.total))
        else:
            text = tr('{name} ({index}/{count}) · {done} из {total} · {speed}/с · осталось {eta}', name=item.name, index=index + 1, count=count,
                      done=hf_download.format_bytes(p.done), total=hf_download.format_bytes(p.total),
                      speed=hf_download.format_bytes(int(p.speed)), eta=hf_download.format_eta(p.eta))
        self.progress_label.configure(text=text, text_color=TEXT)

    def _finished(self, path, error):
        self.running = False
        set_enabled(self.list_button, True)
        self.cancel_button.configure(text=tr('Закрыть'))
        self._selection_changed()
        if error:
            self.progress_label.configure(text=error, text_color=ERROR if not error.startswith(tr('Загрузка остановлена')) else WARNING)
            return
        self.progress.set(1)
        self.progress_label.configure(text=tr('Готово: {path}', path=path), text_color=ACCENT)
        if not model_library.is_mmproj_name(path.name):
            self.add_button.configure(command=lambda: self.on_add(path, None))
            self.add_button.pack(side='left', padx=(0, 10), pady=8)

    def _close(self):
        if self.running:
            self.cancel.set()
            return
        self.window.destroy()
