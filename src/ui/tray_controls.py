"""Tray menu and preferences; all actions return to the Tk event queue."""
import logging
import queue
import threading
import customtkinter as ctk
from ..config import config
from ..i18n import tr
from ..profile_storage import profile_storage
from ..process_manager import pm
from ..shared_services import shared_services
from ..tray_renderer import renderer
from ..tray_settings import STYLES, THEMES, METRICS, COLORS, channels_for, readouts, format_metric, describe_reading
from ..service_profiles import service_actions

# Short product name for space-constrained tray places (tooltip, notifications, menu).
TRAY_NAME = 'LAAS'


class TrayControls:
    def _show_quick_menu(self, event=None):
        """The same actions are available when Windows hides notification icons."""
        import tkinter as tk
        import pystray
        if getattr(self, 'quick_menu', None):
            self.quick_menu.destroy()
        def convert(definition, parent):
            menu = tk.Menu(parent, tearoff=False, background='#18222e', foreground='#e6eef6',
                activebackground='#28394a', activeforeground='#79edc4', font=('Segoe UI', 11))
            for entry in definition.items:
                if entry is pystray.Menu.SEPARATOR:
                    menu.add_separator()
                elif entry.visible:
                    label = ('✓ ' if entry.checked else '') + entry.text
                    state = 'normal' if entry.enabled else 'disabled'
                    if entry.submenu:
                        menu.add_cascade(label=label, menu=convert(entry.submenu, menu), state=state)
                    else:
                        menu.add_command(label=label, command=lambda entry=entry: entry(None), state=state)
            return menu
        self.quick_menu = convert(self._tray_menu(), self)
        x = event.x_root if event else self.winfo_rootx()+25
        y = event.y_root if event else self.winfo_rooty()+480
        try:
            self.quick_menu.tk_popup(x, y)
        finally:
            self.quick_menu.grab_release()

    def _tray_preferences(self):
        return {key: config.get(key) for key in ('tray_style', 'tray_theme', 'tray_channels',
            'tray_metric_gpu0', 'tray_metric_gpu1', 'tray_display_mode')}

    def _enqueue_tray(self, action, value=None):
        # Called by pystray's thread: no Tk or model/driver operations here.
        try:
            self.events.put_nowait(('tray_action', (action, value), None))
        except queue.Full:
            logging.getLogger(__name__).warning('Tray action queue is busy: %s', action)

    def _tray_menu(self):
        import pystray
        Item, Menu = pystray.MenuItem, pystray.Menu
        def action(name, value=None):
            return lambda icon=None, item=None: self._enqueue_tray(name, value)
        def command(label, name, value=None, **kwargs):
            return Item(label, action(name, value), **kwargs)
        enabled = lambda item: not self.busy
        models = [command(p.name, 'model', p.id, enabled=enabled,
            checked=lambda item, id=p.id: id in self.ready_model_ids)
            for p in profile_storage.model_profiles.values() if p.id != 'none' and p.status != 'disabled']
        agents = []
        for id, adapter in self.controller.adapters.items():
            entries = [command(tr('Сделать основным агентом'), 'runtime', id, enabled=enabled,
                               checked=lambda item, id=id: config.get('primary_agent_runtime') == id)]
            for frontend in self.controller.frontends.values():
                if frontend['runtime_id'] != id:
                    continue
                fid = frontend['id']
                entries += [command(tr('Запустить: {name}', name=frontend['name']), 'frontend', fid,
                    enabled=lambda item, available=frontend['status']=='INSTALLED': available and not self.busy),
                    command(tr('Остановить: {name}', name=frontend['name']), 'stop_frontend', fid, enabled=enabled)]
            entries.append(command(tr('Официальные релизы ↗'), 'install', id, enabled=enabled))
            agents.append(Item(adapter.manifest.get('name', id), Menu(*entries)))
        from ..gpu_modes import gpu_mode_manager
        # Checkmarks follow the real driver modes, not the last saved selection.
        # QUICK_GPU_MODES labels are already translated in gpu_modes.
        gpu_entries = [command(label, 'gpu', id, enabled=enabled,
            checked=lambda item, id=id: bool(self.topology) and gpu_mode_manager.current_quick_mode(self.topology) == id)
            for id, label, _ in gpu_mode_manager.QUICK_GPU_MODES]
        from .. import model_server
        services = [Item(tr('Сервер моделей (llama-swap)'), Menu(
            Item(lambda item: self._server_tray_text(), None, enabled=False), Menu.SEPARATOR,
            command(tr('Запустить сервер'), 'backend_start', enabled=enabled),
            command(tr('Остановить сервер'), 'backend_stop', enabled=enabled))), Menu.SEPARATOR]
        for id, profile in shared_services.profiles().items():
            from .service_controls import ACTION_LABELS
            services.append(Item(profile.get('name', id), Menu(*[
                command(ACTION_LABELS[action], 'service_' + action, id, enabled=enabled)
                for action in service_actions(profile)])))
        services.append(command(tr('Настроить службы…'), 'page', 'services'))
        return Menu(
            command(tr('Открыть LAAS'), 'page', 'station', default=True),
            Item(lambda item: self._tray_status_text(), None, enabled=False), Menu.SEPARATOR,
            Item(tr('Модель'), Menu(*(models or [Item(tr('Добавьте профиль модели'), None, enabled=False)]),
                 Menu.SEPARATOR, command(tr('Выгрузить модель'), 'model', 'none', enabled=enabled))),
            Item(tr('Агенты и интерфейсы'), Menu(*agents)),
            Menu.SEPARATOR, *gpu_entries, Menu.SEPARATOR,
            Item(tr('Службы'), Menu(*services)), Menu.SEPARATOR,
            Item(tr('Вид значка'), Menu(*[command(label, 'tray_style', key,
                checked=lambda item, key=key: config.get('tray_style') == key) for key, label in STYLES.items()])),
            command(tr('Настроить показатели трея…'), 'page', 'settings'),
            command(tr('Папка журналов'), 'logs'), Menu.SEPARATOR,
            command(tr('Выход из Station'), 'quit'))

    def _tray_status_text(self):
        if self.busy:
            return tr('Выполняется действие…')
        if not self.ready_model_names:
            return tr('Модель: не загружена')
        return tr('Модель: {names}', names=', '.join(self.ready_model_names))

    def _tray_tooltip(self, reading_lines, limit=127):
        """Windows cuts tray tooltips at 127 characters: shorten the model names, keep the readings."""
        tail = '\n'.join(reading_lines)
        budget = limit - len(TRAY_NAME) - 1 - (len(tail) + 1 if tail else 0)
        status = self._tray_status_text()
        if len(status) > budget and not self.busy and self.ready_model_names:
            empty = tr('Модель: {names}', names='')
            room = max(1, budget - len(empty))
            names = ', '.join(self.ready_model_names)
            status = tr('Модель: {names}', names=names[:room - 1] + '…' if len(names) > room else names)
        lines = [TRAY_NAME, status] + ([tail] if tail else [])
        return '\n'.join(lines)[:limit]

    def _server_tray_text(self):
        from ..process_manager import pm
        return pm.describe(self.backend_info) if getattr(self, 'backend_info', None) else tr('Сервер моделей: проверка…')

    def _gpu_tray_text(self, uuid):
        device = self.topology.get_device_by_uuid(uuid) if self.topology else None
        if not device:
            return tr('GPU недоступен')
        return f'{device.index}: {device.name} · {device.driver_mode} · ' + format_metric(device.temp_c, 'temp')

    def _create_tray(self):
        if self.no_tray:
            return
        try:
            import pystray
            class StationTrayIcon(pystray.Icon):
                def _on_notify(icon, wparam, lparam):
                    # Refresh native descriptors just before opening, never destroy
                    # an HMENU from the telemetry thread while a popup is in use.
                    if lparam == 0x0205:  # WM_RBUTTONUP
                        icon.menu = icon.pending_menu
                    return super()._on_notify(wparam, lparam)
            count = 2 if config.get('tray_style') == 'two_icons' else 1
            self.tray_icons = []
            menu = self._tray_menu()
            for index in range(count):
                icon = StationTrayIcon(f'LocalAgentAIStation{index}', renderer.render_logo(False), TRAY_NAME, menu)
                icon.pending_menu = menu
                def run(icon=icon):
                    try:
                        icon.run()
                    except Exception as exc:
                        self.events.put(('tray_error', str(exc), None))
                self.tray_icons.append(icon)
                threading.Thread(target=run, daemon=True).start()
            self.tray = self.tray_icons[0]
            self._refresh_tray()
        except Exception as exc:
            self.tray = None
            self.status_label.configure(text=tr('Трей недоступен: {error}', error=str(exc)[:100]))

    def _refresh_tray(self, rebuild=False):
        if not self.tray:
            return
        prefs = self._tray_preferences()
        desired = 2 if prefs['tray_style'] == 'two_icons' else 1
        if desired != len(self.tray_icons):
            for icon in self.tray_icons:
                icon.stop()
            self._create_tray()
            return
        rows = [d.to_dict() for d in self.topology.devices] if self.topology else []
        readings = readouts(rows, prefs)
        for index, icon in enumerate(self.tray_icons):
            icon.icon = renderer.render(rows, is_running=self.backend_online, settings=prefs,
                                        channel_index=index if desired == 2 else None)
            lines = [describe_reading(r, rows) for r in (readings[index:index+1] if desired == 2 else readings)]
            icon.title = self._tray_tooltip(lines)
            if rebuild:
                icon.pending_menu = self._tray_menu()

    def _choice(self, parent, choices, selected, width=180):
        # ProfileCombo can also map non-profile values to translated labels.
        from .control_center import ProfileCombo
        widget = ProfileCombo(parent, resolver=lambda key: choices.get(key, key), values=list(choices),
            width=width, height=34, state='readonly', command=lambda value: self._preview_tray_preferences(),
            fg_color='#111b25', border_color='#28394a', button_color='#28394a')
        widget.pack(side='left', padx=(0, 10), pady=8)
        widget.set(selected if selected in choices else next(iter(choices)))
        return widget

    def _build_tray_settings(self, page):
        card = self.card(page, tr('Трей: показатели и управление'),
            tr('Правой кнопкой — модели, агенты, GPU и службы. Для каждой иконки можно выбрать GPU, цифры и заполнение фона независимо.'))
        prefs = self._tray_preferences()
        row = self.row(card)
        self.tray_style_choice = self._choice(row, STYLES, prefs['tray_style'], 265)
        self.tray_theme_choice = self._choice(row, THEMES, prefs['tray_theme'], 200)
        self.tray_channel_controls = []
        self.tray_sources = {'auto:0': tr('Первый обнаруженный GPU'), 'auto:1': tr('Второй обнаруженный GPU'),
                             'peak': tr('Максимум по всем GPU'), 'average': tr('Среднее по всем GPU')}
        for index, channel in enumerate(channels_for(prefs)):
            ctk.CTkLabel(card, text=tr('Показатель {number}     Устройство / цифры / фон / цвет', number=index+1),
                         anchor='w', text_color='#91a2b4').pack(fill='x', padx=20, pady=(8, 0))
            row = self.row(card)
            sources = dict(self.tray_sources)
            if channel['source'] not in sources:
                sources[channel['source']] = tr('Сохранённый GPU ({id})', id=channel['source'][-8:])
            source = self._choice(row, sources, channel['source'], 230)
            source.resolver = lambda key: self.tray_sources.get(key, 'GPU …' + key[-8:])
            self.tray_channel_controls.append({
                'source': source,
                'text_metric': self._choice(row, METRICS, channel['text_metric'], 145),
                'fill_metric': self._choice(row, METRICS, channel['fill_metric'], 145),
                'color': self._choice(row, {**COLORS, channel['color']: COLORS.get(channel['color'], channel['color'])}, channel['color'], 112)})
        row = self.row(card)
        self.tray_preview_label = ctk.CTkLabel(row, text='', width=132)
        self.tray_preview_label.pack(side='left', padx=(0, 16), pady=12)
        self.tray_preview_caption = ctk.CTkLabel(row, text='', anchor='w', justify='left', text_color='#91a2b4')
        self.tray_preview_caption.pack(side='left')
        row = self.row(card)
        self.button(row, tr('Применить к трею'), self._save_tray_preferences, True)
        self.button(row, tr('Вернуть рекомендуемые'), self._reset_tray_preferences)
        ctk.CTkLabel(card, text=tr('Заполнение: 0–100%; для температуры — 0–100 °C. Нет измерения — прочерк.\n'
            'В режимах максимума/среднего используются метрики первого показателя.'), justify='left',
            anchor='w', text_color='#91a2b4').pack(fill='x', padx=20, pady=(0, 16))
        self._preview_tray_preferences()

    def _pending_tray_preferences(self):
        channels = [{key: widget.get() for key, widget in row.items()} for row in self.tray_channel_controls]
        return {'tray_style': self.tray_style_choice.get(), 'tray_theme': self.tray_theme_choice.get(),
                'tray_display_mode': channels[0]['text_metric'], 'tray_channels': channels}

    def _preview_tray_preferences(self):
        if not hasattr(self, 'tray_preview_label'):
            return
        from PIL import Image
        prefs = self._pending_tray_preferences()
        rows = [d.to_dict() for d in self.topology.devices] if self.topology else []
        count = 2 if prefs['tray_style'] == 'two_icons' else 1
        preview = Image.new('RGBA', (64*count, 64))
        for i in range(count):
            preview.alpha_composite(renderer.render(rows, settings=prefs, is_running=self.backend_online,
                channel_index=i if count == 2 else None), (i*64, 0))
        self.tray_preview_image = ctk.CTkImage(preview, size=(48*count, 48))
        self.tray_preview_label.configure(image=self.tray_preview_image)
        readings = readouts(rows, prefs)
        self.tray_preview_caption.configure(text='\n'.join(f'{i+1}. ' + describe_reading(r, rows)
            for i, r in enumerate(readings)))

    def _update_tray_sources(self):
        for d in self.topology.devices:
            self.tray_sources[d.uuid] = f'{d.index}: {d.name}'
        for row in self.tray_channel_controls:
            source = row['source']
            selected = source.get()
            self.tray_sources.setdefault(selected, tr('Недоступный GPU …{id}', id=selected[-8:]))
            source.configure(values=list(self.tray_sources))
            source.set(selected)

    def _save_tray_preferences(self):
        try:
            prefs = self._pending_tray_preferences()
            # Persist UUID after an explicit save, so driver reordering cannot swap cards.
            for channel in prefs['tray_channels']:
                source = channel['source']
                if source.startswith('auto:') and self.topology:
                    index = int(source[5:])
                    if index < len(self.topology.devices):
                        channel['source'] = self.topology.devices[index].uuid
            config.update(prefs)
            for row, channel in zip(self.tray_channel_controls, prefs['tray_channels']):
                row['source'].set(channel['source'])
            self._refresh_tray(rebuild=True)
            self.status_label.configure(text=tr('Настройки трея сохранены'), text_color='#79edc4')
        except Exception as exc:
            self.status_label.configure(text=tr('Не удалось сохранить: {error}', error=str(exc)), text_color='#f8ad88')

    def _reset_tray_preferences(self):
        self.tray_style_choice.set('two_icons')
        self.tray_theme_choice.set('dark_tile')
        for i, row in enumerate(self.tray_channel_controls):
            row['source'].set(f'auto:{i}')
            row['text_metric'].set('temp')
            row['fill_metric'].set('load')
            row['color'].set(('#79edc4', '#48c6e5')[i])
        self._preview_tray_preferences()
