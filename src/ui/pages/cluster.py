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

        # Top summary card
        self.cluster_summary_card = ctk.CTkFrame(page, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        self.cluster_summary_card.pack(fill='x', padx=1, pady=(0, 14))

        # Header row with title and action buttons
        header_row = ctk.CTkFrame(self.cluster_summary_card, fg_color='transparent')
        header_row.pack(fill='x', padx=20, pady=(15, 6))

        title_box = ctk.CTkFrame(header_row, fg_color='transparent')
        title_box.pack(side='left', fill='y')
        ctk.CTkLabel(title_box, text=tr('LLM-кластер инференса'), font=('Segoe UI', 18, 'bold'), anchor='w').pack(anchor='w')
        ctk.CTkLabel(title_box, text=tr('Распределенный мониторинг видеокарт и генерации моделей по HTTP'),
                     text_color=MUTED, font=('Segoe UI', 12), anchor='w').pack(anchor='w')

        btn_box = ctk.CTkFrame(header_row, fg_color='transparent')
        btn_box.pack(side='right')

        ctk.CTkButton(
            btn_box, text=tr('📥 Импорт XML'), fg_color='#238636', hover_color='#2ea043',
            height=34, corner_radius=7, font=('Segoe UI', 12),
            command=self._import_cluster_xml
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            btn_box, text=tr('📤 Экспорт XML'), fg_color=EDGE, hover_color='#364a60',
            height=34, corner_radius=7, font=('Segoe UI', 12),
            command=self._export_cluster_xml
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            btn_box, text=tr('+ Добавить узел'), fg_color='#1f6feb', hover_color='#238636',
            height=34, corner_radius=7, font=('Segoe UI', 12, 'bold'),
            command=lambda: self._open_cluster_node_dialog()
        ).pack(side='left', padx=(0, 8))

        ctk.CTkButton(
            btn_box, text=tr('⟳ Обновить'), fg_color=EDGE, hover_color='#364a60',
            height=34, corner_radius=7, font=('Segoe UI', 12),
            command=lambda: self._refresh_cluster_ui()
        ).pack(side='left')

        # KPI Tiles
        kpi_row = ctk.CTkFrame(self.cluster_summary_card, fg_color='transparent')
        kpi_row.pack(fill='x', padx=20, pady=(6, 16))
        kpi_row.grid_columnconfigure((0, 1, 2, 3), weight=1, uniform='kpi')

        self.kpi_labels = {}
        kpi_defs = [
            ('gpus', tr('ВСЕГО GPU В ПУЛЕ'), '5× Enterprise'),
            ('vram', tr('СУММАРНАЯ ПАМЯТЬ VRAM'), '92.0 GB'),
            ('active', tr('АКТИВНЫЕ ГЕНЕРАЦИИ'), tr('0 задач')),
            ('nodes', tr('УЗЛЫ ОНЛАЙН'), '4 / 4'),
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

        # Register telemetry hooks
        self.telemetry_hooks.append(lambda *_: self._refresh_cluster_ui())
        self.page_show_hooks['cluster'] = self._refresh_cluster_ui

    def _refresh_cluster_ui(self):
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

        # Rebuild or update node cards
        for child in self.cluster_nodes_container.winfo_children():
            child.destroy()

        nodes = snap.get('nodes', [])
        snapshots = snap.get('snapshots', {})

        for node in nodes:
            nid = node.get('id')
            n_snap = snapshots.get(nid, {})
            self._render_node_card(self.cluster_nodes_container, node, n_snap)

        # Update Chart
        hist = snap.get('history', {})
        timestamps = hist.get('timestamps', [])
        node_utils = hist.get('node_gpu_utils', {})

        if len(timestamps) >= 2:
            now = time.time()
            # map timestamps to virtual seconds
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
        nid = node.get('id')
        is_online = snap.get('status') == 'online'
        status_color = '#56d6b1' if is_online else '#f8ad88'
        status_text = f"ONLINE ({snap.get('latency_ms', 0)} ms)" if is_online else tr('НЕДОСТУПЕН')

        card = ctk.CTkFrame(parent, fg_color=PANEL, corner_radius=12, border_color=EDGE, border_width=1)
        card.pack(fill='x', padx=1, pady=(0, 10))

        # Top line: Status, Name, URL, and Controls
        top_line = ctk.CTkFrame(card, fg_color='transparent')
        top_line.pack(fill='x', padx=16, pady=(12, 6))

        left_hdr = ctk.CTkFrame(top_line, fg_color='transparent')
        left_hdr.pack(side='left')

        ctk.CTkLabel(left_hdr, text='●', font=('Segoe UI', 14), text_color=status_color).pack(side='left', padx=(0, 6))
        ctk.CTkLabel(left_hdr, text=node.get('name', nid), font=('Segoe UI', 15, 'bold'), text_color=TEXT).pack(side='left', padx=(0, 8))

        type_badge = ctk.CTkFrame(left_hdr, fg_color='#111b25', corner_radius=5)
        type_badge.pack(side='left', padx=(0, 8))
        ctk.CTkLabel(type_badge, text=node.get('type', 'node').upper(), font=('Segoe UI', 10, 'bold'),
                     text_color=MUTED).pack(padx=6, pady=2)

        ctk.CTkLabel(left_hdr, text=node.get('url', ''), font=('Consolas', 11), text_color=MUTED).pack(side='left')

        # Right header controls
        right_hdr = ctk.CTkFrame(top_line, fg_color='transparent')
        right_hdr.pack(side='right')

        ctk.CTkLabel(right_hdr, text=status_text, font=('Segoe UI', 11, 'bold'),
                     text_color=status_color).pack(side='left', padx=(0, 14))

        ctk.CTkButton(
            right_hdr, text=tr('Редактировать'), width=95, height=26, fg_color=EDGE, hover_color='#364a60',
            font=('Segoe UI', 11), command=lambda n=node: self._open_cluster_node_dialog(n)
        ).pack(side='left', padx=(0, 6))

        ctk.CTkButton(
            right_hdr, text=tr('Удалить'), width=65, height=26, fg_color='#3d1f24', hover_color='#822727',
            font=('Segoe UI', 11), command=lambda i=nid: self._confirm_remove_cluster_node(i)
        ).pack(side='left')

        # Middle content: GPUs and Inference status
        content_box = ctk.CTkFrame(card, fg_color='transparent')
        content_box.pack(fill='x', padx=16, pady=(0, 12))

        gpus = snap.get('gpus', [])
        inf = snap.get('inference', {})

        # GPU metrics list
        if gpus:
            for g in gpus:
                g_row = ctk.CTkFrame(content_box, fg_color='#121a24', corner_radius=8, border_color=EDGE, border_width=1)
                g_row.pack(fill='x', pady=3)

                r1 = ctk.CTkFrame(g_row, fg_color='transparent')
                r1.pack(fill='x', padx=12, pady=(6, 2))

                g_name = g.get('name', 'GPU')
                ctk.CTkLabel(r1, text=f"🎮 {g_name}", font=('Segoe UI', 12, 'bold'), text_color=TEXT).pack(side='left')

                util = g.get('util_percent', 0.0)
                ctk.CTkLabel(r1, text=f"{util:.1f}% Load", font=('Consolas', 12, 'bold'), text_color=ACCENT).pack(side='right')

                # Progress bar
                p_bar = ctk.CTkProgressBar(g_row, height=6, corner_radius=3, fg_color='#223140', progress_color='#06b6d4')
                p_bar.pack(fill='x', padx=12, pady=2)
                p_bar.set(min(1.0, max(0.0, util / 100.0)))

                # Detail tags
                tags_row = ctk.CTkFrame(g_row, fg_color='transparent')
                tags_row.pack(fill='x', padx=12, pady=(2, 6))

                vram_u = g.get('vram_used_gb', 0.0)
                vram_t = g.get('vram_total_gb', 0.0)
                temp = g.get('temp_c', 0.0)
                pwr = g.get('power_w', 0.0)

                temp_color = '#f8ad88' if temp >= 85 else ACCENT

                ctk.CTkLabel(tags_row, text=f"VRAM: {vram_u:.1f} / {vram_t:.1f} GB", font=('Consolas', 11), text_color=MUTED).pack(side='left', padx=(0, 14))
                ctk.CTkLabel(tags_row, text=f"Temp: {temp:.0f}°C", font=('Consolas', 11), text_color=temp_color).pack(side='left', padx=(0, 14))
                if pwr > 0:
                    ctk.CTkLabel(tags_row, text=f"Power: {pwr:.1f} W", font=('Consolas', 11), text_color=MUTED).pack(side='left')

        # Host system metrics (CPU/RAM)
        cpu_val = snap.get('cpu_util')
        ram_u = snap.get('ram_used_gb')
        ram_t = snap.get('ram_total_gb')
        if cpu_val is not None or ram_u is not None:
            sys_frame = ctk.CTkFrame(content_box, fg_color='#10161f', corner_radius=6)
            sys_frame.pack(fill='x', pady=2)
            r_sys = ctk.CTkFrame(sys_frame, fg_color='transparent')
            r_sys.pack(fill='x', padx=12, pady=4)
            if cpu_val is not None:
                ctk.CTkLabel(r_sys, text=f"CPU: {cpu_val:.1f}%", font=('Consolas', 10), text_color=MUTED).pack(side='left', padx=(0, 16))
            if ram_u is not None and ram_t is not None:
                ctk.CTkLabel(r_sys, text=f"RAM: {ram_u:.1f} / {ram_t:.1f} GB", font=('Consolas', 10), text_color=MUTED).pack(side='left')

        # Inference status row
        if inf.get('online', False):
            inf_row = ctk.CTkFrame(content_box, fg_color='#101a18', corner_radius=8, border_color='#1b4332', border_width=1)
            inf_row.pack(fill='x', pady=3)

            r_inf = ctk.CTkFrame(inf_row, fg_color='transparent')
            r_inf.pack(fill='x', padx=12, pady=6)

            is_proc = inf.get('is_processing', False)
            badge_text = tr('⚡ ГЕНЕРАЦИЯ') if is_proc else tr('IDLE (ОЖИДАНИЕ)')
            badge_color = '#56d6b1' if is_proc else MUTED

            ctk.CTkLabel(r_inf, text=f"LLM Engine: {badge_text}", font=('Segoe UI', 11, 'bold'), text_color=badge_color).pack(side='left', padx=(0, 14))

            p_tok = inf.get('prompt_tokens', 0)
            d_tok = inf.get('decoded_tokens', 0)
            r_tok = inf.get('remain_tokens', 0)
            n_ctx = inf.get('n_ctx', 65536)

            ctk.CTkLabel(
                r_inf,
                text=tr(
                    'Промпт: {p_tok} токенов • Генерация: {d_tok} • Остаток: {r_tok} • Контекст: {n_ctx}K',
                    p_tok=p_tok,
                    d_tok=d_tok,
                    r_tok=r_tok,
                    n_ctx=n_ctx // 1024,
                ),
                font=('Consolas', 10),
                text_color=MUTED
            ).pack(side='right')

        # Notes
        notes = node.get('notes', '')
        if notes:
            ctk.CTkLabel(card, text=f"ℹ {notes}", font=('Segoe UI', 10), text_color='#5f758a').pack(anchor='w', padx=16, pady=(0, 8))

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
        types = ['llama_server', 'llama_swap', 'local', 'laas_exporter']
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
            self._refresh_cluster_ui()

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
            self._refresh_cluster_ui()

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
            self._refresh_cluster_ui()
            messagebox.showinfo(tr('Импорт XML'), tr('Импортировано {count} узлов кластера', count=count))
        except Exception as e:
            messagebox.showerror(tr('Ошибка импорта XML'), str(e))
