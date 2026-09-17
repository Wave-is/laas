"""Monitoring page: sampled GPU and model server history, overheating alerts."""
import logging
import threading
import time
import tkinter as tk
import customtkinter as ctk
from ...config import config
from ...i18n import tr
from ...metrics_history import MetricsSampler, MetricsStore
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING

log = logging.getLogger(__name__)

SERIES_COLORS = ['#56d6b1', '#7aa7ff', '#f8ad88', '#d58cf0', '#f2d06b', '#6fd3f2', '#ff7b8a', '#a7e07a']
GRID = '#223140'
RANGES = {'1h': (3600, 10), '24h': (24 * 3600, 120)}
REFRESH_MS = 10_000
CHART_HEIGHT = 190
DEFAULT_THRESHOLD = 85


def alert_settings():
    enabled = config.get('monitoring_alerts_enabled', True)
    try:
        threshold = float(config.get('monitoring_alert_threshold_c', DEFAULT_THRESHOLD))
    except (TypeError, ValueError):
        threshold = float(DEFAULT_THRESHOLD)
    return enabled is not False, threshold


def gpu_label(index, name):
    short = (name or '').replace('NVIDIA ', '').replace('GeForce ', '').strip()
    prefix = f'GPU {index}' if index is not None else 'GPU'
    return f'{prefix} · {short}' if short else prefix


def fmt(value, digits=0, suffix=''):
    if value is None:
        return '—'
    return f'{value:.{digits}f}{suffix}'


def nice_max(value, minimum):
    value = max(value or 0, minimum)
    magnitude = 10 ** max(0, len(str(int(value))) - 1)
    for factor in (1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if factor * magnitude >= value:
            return factor * magnitude
    return value


class LineChart:
    """A small time-series chart drawn on a plain Tk canvas; redraws itself when resized."""

    def __init__(self, parent, title):
        self.frame = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        ctk.CTkLabel(self.frame, text=title, font=('Segoe UI', 15, 'bold'), anchor='w').pack(fill='x', padx=16, pady=(10, 0))
        self.legend = ctk.CTkFrame(self.frame, fg_color='transparent')
        self.legend.pack(fill='x', padx=16)
        self.canvas = tk.Canvas(self.frame, height=CHART_HEIGHT, bg=PANEL, highlightthickness=0, bd=0)
        self.canvas.pack(fill='x', expand=True, padx=10, pady=(2, 10))
        self.canvas.bind('<Configure>', lambda event: self.draw())
        self.data = None
        self._legend_key = None

    def set(self, series, t0, t1, unit='', minimum_max=1.0, fixed_max=None, marker=None, digits=0):
        """series: [(label, color, [(ts, value or None)])]."""
        self.data = dict(series=series, t0=t0, t1=t1, unit=unit, minimum_max=minimum_max, fixed_max=fixed_max,
                         marker=marker, digits=digits)
        key = tuple((label, color) for label, color, _ in series)
        if key != self._legend_key:
            self._legend_key = key
            for child in self.legend.winfo_children():
                child.destroy()
            for label, color, _ in series:
                ctk.CTkLabel(self.legend, text='● ' + label, text_color=color, font=('Segoe UI', 12)).pack(side='left', padx=(0, 14))
        self.draw()

    def draw(self):
        canvas = self.canvas
        canvas.delete('all')
        width, height = max(canvas.winfo_width(), 200), max(canvas.winfo_height(), 120)
        if not self.data:
            return
        d = self.data
        values = [v for _, _, points in d['series'] for _, v in points if v is not None]
        top = d['fixed_max'] if d['fixed_max'] is not None else nice_max(max(values + [d['marker'] or 0]) * 1.1 if values else 0, d['minimum_max'])
        left, right, upper, lower = 48, 10, 8, 22
        plot_w, plot_h = width - left - right, height - upper - lower
        t0, t1 = d['t0'], d['t1']
        x = lambda ts: left + (ts - t0) / max(t1 - t0, 1) * plot_w
        y = lambda v: upper + plot_h - min(max(v, 0), top) / top * plot_h
        for i in range(5):
            value = top * i / 4
            yy = y(value)
            canvas.create_line(left, yy, width - right, yy, fill=GRID)
            canvas.create_text(left - 6, yy, text=fmt(value, 0 if top >= 8 else 1) + d['unit'], fill=MUTED, anchor='e', font=('Segoe UI', 8))
        span = t1 - t0
        step = 600 if span <= 3600 else 4 * 3600
        tick = (int(t0 // step) + 1) * step
        while tick < t1:
            xx = x(tick)
            canvas.create_line(xx, upper, xx, upper + plot_h, fill=GRID)
            canvas.create_text(xx, height - 10, text=time.strftime('%H:%M', time.localtime(tick)), fill=MUTED, font=('Segoe UI', 8))
            tick += step
        if d['marker'] is not None and d['marker'] <= top:
            yy = y(d['marker'])
            canvas.create_line(left, yy, width - right, yy, fill=WARNING, dash=(4, 3))
        if not values:
            canvas.create_text(left + plot_w / 2, upper + plot_h / 2, text=tr('Нет данных за выбранный период'), fill=MUTED, font=('Segoe UI', 10))
            return
        gap = (span / 360 if span <= 3600 else span / 720) * 3.5
        for label, color, points in d['series']:
            segment, last_ts = [], None
            for ts, value in points:
                if value is None or (last_ts is not None and ts - last_ts > max(gap, 35)):
                    self._line(segment, color)
                    segment = []
                if value is not None:
                    segment.extend((x(ts), y(value)))
                last_ts = ts if value is not None else last_ts
            self._line(segment, color)

    def _line(self, coords, color):
        if len(coords) >= 4:
            self.canvas.create_line(*coords, fill=color, width=2)
        elif len(coords) == 2:
            self.canvas.create_oval(coords[0] - 2, coords[1] - 2, coords[0] + 2, coords[1] + 2, fill=color, outline=color)


class MonitoringPage:
    def _build_monitoring(self):
        page = self.page('monitoring')
        self.metrics_store = MetricsStore()
        self.metrics_sampler = MetricsSampler(self.metrics_store, on_alert=self._monitoring_alert, alert_settings=alert_settings)
        self.poll_hooks.append(self.metrics_sampler.on_poll)
        self.page_show_hooks['monitoring'] = self._refresh_monitoring
        self.monitoring_range = '1h'
        self.monitoring_loading = False

        card = self.card(page, tr('Мониторинг'), tr('История температуры, загрузки, видеопамяти и мощности GPU, запросы к серверу моделей и предупреждения о перегреве.'))
        row = self.row(card)
        labels = {'1h': tr('1 час'), '24h': tr('24 часа')}
        self.monitoring_range_labels = labels
        self.monitoring_range_button = ctk.CTkSegmentedButton(row, values=list(labels.values()), command=self._set_monitoring_range,
            selected_color=EDGE, selected_hover_color=EDGE, unselected_color='#111b25', text_color=TEXT)
        self.monitoring_range_button.set(labels['1h'])
        self.monitoring_range_button.pack(side='left', pady=(4, 12))
        ctk.CTkLabel(row, text=tr('Точки записываются каждые 10 секунд и хранятся 7 дней.'), text_color=MUTED, anchor='w').pack(side='left', padx=16, pady=(4, 12))
        self.monitoring_summary = ctk.CTkLabel(card, text=tr('Сбор данных…'), anchor='w', justify='left', font=('Consolas', 13), wraplength=900)
        self.monitoring_summary.pack(fill='x', padx=20, pady=(0, 14))

        self.monitoring_grid = ctk.CTkFrame(page, fg_color='transparent')
        self.monitoring_grid.pack(fill='x')
        self.monitoring_charts = {
            'temp': LineChart(self.monitoring_grid, tr('Температура GPU, °C')),
            'load': LineChart(self.monitoring_grid, tr('Загрузка GPU, %')),
            'vram': LineChart(self.monitoring_grid, tr('Видеопамять занята, %')),
            'power': LineChart(self.monitoring_grid, tr('Мощность GPU, Вт')),
            'speed': LineChart(self.monitoring_grid, tr('Сервер моделей: генерация, токенов/с')),
            'requests': LineChart(self.monitoring_grid, tr('Сервер моделей: запросы')),
        }
        self.monitoring_columns = None
        self.monitoring_grid.bind('<Configure>', self._layout_monitoring_charts)
        self._layout_monitoring_charts()
        self.after(REFRESH_MS, self._monitoring_tick)

    def _layout_monitoring_charts(self, event=None):
        width = event.width if event else self.monitoring_grid.winfo_width()
        columns = 2 if width >= 1000 else 1
        if columns == self.monitoring_columns:
            return
        self.monitoring_columns = columns
        for column in range(2):
            self.monitoring_grid.grid_columnconfigure(column, weight=1 if column < columns else 0, uniform='charts' if column < columns else '')
        for i, chart in enumerate(self.monitoring_charts.values()):
            chart.frame.grid_forget()
            chart.frame.grid(row=i // columns, column=i % columns, sticky='ew',
                             padx=(0, 14 if columns == 2 and i % 2 == 0 else 1), pady=(0, 14))

    def _set_monitoring_range(self, label):
        self.monitoring_range = next((key for key, text in self.monitoring_range_labels.items() if text == label), '1h')
        self._refresh_monitoring()

    def _monitoring_tick(self):
        try:
            page = self.pages.get('monitoring')
            if page is not None and page.winfo_ismapped() and self.state() != 'withdrawn':
                self._refresh_monitoring()
        except Exception:
            log.exception('Monitoring refresh failed')
        finally:
            if not self.stop_event.is_set():
                self.after(REFRESH_MS, self._monitoring_tick)

    def _refresh_monitoring(self):
        if self.monitoring_loading:
            return
        self.monitoring_loading = True
        span, bucket = RANGES.get(self.monitoring_range, RANGES['1h'])

        def load():
            now = time.time()
            try:
                result = (now, span, self.metrics_store.gpu_series(now - span, bucket),
                          self.metrics_store.server_series(now - span, bucket), bucket)
            except Exception:
                log.exception('Cannot read monitoring history')
                result = (now, span, {}, [], bucket)
            self.call_in_ui(lambda: self._render_monitoring(*result))
        threading.Thread(target=load, daemon=True).start()

    def _render_monitoring(self, now, span, gpus, server, bucket):
        self.monitoring_loading = False
        t0, t1 = now - span, now
        ordered = sorted(gpus.items(), key=lambda item: (item[1]['index'] is None, item[1]['index'] or 0, item[0]))
        def series(column):
            return [(gpu_label(info['index'], info['name']), SERIES_COLORS[i % len(SERIES_COLORS)],
                     [(p[0], p[column]) for p in info['points']]) for i, (uuid, info) in enumerate(ordered)]
        enabled, threshold = alert_settings()
        charts = self.monitoring_charts
        charts['temp'].set(series(1), t0, t1, '°', minimum_max=100, marker=threshold if enabled else None)
        charts['load'].set(series(2), t0, t1, '%', fixed_max=100)
        charts['vram'].set(series(3), t0, t1, '%', fixed_max=100)
        charts['power'].set(series(4), t0, t1, tr(' Вт'), minimum_max=50)
        minutes = bucket / 60
        charts['speed'].set([(tr('токенов/с'), SERIES_COLORS[0], [(p[0], p[4]) for p in server])], t0, t1, '', minimum_max=10)
        charts['requests'].set([
            (tr('начато в минуту'), SERIES_COLORS[1], [(p[0], None if p[3] is None else p[3] / minutes) for p in server]),
            (tr('в работе (среднее)'), SERIES_COLORS[2], [(p[0], p[2]) for p in server]),
        ], t0, t1, '', minimum_max=2)
        self.monitoring_summary.configure(text=self._monitoring_summary_text(ordered, server, span))

    def _monitoring_summary_text(self, ordered, server, span):
        latest = getattr(self.metrics_sampler, 'latest', None)
        lines = []
        gpus = latest['gpus'] if latest else []
        if not gpus:
            for uuid, info in ordered:
                if info['points']:
                    p = info['points'][-1]
                    gpus.append({'uuid': uuid, 'index': info['index'], 'name': info['name'], 'temp': p[1], 'load': p[2],
                                 'power': p[4], 'fan': p[5], 'vram_used': p[6], 'vram_total': p[7]})
        for gpu in sorted(gpus, key=lambda g: (g.get('index') is None, g.get('index') or 0)):
            used, total = gpu.get('vram_used'), gpu.get('vram_total')
            vram = (f'{used / 1024:.1f}/{total / 1024:.1f} GiB' if used is not None and total else '—')
            peak = max((p[1] for uuid, info in ordered if uuid == gpu.get('uuid') for p in info['points'] if p[1] is not None), default=None)
            lines.append(tr('{gpu}: {temp} °C (макс. {peak}) · загрузка {load}% · VRAM {vram} · {power} Вт · вентилятор {fan}%',
                gpu=gpu_label(gpu.get('index'), gpu.get('name')), temp=fmt(gpu.get('temp')), peak=fmt(peak),
                load=fmt(gpu.get('load')), vram=vram, power=fmt(gpu.get('power'), 1), fan=fmt(gpu.get('fan'))))
        if not lines:
            lines.append(tr('Видеокарты пока не опрошены.'))
        state = latest['server'] if latest else None
        requests = sum(p[3] for p in server if p[3] is not None)
        speeds = [p[4] for p in server if p[4] is not None and p[4] > 0]
        period = tr('за час') if span <= 3600 else tr('за 24 часа')
        if state is None:
            lines.append(tr('Сервер моделей: данные появятся после первого замера.'))
        elif not state.get('online'):
            lines.append(tr('Сервер моделей не отвечает.'))
        else:
            lines.append(tr('Сервер моделей: в работе {active} · генерация {speed} токенов/с · запросов {period}: {requests} · средняя скорость при генерации {average} токенов/с',
                active=fmt(state.get('in_flight')), speed=fmt(state.get('tokens_per_s'), 1), period=period,
                requests=f'{requests:.0f}', average=fmt(sum(speeds) / len(speeds) if speeds else None, 1)))
        return '\n'.join(lines)

    def _monitoring_alert(self, gpu, threshold):
        """Called on the sampling thread."""
        name = gpu_label(gpu.get('index'), gpu.get('name'))
        temp = gpu.get('temp')
        log.warning('GPU overheating: %s %s C (threshold %s C)', name, temp, threshold)
        message = tr('{gpu}: температура {temp} °C (порог {threshold} °C). Проверьте охлаждение.',
                     gpu=name, temp=fmt(temp), threshold=fmt(threshold))
        self.call_in_ui(lambda: self.notify(message, tr('LAAS: перегрев GPU')))

    def _settings_section_monitoring(self, page):
        card = self.card(page, tr('Предупреждения о перегреве'), tr('Уведомление в трее, если видеокарта держит температуру не ниже порога три замера подряд (около 30 секунд). '
            'Повторное уведомление — после остывания на 5 °C и не чаще раза в 10 минут для каждой видеокарты.'))
        enabled, threshold = alert_settings()
        self.overheat_enabled_var = tk.BooleanVar(value=enabled)
        ctk.CTkSwitch(card, text=tr('Предупреждать о перегреве GPU'), variable=self.overheat_enabled_var).pack(anchor='w', padx=20, pady=(0, 4))
        row = self.row(card)
        ctk.CTkLabel(row, text=tr('Порог, °C'), anchor='w').pack(side='left', padx=(0, 10))
        self.overheat_threshold_entry = ctk.CTkEntry(row, width=80)
        self.overheat_threshold_entry.insert(0, fmt(threshold))
        self.overheat_threshold_entry.pack(side='left', padx=(0, 12), pady=14)
        self.button(row, tr('Сохранить'), self._save_overheat_settings, True)

    def _save_overheat_settings(self):
        text = self.overheat_threshold_entry.get().strip().replace(',', '.')
        try:
            threshold = float(text)
            if not 40 <= threshold <= 110:
                raise ValueError
        except ValueError:
            self.status_label.configure(text=tr('Порог температуры: нужно число от 40 до 110 °C.'), text_color=WARNING)
            return
        threshold = int(threshold) if threshold.is_integer() else threshold
        try:
            config.update({'monitoring_alerts_enabled': bool(self.overheat_enabled_var.get()), 'monitoring_alert_threshold_c': threshold})
        except Exception as exc:
            self.status_label.configure(text=tr('Ошибка: ') + str(exc)[:200], text_color=WARNING)
            return
        self.status_label.configure(text=tr('Настройки предупреждений о перегреве сохранены.'), text_color=ACCENT)
