"""
Cluster Page for LAAS.
Real-time monitoring of distributed LLM nodes (GPU, VRAM, Temp, Power, and Inference).
Supports dynamic adding, testing, configuring, and removing cluster nodes.
"""
import logging
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk

from ...cluster_manager import cluster_manager
from ...config import config
from ...i18n import tr
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING, number
from .monitoring import LineChart, SERIES_COLORS

log = logging.getLogger(__name__)


class ClusterPage:
    """Mixin for ControlCenter providing the 'LLM-кластер' page."""

    def _build_cluster(self):
        page = self.page('cluster')
        self.cluster_manager = cluster_manager
        self.cluster_manager.start()
        try:
            from ...telemetry_server import telemetry_server
            telemetry_server.start()
        except Exception as e:
            log.warning("Could not start telemetry server: %s", e)

        try:
            from ...cluster_discovery import cluster_discovery
            cluster_discovery.start()
        except Exception as e:
            log.warning("Could not start cluster discovery: %s", e)

        self._node_card_widgets = {}
        self._cluster_timer_id = None
        self._cluster_polling_active = False

        # Top summary card
        self.cluster_summary_card = ctk.CTkFrame(page, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        self.cluster_summary_card.pack(fill='x', padx=1, pady=(0, 14))

        # Header row 1: Title and refresh controls
        header_row = ctk.CTkFrame(self.cluster_summary_card, fg_color='transparent')
        header_row.pack(fill='x', padx=20, pady=(15, 6))

        title_box = ctk.CTkFrame(header_row, fg_color='transparent')
        title_box.pack(side='left', fill='y')
        ctk.CTkLabel(title_box, text=tr('LLM-кластер инференса'), font=('Segoe UI', 18, 'bold'), anchor='w').pack(anchor='w')
        ctk.CTkLabel(title_box, text=tr('Распределенный мониторинг видеокарт и генерации моделей по HTTP'),
                     text_color=MUTED, font=('Segoe UI', 12), anchor='w').pack(anchor='w')

        top_btn_box = ctk.CTkFrame(header_row, fg_color='transparent')
        top_btn_box.pack(side='right')

        self.cluster_last_updated_lbl = ctk.CTkLabel(
            top_btn_box, text=tr('Не обновлялось'), font=('Segoe UI', 11), text_color=MUTED
        )
        self.cluster_last_updated_lbl.pack(side='left', padx=(0, 12))

        ctk.CTkLabel(
            top_btn_box, text=tr('Обновление:'), font=('Segoe UI', 11), text_color=MUTED
        ).pack(side='left', padx=(0, 6))

        mode_options = [tr('30 сек'), tr('60 сек'), tr('2 мин'), tr('Вручную')]
        self._mode_keys = {
            tr('30 сек'): '30s',
            tr('60 сек'): '60s',
            tr('2 мин'): '120s',
            tr('Вручную'): 'manual'
        }
        self._keys_to_mode = {v: k for k, v in self._mode_keys.items()}
        self._keys_to_mode['15s'] = tr('30 сек')

        current_mode_key = config.get('cluster_refresh_mode', '30s')
        initial_display_mode = self._keys_to_mode.get(current_mode_key, tr('30 сек'))

        self.cluster_mode_combo = ctk.CTkComboBox(
            top_btn_box, values=mode_options, width=105, height=30,
            command=self._on_cluster_mode_changed, state='readonly', font=('Segoe UI', 11)
        )
        self.cluster_mode_combo.set(initial_display_mode)
        self.cluster_mode_combo.pack(side='left', padx=(0, 10))

        self.cluster_refresh_btn = ctk.CTkButton(
            top_btn_box, text=tr('⟳ Обновить'), fg_color=EDGE, hover_color='#364a60',
            height=32, corner_radius=7, font=('Segoe UI', 12, 'bold'),
            command=self._trigger_manual_cluster_refresh
        )
        self.cluster_refresh_btn.pack(side='left')

        # Action toolbar row 2: clean dedicated row underneath title
        toolbar_row = ctk.CTkFrame(self.cluster_summary_card, fg_color='transparent')
        toolbar_row.pack(fill='x', padx=20, pady=(2, 10))

        ctk.CTkButton(
            toolbar_row, text=tr('📥 Импорт XML'), fg_color='#238636', hover_color='#2ea043',
            height=32, corner_radius=7, font=('Segoe UI', 12),
            command=self._import_cluster_xml
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            toolbar_row, text=tr('📤 Экспорт XML'), fg_color=EDGE, hover_color='#364a60',
            height=32, corner_radius=7, font=('Segoe UI', 12),
            command=self._export_cluster_xml
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            toolbar_row, text=tr('📢 Рассказать агентам'), fg_color='#8957e5', hover_color='#a371f7',
            height=32, corner_radius=7, font=('Segoe UI', 12, 'bold'),
            command=lambda: self._share_comfyui_with_agents()
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            toolbar_row, text=tr('+ Добавить узел'), fg_color='#1f6feb', hover_color='#238636',
            height=32, corner_radius=7, font=('Segoe UI', 12, 'bold'),
            command=lambda: self._open_cluster_node_dialog()
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            toolbar_row, text=tr('🔍 Автопоиск в сети'), fg_color='#0969da', hover_color='#2188ff',
            height=32, corner_radius=7, font=('Segoe UI', 12, 'bold'),
            command=lambda: self._open_discovery_dialog()
        ).pack(side='left')

        # KPI Tiles
        kpi_row = ctk.CTkFrame(self.cluster_summary_card, fg_color='transparent')
        kpi_row.pack(fill='x', padx=20, pady=(6, 16))
        kpi_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform='kpi')

        self.kpi_labels = {}
        kpi_defs = [
            ('gpus', tr('ВСЕГО GPU В ПУЛЕ'), '0× GPU'),
            ('vram', tr('СУММАРНАЯ ПАМЯТЬ VRAM'), '0.0 GB'),
            ('active', tr('АКТИВНЫЕ ГЕНЕРАЦИИ'), tr('0 задач')),
            ('nodes', tr('УЗЛЫ ОНЛАЙН'), '0 / 0'),
        ]

        for col, (key, label, default_val) in enumerate(kpi_defs):
            tile = ctk.CTkFrame(kpi_row, fg_color='#121a24', corner_radius=8, border_color=EDGE, border_width=1)
            tile.grid(row=0, column=col, sticky='nsew', padx=(0, 10 if col < 3 else 0))
            ctk.CTkLabel(tile, text=label, font=('Segoe UI', 9, 'bold'), text_color=MUTED, anchor='w').pack(fill='x', padx=12, pady=(8, 2))
            val_lbl = ctk.CTkLabel(tile, text=default_val, font=('Segoe UI', 15, 'bold'), text_color=ACCENT, anchor='w')
            val_lbl.pack(fill='x', padx=12, pady=(0, 8))
            self.kpi_labels[key] = val_lbl

        # Nodes Grid Area
        self.cluster_nodes_container = ctk.CTkFrame(page, fg_color='transparent')
        self.cluster_nodes_container.pack(fill='x', pady=(0, 14))

        # Real-time GPU Timeline Chart
        self.cluster_chart_card = ctk.CTkFrame(page, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        self.cluster_chart_card.pack(fill='x', padx=1, pady=(0, 14))

        chart_hdr = ctk.CTkFrame(self.cluster_chart_card, fg_color='transparent')
        chart_hdr.pack(fill='x', padx=20, pady=(12, 4))
        ctk.CTkLabel(chart_hdr, text=tr('Загрузка GPU в кластере (Real-time Timeline)'),
                     font=('Segoe UI', 15, 'bold'), anchor='w').pack(side='left')

        self.cluster_chart = LineChart(self.cluster_chart_card, '')
        self.cluster_chart.frame.pack(fill='x', expand=True, padx=10, pady=(0, 10))

        # Refresh only on explicit page show (completely decoupled from local telemetry ticks)
        self.page_show_hooks['cluster'] = self._on_cluster_page_shown

    def _on_cluster_page_shown(self):
        self._refresh_cluster_ui()
        if self.cluster_manager.get_nodes() and not self.cluster_manager.get_snapshot().get('snapshots'):
            self._trigger_manual_cluster_refresh()
        self._schedule_cluster_timer()

    def _on_cluster_mode_changed(self, choice):
        mode_key = self._mode_keys.get(choice, 'manual')
        config.set('cluster_refresh_mode', mode_key)
        self._schedule_cluster_timer()

    def _schedule_cluster_timer(self):
        if hasattr(self, '_cluster_timer_id') and self._cluster_timer_id is not None:
            try:
                self.after_cancel(self._cluster_timer_id)
            except Exception:
                pass
            self._cluster_timer_id = None

        if getattr(self, 'current_page_name', '') != 'cluster':
            return

        mode = config.get('cluster_refresh_mode', '30s')
        if mode == 'manual':
            return

        ms = 30000
        if mode in ('15s', '30s'):
            ms = 30000
        elif mode == '60s':
            ms = 60000
        elif mode == '120s':
            ms = 120000

        self._cluster_timer_id = self.after(ms, self._cluster_auto_tick)

    def _cluster_auto_tick(self):
        self._cluster_timer_id = None
        if getattr(self, 'current_page_name', '') != 'cluster':
            return
        mode = config.get('cluster_refresh_mode', '30s')
        if mode == 'manual':
            return

        def on_done(snap):
            def ui_update():
                if getattr(self, 'current_page_name', '') == 'cluster':
                    self._refresh_cluster_ui(snap=snap)
                    now_str = time.strftime('%H:%M:%S')
                    self.cluster_last_updated_lbl.configure(text=tr('Последнее обновление: {time}', time=now_str))
                    self._schedule_cluster_timer()
            self.call_in_ui(ui_update)

        self.cluster_manager.sample_async(callback=on_done)

    def _trigger_manual_cluster_refresh(self):
        if getattr(self, '_cluster_polling_active', False):
            return
        self._cluster_polling_active = True
        self.cluster_refresh_btn.configure(text='⏳ ' + tr('Опрос...'), state='disabled')

        def on_done(snap):
            def ui_update():
                self._cluster_polling_active = False
                self._refresh_cluster_ui(snap=snap)
                now_str = time.strftime('%H:%M:%S')
                self.cluster_last_updated_lbl.configure(text=tr('Последнее обновление: {time}', time=now_str))
                self.cluster_refresh_btn.configure(text=tr('⟳ Обновить'), state='normal')
            self.call_in_ui(ui_update)

        self.cluster_manager.sample_async(callback=on_done)

    def _refresh_cluster_ui(self, snap=None, rebuild=False):
        if snap is None:
            try:
                snap = self.cluster_manager.get_snapshot()
            except Exception:
                return

        summary = snap.get('summary', {})
        self.kpi_labels['gpus'].configure(text=f"{summary.get('total_gpus', 0)}× GPU")
        self.kpi_labels['vram'].configure(text=f"{summary.get('total_vram_gb', 0):.1f} GB")
        self.kpi_labels['active'].configure(
            text=f"{summary.get('active_inferences', 0)} " + tr('активно'),
            text_color='#f8ad88' if summary.get('active_inferences', 0) > 0 else ACCENT
        )
        self.kpi_labels['nodes'].configure(
            text=f"{summary.get('online_nodes', 0)} / {summary.get('total_nodes', 0)}"
        )

        nodes = snap.get('nodes', [])
        snapshots = snap.get('snapshots', {})

        current_ids = tuple(n.get('id') for n in nodes)
        existing_ids = tuple(self._node_card_widgets.keys())

        if rebuild or current_ids != existing_ids:
            for child in self.cluster_nodes_container.winfo_children():
                child.destroy()
            self._node_card_widgets.clear()
            for node in nodes:
                nid = node.get('id')
                self._node_card_widgets[nid] = self._build_node_card(self.cluster_nodes_container, node)

        for node in nodes:
            nid = node.get('id')
            n_snap = snapshots.get(nid, {})
            if nid in self._node_card_widgets:
                self._update_node_card_inplace(self._node_card_widgets[nid], node, n_snap)

        # Update Chart
        hist = snap.get('history', {})
        timestamps = hist.get('timestamps', [])
        node_utils = hist.get('node_gpu_utils', {})

        if len(timestamps) >= 2:
            now = time.time()
            t0 = now - len(timestamps) * 2.0
            t1 = now
            series = []
            for i, (nid, points) in enumerate(node_utils.items()):
                node_name = next((n['name'] for n in nodes if n['id'] == nid), nid)
                color = SERIES_COLORS[i % len(SERIES_COLORS)]
                pts = []
                for idx, val in enumerate(points):
                    pts.append((t0 + idx * 2.0, val))
                series.append((node_name, color, pts))

            self.cluster_chart.set(series, t0, t1, unit='%', minimum_max=100.0, fixed_max=100.0)

    def _render_node_card(self, parent, node, snap):
        """Legacy helper kept for backward compatibility."""
        widgets = self._build_node_card(parent, node)
        self._update_node_card_inplace(widgets, node, snap)
        return widgets

    def _build_node_card(self, parent, node):
        nid = node.get('id')
        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        card.pack(fill='x', padx=1, pady=(0, 10))

        # Top line: Status, Name, URL, and Controls
        top_line = ctk.CTkFrame(card, fg_color='transparent')
        top_line.pack(fill='x', padx=16, pady=(12, 6))

        left_hdr = ctk.CTkFrame(top_line, fg_color='transparent')
        left_hdr.pack(side='left')

        status_dot = ctk.CTkLabel(left_hdr, text='●', font=('Segoe UI', 14), text_color=MUTED)
        status_dot.pack(side='left', padx=(0, 6))

        name_lbl = ctk.CTkLabel(left_hdr, text=node.get('name', nid), font=('Segoe UI', 15, 'bold'), text_color=TEXT)
        name_lbl.pack(side='left', padx=(0, 8))

        type_badge = ctk.CTkFrame(left_hdr, fg_color='#111b25', corner_radius=5)
        type_badge.pack(side='left', padx=(0, 8))
        type_lbl = ctk.CTkLabel(type_badge, text=node.get('type', 'node').upper(), font=('Segoe UI', 10, 'bold'),
                                text_color=MUTED)
        type_lbl.pack(padx=6, pady=2)

        url_lbl = ctk.CTkLabel(left_hdr, text=node.get('url', ''), font=('Consolas', 11), text_color=MUTED)
        url_lbl.pack(side='left')

        # Right header controls
        right_hdr = ctk.CTkFrame(top_line, fg_color='transparent')
        right_hdr.pack(side='right')

        status_lbl = ctk.CTkLabel(right_hdr, text=tr('Ожидание...'), font=('Segoe UI', 11, 'bold'),
                                  text_color=MUTED)
        status_lbl.pack(side='left', padx=(0, 14))

        if node.get('type') == 'comfyui':
            ctk.CTkButton(
                right_hdr, text=tr('📢 Навык агентам'), width=120, height=26, fg_color='#8957e5', hover_color='#a371f7',
                font=('Segoe UI', 11, 'bold'), command=lambda n=node: self._share_comfyui_with_agents(n)
            ).pack(side='left', padx=(0, 6))

        ctk.CTkButton(
            right_hdr, text=tr('Редактировать'), width=95, height=26, fg_color=EDGE, hover_color='#364a60',
            font=('Segoe UI', 11), command=lambda n=node: self._open_cluster_node_dialog(n)
        ).pack(side='left', padx=(0, 6))

        ctk.CTkButton(
            right_hdr, text=tr('Удалить'), width=65, height=26, fg_color='#3d1f24', hover_color='#822727',
            font=('Segoe UI', 11), command=lambda i=nid: self._confirm_remove_cluster_node(i)
        ).pack(side='left')

        # Middle content container: GPUs and Inference status
        content_box = ctk.CTkFrame(card, fg_color='transparent')
        content_box.pack(fill='x', padx=16, pady=(0, 12))

        gpus_container = ctk.CTkFrame(content_box, fg_color='transparent')
        gpus_container.pack(fill='x')

        sys_frame = ctk.CTkFrame(content_box, fg_color='#10161f', corner_radius=6)
        r_sys = ctk.CTkFrame(sys_frame, fg_color='transparent')
        r_sys.pack(fill='x', padx=12, pady=4)
        cpu_lbl = ctk.CTkLabel(r_sys, text='', font=('Consolas', 10), text_color=MUTED)
        cpu_lbl.pack(side='left', padx=(0, 16))
        ram_lbl = ctk.CTkLabel(r_sys, text='', font=('Consolas', 10), text_color=MUTED)
        ram_lbl.pack(side='left')

        inf_row = ctk.CTkFrame(content_box, fg_color='#101a18', corner_radius=8, border_color='#1b4332', border_width=1)
        r_inf = ctk.CTkFrame(inf_row, fg_color='transparent')
        r_inf.pack(fill='x', padx=12, pady=6)
        inf_badge = ctk.CTkLabel(r_inf, text='', font=('Segoe UI', 11, 'bold'))
        inf_badge.pack(side='left', padx=(0, 14))
        inf_detail = ctk.CTkLabel(r_inf, text='', font=('Consolas', 10), text_color=MUTED)
        inf_detail.pack(side='right')

        notes = node.get('notes', '')
        if notes:
            ctk.CTkLabel(card, text=f"ℹ {notes}", font=('Segoe UI', 10), text_color='#5f758a').pack(anchor='w', padx=16, pady=(0, 8))

        return {
            'card': card,
            'status_dot': status_dot,
            'name_lbl': name_lbl,
            'type_lbl': type_lbl,
            'url_lbl': url_lbl,
            'status_lbl': status_lbl,
            'content_box': content_box,
            'gpus_container': gpus_container,
            'gpu_rows': [],
            'sys_frame': sys_frame,
            'cpu_lbl': cpu_lbl,
            'ram_lbl': ram_lbl,
            'inf_row': inf_row,
            'inf_badge': inf_badge,
            'inf_detail': inf_detail,
        }

    def _update_node_card_inplace(self, widgets, node, snap):
        nid = node.get('id')
        is_online = snap.get('status') == 'online'
        status_color = '#56d6b1' if is_online else '#f8ad88'
        status_text = f"ONLINE ({snap.get('latency_ms', 0)} ms)" if is_online else tr('НЕДОСТУПЕН')

        widgets['name_lbl'].configure(text=node.get('name', nid))
        widgets['url_lbl'].configure(text=node.get('url', ''))
        widgets['type_lbl'].configure(text=node.get('type', 'node').upper())
        widgets['status_dot'].configure(text_color=status_color)
        widgets['status_lbl'].configure(text=status_text, text_color=status_color)

        # GPU metrics
        gpus = snap.get('gpus', [])
        gpu_container = widgets['gpus_container']
        existing_gpu_rows = widgets['gpu_rows']

        if len(existing_gpu_rows) != len(gpus):
            for child in gpu_container.winfo_children():
                child.destroy()
            existing_gpu_rows.clear()
            for idx, g in enumerate(gpus):
                g_row = ctk.CTkFrame(gpu_container, fg_color='#121a24', corner_radius=8, border_color=EDGE, border_width=1)
                g_row.pack(fill='x', pady=3)

                r1 = ctk.CTkFrame(g_row, fg_color='transparent')
                r1.pack(fill='x', padx=12, pady=(6, 2))

                name_lbl = ctk.CTkLabel(r1, text='', font=('Segoe UI', 12, 'bold'), text_color=TEXT)
                name_lbl.pack(side='left')

                load_lbl = ctk.CTkLabel(r1, text='', font=('Consolas', 12, 'bold'), text_color=ACCENT)
                load_lbl.pack(side='right')

                p_bar = ctk.CTkProgressBar(g_row, height=6, corner_radius=3, fg_color='#223140', progress_color='#06b6d4')
                p_bar.pack(fill='x', padx=12, pady=2)

                tags_row = ctk.CTkFrame(g_row, fg_color='transparent')
                tags_row.pack(fill='x', padx=12, pady=(2, 6))

                vram_lbl = ctk.CTkLabel(tags_row, text='', font=('Consolas', 11), text_color=MUTED)
                vram_lbl.pack(side='left', padx=(0, 14))

                temp_lbl = ctk.CTkLabel(tags_row, text='', font=('Consolas', 11), text_color=ACCENT)
                temp_lbl.pack(side='left', padx=(0, 14))

                pwr_lbl = ctk.CTkLabel(tags_row, text='', font=('Consolas', 11), text_color=MUTED)
                pwr_lbl.pack(side='left')

                existing_gpu_rows.append({
                    'name_lbl': name_lbl,
                    'load_lbl': load_lbl,
                    'p_bar': p_bar,
                    'vram_lbl': vram_lbl,
                    'temp_lbl': temp_lbl,
                    'pwr_lbl': pwr_lbl
                })

        for idx, g in enumerate(gpus):
            if idx < len(existing_gpu_rows):
                row_w = existing_gpu_rows[idx]
                g_name = g.get('name', 'GPU')
                util = float(g.get('util_percent', 0.0))
                vram_u = float(g.get('vram_used_gb', 0.0))
                vram_t = float(g.get('vram_total_gb', 0.0))
                temp = float(g.get('temp_c', 0.0))
                pwr = float(g.get('power_w', 0.0))

                temp_color = '#f8ad88' if temp >= 85 else ACCENT

                row_w['name_lbl'].configure(text=f"🎮 {g_name}")
                row_w['load_lbl'].configure(text=f"{util:.1f}% Load")
                row_w['p_bar'].set(min(1.0, max(0.0, util / 100.0)))
                row_w['vram_lbl'].configure(text=f"VRAM: {vram_u:.1f} / {vram_t:.1f} GB")
                row_w['temp_lbl'].configure(text=f"Temp: {temp:.0f}°C", text_color=temp_color)
                row_w['pwr_lbl'].configure(text=f"Power: {pwr:.1f} W" if pwr > 0 else "")

        # Host system metrics (CPU/RAM)
        cpu_val = snap.get('cpu_util')
        ram_u = snap.get('ram_used_gb')
        ram_t = snap.get('ram_total_gb')
        sys_frame = widgets['sys_frame']
        if cpu_val is not None or (ram_u is not None and ram_t is not None):
            if not sys_frame.winfo_ismapped():
                sys_frame.pack(fill='x', pady=2)
            widgets['cpu_lbl'].configure(text=f"CPU: {cpu_val:.1f}%" if cpu_val is not None else "")
            widgets['ram_lbl'].configure(text=f"RAM: {ram_u:.1f} / {ram_t:.1f} GB" if (ram_u is not None and ram_t is not None) else "")
        else:
            if sys_frame.winfo_ismapped():
                sys_frame.pack_forget()

        # Inference status row
        inf = snap.get('inference', {})
        inf_row = widgets['inf_row']
        if inf.get('online', False):
            if not inf_row.winfo_ismapped():
                inf_row.pack(fill='x', pady=3)
            is_proc = inf.get('is_processing', False)
            node_type = node.get('type', '')
            inf_type = inf.get('type', '')

            if node_type == 'comfyui' or inf_type == 'comfyui':
                q_rem = inf.get('queue_remaining', 0)
                if is_proc:
                    badge_text = tr("⚡ ГЕНЕРАЦИЯ КАРТИНКИ (в очереди: {q})", q=q_rem)
                    badge_color = '#e3b341'
                else:
                    badge_text = tr("IDLE (ГОТОВ К ГЕНЕРАЦИИ)")
                    badge_color = '#56d6b1'

                widgets['inf_badge'].configure(
                    text=f"{tr('ComfyUI (Генератор изображений)')}: {badge_text}",
                    text_color=badge_color
                )
                widgets['inf_detail'].configure(
                    text=f"Queue: {q_rem} tasks • SDXL / FLUX / Z-Image"
                )
            else:
                badge_text = tr('⚡ ГЕНЕРАЦИЯ') if is_proc else tr('IDLE (ОЖИДАНИЕ)')
                badge_color = '#56d6b1' if is_proc else MUTED

                widgets['inf_badge'].configure(
                    text=f"LLM Engine: {badge_text}",
                    text_color=badge_color
                )
                p_tok = inf.get('prompt_tokens', 0)
                d_tok = inf.get('decoded_tokens', 0)
                r_tok = inf.get('remain_tokens', 0)
                n_ctx = inf.get('n_ctx', 65536)

                widgets['inf_detail'].configure(
                    text=tr(
                        'Промпт: {p_tok} токенов • Генерация: {d_tok} • Остаток: {r_tok} • Контекст: {n_ctx}K',
                        p_tok=p_tok,
                        d_tok=d_tok,
                        r_tok=r_tok,
                        n_ctx=n_ctx // 1024,
                    )
                )
        else:
            if inf_row.winfo_ismapped():
                inf_row.pack_forget()

    def _open_cluster_node_dialog(self, node=None):
        dlg = ctk.CTkToplevel(self)
        dlg.title(tr('Настройка узла кластера') if node else tr('Добавить узел кластера'))
        dlg.geometry('520x450')
        dlg.transient(self)
        dlg.grab_set()

        form = ctk.CTkFrame(dlg, fg_color='transparent')
        form.pack(fill='both', expand=True, padx=24, pady=16)

        # Name
        ctk.CTkLabel(form, text=tr('Название узла:'), font=('Segoe UI', 12), text_color=MUTED).pack(anchor='w', pady=(0, 2))
        name_entry = ctk.CTkEntry(form, fg_color='#111b25', border_color=EDGE, height=32)
        name_entry.pack(fill='x')
        if node:
            name_entry.insert(0, node.get('name', ''))

        # URL
        ctk.CTkLabel(form, text=tr('URL инференса (Llama-server / Llama-swap):'), font=('Segoe UI', 12), text_color=MUTED).pack(anchor='w', pady=(8, 2))
        url_entry = ctk.CTkEntry(form, fg_color='#111b25', border_color=EDGE, height=32)
        url_entry.pack(fill='x')
        if node:
            url_entry.insert(0, node.get('url', ''))
        else:
            url_entry.insert(0, 'http://192.168.1.xxx:8080')

        # Telemetry URL
        ctk.CTkLabel(form, text=tr('URL телеметрии (Telegraf :9273/metrics или Exporter):'), font=('Segoe UI', 12), text_color=MUTED).pack(anchor='w', pady=(8, 2))
        telemetry_entry = ctk.CTkEntry(form, fg_color='#111b25', border_color=EDGE, height=32)
        telemetry_entry.pack(fill='x')
        if node and node.get('telemetry_url'):
            telemetry_entry.insert(0, node.get('telemetry_url', ''))

        # Type dropdown
        ctk.CTkLabel(form, text=tr('Тип протокола:'), font=('Segoe UI', 12), text_color=MUTED).pack(anchor='w', pady=(8, 2))
        types = ['llama_server', 'llama_swap', 'comfyui', 'local', 'laas_exporter']
        type_combo = ctk.CTkComboBox(form, values=types, fg_color='#111b25', border_color=EDGE, height=32, state='readonly')
        type_combo.pack(fill='x')
        if node:
            type_combo.set(node.get('type', 'llama_server'))
        else:
            type_combo.set('llama_server')

        # Notes
        ctk.CTkLabel(form, text=tr('Описание / заметка:'), font=('Segoe UI', 12), text_color=MUTED).pack(anchor='w', pady=(8, 2))
        notes_entry = ctk.CTkEntry(form, fg_color='#111b25', border_color=EDGE, height=32)
        notes_entry.pack(fill='x')
        if node:
            notes_entry.insert(0, node.get('notes', ''))

        # Test result label
        test_res_lbl = ctk.CTkLabel(dlg, text='', font=('Segoe UI', 11))
        test_res_lbl.pack(padx=24, pady=4, anchor='w')

        btn_row = ctk.CTkFrame(dlg, fg_color='transparent')
        btn_row.pack(fill='x', padx=24, pady=(6, 18))

        def on_test():
            test_res_lbl.configure(text=tr('Проверка соединения…'), text_color=MUTED)
            test_data = {
                'name': name_entry.get().strip(),
                'url': url_entry.get().strip(),
                'telemetry_url': telemetry_entry.get().strip(),
                'type': type_combo.get()
            }
            threading.Thread(target=self._test_node_in_dialog, args=(test_data, test_res_lbl), daemon=True).start()

        def on_save():
            name = name_entry.get().strip()
            url = url_entry.get().strip()
            if not name or not url:
                messagebox.showwarning(tr('Ошибка'), tr('Заполните название и адрес узла.'))
                return
            ndata = {
                'id': node['id'] if node else f"node-{int(time.time())}",
                'name': name,
                'url': url,
                'telemetry_url': telemetry_entry.get().strip(),
                'type': type_combo.get(),
                'notes': notes_entry.get().strip(),
                'enabled': True
            }
            if node:
                self.cluster_manager.update_node(node['id'], ndata)
            else:
                self.cluster_manager.add_node(ndata)
            dlg.destroy()
            self._refresh_cluster_ui(rebuild=True)

        ctk.CTkButton(btn_row, text=tr('⚡ Тест соединения'), fg_color='#238636', hover_color='#2ea043', height=34,
                      command=on_test).pack(side='left')

        ctk.CTkButton(btn_row, text=tr('Сохранить'), fg_color='#1f6feb', hover_color='#238636', height=34,
                      command=on_save).pack(side='right')

        ctk.CTkButton(btn_row, text=tr('Отмена'), fg_color='transparent', hover_color=EDGE, height=34,
                      command=dlg.destroy).pack(side='right', padx=10)

    def _test_node_in_dialog(self, node_data, label_widget):
        res = self.cluster_manager.test_node(node_data)
        if res.get('status') == 'online':
            g_count = len(res.get('gpus', []))
            lat = res.get('latency_ms', 0)
            label_widget.configure(
                text=tr('✓ Успешно подключено ({lat} ms, {g_count} GPU обнаружено)', lat=lat, g_count=g_count),
                text_color='#56d6b1'
            )
        else:
            err = res.get('error', tr('Таймаут или ошибка сети'))
            label_widget.configure(
                text=f"✗ {tr('Ошибка')}: {err[:50]}",
                text_color='#f8ad88'
            )

    def _confirm_remove_cluster_node(self, node_id):
        if messagebox.askyesno(tr('Удаление узла'), tr('Удалить этот узел из мониторинга кластера?')):
            self.cluster_manager.remove_node(node_id)
            self._refresh_cluster_ui(rebuild=True)

    def _export_cluster_xml(self):
        path = filedialog.asksaveasfilename(
            title=tr('Экспорт топологии кластера в XML'),
            defaultextension='.xml',
            initialfile='cluster_topology.xml',
            filetypes=[(tr('XML файлы'), '*.xml'), (tr('Все файлы'), '*.*')]
        )
        if not path:
            return
        try:
            self.cluster_manager.export_nodes_xml(path)
            messagebox.showinfo(tr('Экспорт XML'), tr('Конфигурация кластера успешно сохранена в {path}', path=path))
        except Exception as e:
            messagebox.showerror(tr('Ошибка'), str(e))

    def _import_cluster_xml(self):
        path = filedialog.askopenfilename(
            title=tr('Импорт топологии кластера из XML'),
            filetypes=[(tr('XML файлы'), '*.xml'), (tr('Все файлы'), '*.*')]
        )
        if not path:
            return
        try:
            count = self.cluster_manager.import_nodes_xml(path, merge=True)
            self._refresh_cluster_ui(rebuild=True)
            messagebox.showinfo(tr('Импорт XML'), tr('Импортировано {count} узлов кластера', count=count))
        except Exception as e:
            messagebox.showerror(tr('Ошибка импорта XML'), str(e))

    def _share_comfyui_with_agents(self, node=None):
        """Deploy ComfyUI skill + sync all models (local + cluster) to agents."""
        # 1. ComfyUI skill deployment (existing logic)
        target_url = None
        if node and node.get('url'):
            target_url = node.get('url')
        else:
            for n in self.cluster_manager.get_nodes():
                if n.get('type') == 'comfyui' or ':8188' in n.get('url', ''):
                    target_url = n.get('url')
                    break

        from src.skill_distributor import skill_distributor
        if not target_url:
            target_url = skill_distributor.get_comfy_endpoint()

        skill_results = []
        try:
            res = skill_distributor.deploy(server_url=target_url)
            skill_results = res.get('agents_updated', [])
        except Exception as ex:
            log.warning("ComfyUI skill deploy failed: %s", ex)

        # 2. Model synchronization (new logic)
        model_summary = []
        try:
            from src.node_models import discover_cluster_models
            cluster_models = discover_cluster_models(self.cluster_manager)
            if cluster_models:
                model_summary.append(tr('{count} моделей обнаружено на кластерных нодах', count=len(cluster_models)))
            # Trigger agent model sync via controller
            if hasattr(self, 'controller') and self.controller:
                for id, adapter in self.controller.adapters.items():
                    try:
                        from src.gpu_modes import gpu_mode_manager
                        model = gpu_mode_manager.get_active_model_profile()
                        preview = self.controller.preview_sync(id, model)
                        if preview.status != 'IN SYNC':
                            from src.agent_sync import apply_preview
                            apply_preview(preview, accept_custom=True)
                            model_summary.append(tr('{agent}: модели синхронизированы',
                                agent=self.controller.adapters[id].manifest.get('name', id)))
                        else:
                            model_summary.append(tr('{agent}: уже синхронизирован',
                                agent=self.controller.adapters[id].manifest.get('name', id)))
                    except Exception as ex:
                        model_summary.append(tr('{agent}: ошибка — {error}',
                            agent=self.controller.adapters[id].manifest.get('name', id), error=str(ex)[:80]))
        except Exception as ex:
            model_summary.append(tr('Обнаружение кластерных моделей: {error}', error=str(ex)[:80]))

        # 3. Combined result message
        parts = []
        if skill_results:
            agents_str = "\n".join(f"• {a}" for a in skill_results)
            parts.append(tr('🎨 Навык ComfyUI: {agents}', agents=agents_str))
        if model_summary:
            parts.append(tr('🤖 Модели:\n{summary}', summary="\n".join(f"• {s}" for s in model_summary)))
        if not parts:
            messagebox.showwarning(
                tr('Оповестить агентов'),
                tr('Не найдено ни одного поддерживаемого агента (Qwen, Antigravity, OpenClaw, Hermes).')
            )
            return
        messagebox.showinfo(
            tr('Оповестить агентов'),
            "\n\n".join(parts)
        )

    def _open_discovery_dialog(self):
        ClusterDiscoveryDialog(self, self.cluster_manager, on_nodes_added=lambda: self._refresh_cluster_ui(rebuild=True))


class ClusterDiscoveryDialog(ctk.CTkToplevel):
    def __init__(self, parent, cluster_manager, on_nodes_added=None):
        super().__init__(parent)
        self.cluster_manager = cluster_manager
        self.on_nodes_added = on_nodes_added
        self.title(tr('Автопоиск узлов в локальной сети'))
        self.geometry('760x520')
        self.minsize(640, 420)
        self.configure(fg_color=BG)
        self.transient(parent)

        # Header
        hdr = ctk.CTkFrame(self, fg_color='transparent')
        hdr.pack(fill='x', padx=24, pady=(20, 10))
        ctk.CTkLabel(hdr, text=tr('Автопоиск узлов в локальной сети'), font=('Segoe UI', 20, 'bold')).pack(anchor='w')
        ctk.CTkLabel(hdr, text=tr('Обнаружение других экземпляров Station на компьютерах в вашей сети (UDP 47150).'),
                     text_color=MUTED, font=('Segoe UI', 12)).pack(anchor='w')

        # Actions row
        act_row = ctk.CTkFrame(self, fg_color='transparent')
        act_row.pack(fill='x', padx=24, pady=(0, 10))
        self.status_lbl = ctk.CTkLabel(act_row, text=tr('Поиск узлов…'), font=('Segoe UI', 12, 'bold'), text_color=ACCENT)
        self.status_lbl.pack(side='left')
        ctk.CTkButton(act_row, text=tr('🔍 Повторить поиск'), fg_color=EDGE, hover_color='#364a60', height=30,
                      command=self._refresh_nodes).pack(side='right')

        # List frame
        self.list_frame = ctk.CTkScrollableFrame(self, fg_color=PANEL, corner_radius=10, border_color=EDGE, border_width=1)
        self.list_frame.pack(fill='both', expand=True, padx=24, pady=(0, 14))

        # Bottom row
        btm = ctk.CTkFrame(self, fg_color='transparent')
        btm.pack(fill='x', padx=24, pady=(0, 18))
        ctk.CTkButton(btm, text=tr('Закрыть'), fg_color=EDGE, width=120, height=34, command=self.destroy).pack(side='right')

        self._probe_and_render()

    def _probe_and_render(self):
        try:
            from ...cluster_discovery import cluster_discovery
            cluster_discovery.probe()
        except Exception:
            pass
        self.after(500, self._render_nodes)

    def _refresh_nodes(self):
        self.status_lbl.configure(text=tr('Опрос сети…'), text_color=MUTED)
        try:
            from ...cluster_discovery import cluster_discovery
            cluster_discovery.probe()
        except Exception:
            pass
        self.after(600, self._render_nodes)

    def _render_nodes(self):
        try:
            from ...cluster_discovery import cluster_discovery
            nodes = cluster_discovery.get_discovered_nodes()
        except Exception:
            nodes = []

        existing_urls = {n.get('url', '').rstrip('/') for n in self.cluster_manager.get_nodes()}
        existing_ips = {n.get('url', '').split('://')[-1].split(':')[0] for n in self.cluster_manager.get_nodes()}

        for child in self.list_frame.winfo_children():
            child.destroy()

        if not nodes:
            self.status_lbl.configure(text=tr('Узлы не найдены'), text_color=MUTED)
            ctk.CTkLabel(self.list_frame, text=tr('Узлы Station в локальной сети не найдены.\nУбедитесь, что Station запущена на других ПК и брандмауэр разрешает UDP порт 47150.'),
                         text_color=MUTED, font=('Segoe UI', 13), justify='center').pack(pady=60)
            return

        self.status_lbl.configure(text=tr('Обнаружено узлов в сети: {count}', count=len(nodes)), text_color=ACCENT)
        for n in nodes:
            row = ctk.CTkFrame(self.list_frame, fg_color='#121a24', corner_radius=8, border_color=EDGE, border_width=1)
            row.pack(fill='x', padx=8, pady=4)

            info_col = ctk.CTkFrame(row, fg_color='transparent')
            info_col.pack(side='left', fill='both', expand=True, padx=14, pady=10)

            title_txt = f"● {n.get('hostname', 'Station')} ({n.get('ip')}:{n.get('port', 9292)})"
            ctk.CTkLabel(info_col, text=title_txt, font=('Segoe UI', 14, 'bold'), text_color=TEXT, anchor='w').pack(fill='x')

            gpu_list = n.get('gpus', [])
            gpu_str = ", ".join(f"{g.get('name')} ({g.get('vram_gb')} GB)" for g in gpu_list) if gpu_list else tr('GPU не обнаружены')
            sub_txt = f"{tr('GPU:')} {gpu_str}  ·  v{n.get('version', '')}"
            ctk.CTkLabel(info_col, text=sub_txt, font=('Segoe UI', 11), text_color=MUTED, anchor='w').pack(fill='x', pady=(2, 0))

            btn_col = ctk.CTkFrame(row, fg_color='transparent')
            btn_col.pack(side='right', padx=14, pady=10)

            is_already = (n.get('url', '').rstrip('/') in existing_urls) or (n.get('ip') in existing_ips)
            if is_already:
                ctk.CTkLabel(btn_col, text=tr('Уже в кластере ✓'), text_color=ACCENT, font=('Segoe UI', 12, 'bold')).pack(pady=4)
            else:
                ctk.CTkButton(
                    btn_col, text=tr('+ Добавить в кластер'), fg_color='#238636', hover_color='#2ea043',
                    height=32, font=('Segoe UI', 12, 'bold'),
                    command=lambda node=n: self._add_node(node)
                ).pack()

    def _add_node(self, node):
        nid = f"node-{node.get('hostname', 'pc').lower()}-{int(time.time())}"
        ndata = {
            'id': nid,
            'name': node.get('hostname', 'Station PC'),
            'url': node.get('url', f"http://{node.get('ip')}:{node.get('port', 9292)}"),
            'telemetry_url': node.get('telemetry_url', f"http://{node.get('ip')}:47050/metrics"),
            'type': node.get('type', 'llama_swap'),
            'notes': f"Auto-discovered LAN node (v{node.get('version', '')})",
            'enabled': True
        }
        self.cluster_manager.add_node(ndata)
        self._render_nodes()
        if self.on_nodes_added:
            self.on_nodes_added()

