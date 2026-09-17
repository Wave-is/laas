"""Hardware page: GPU profile selection and per-device state."""
import json
import logging
import os
from pathlib import Path
from tkinter import filedialog, messagebox
import customtkinter as ctk
from ...config import config
from ...i18n import tr
from ...paths import APP_NAME, data_dir
from ...profile_storage import profile_storage
from ...gpu_modes import gpu_mode_manager
from ...storage import atomic_write
from ...agent_sync import apply_preview
from ... import model_server
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING, number


class HardwarePage:
    def _build_hardware(self):
        page = self.page('hardware')
        from ...gpu_details import gpu_details
        self.poll_hooks.append(lambda *_: gpu_details.refresh())
        self.gpu_detail_widgets = {}
        card = self.card(page, tr('Профиль оборудования'), tr('Выберите режимы GPU. Перед переключением появится список изменений, если подтверждение не отключено. При совпадении режимов переключение не выполняется.'))
        row = self.row(card)
        self.gpu_combo = self.combo(row, list(profile_storage.gpu_profiles), config.get('active_gpu_profile'))
        self.button(row, tr('Применить GPU-профиль'),self._preview_gpu, True, width=190)
        self.gpu_area = ctk.CTkFrame(page, fg_color='transparent')
        self.gpu_area.pack(fill='x')
        self.topology_label = ctk.CTkLabel(page, text='', text_color=MUTED, anchor='w', justify='left', wraplength=790)
        self.topology_label.pack(fill='x', pady=8)

    def _update_hardware(self, top):
        from ...gpu_details import gpu_details
        ids = tuple(d.uuid for d in top.devices)
        if tuple(self.gpu_widgets) != ids:
            for child in self.gpu_area.winfo_children():
                child.destroy()
            self.gpu_widgets = {}
            self.gpu_detail_widgets = {}
            for device in top.devices:
                card = self.card(self.gpu_area, device.name, device.uuid)
                label = ctk.CTkLabel(card, text='', anchor='w', font=('Segoe UI', 14))
                label.pack(fill='x', padx=20, pady=(0, 4))
                detail = ctk.CTkLabel(card, text='', anchor='w', font=('Segoe UI', 12), text_color=MUTED)
                detail.pack(fill='x', padx=20, pady=(0, 14))
                self.gpu_widgets[device.uuid] = label
                self.gpu_detail_widgets[device.uuid] = detail
        for d in top.devices:
            self.gpu_widgets[d.uuid].configure(text=f'{d.driver_mode}    {number(d.temp_c, "°C")}    '
                + tr('Нагрузка {load}', load=number(d.load_percent, '%')) + '    '
                + tr('VRAM свободно {free} / {total} MiB', free=number(d.vram_free_mib), total=number(d.vram_total_mib))
                + ('    ' + tr('Дисплей активен') if d.display_active else ''))
            extra = gpu_details.get(d.uuid)
            items = []
            if extra.get('power.draw') is not None:
                limit = f" / {number(extra.get('power.limit'), ' W')}" if extra.get('power.limit') else ''
                items.append(tr('Мощность: {draw}{limit}', draw=number(extra.get('power.draw'), ' W'), limit=limit))
            if extra.get('fan.speed') is not None:
                items.append(tr('Вентилятор: {fan}', fan=number(extra.get('fan.speed'), '%')))
            gen = extra.get('pcie.link.gen.current')
            width = extra.get('pcie.link.width.current')
            if gen and width:
                items.append(f'PCIe Gen{gen} x{width}')
            if extra.get('throttle'):
                active_throttle = [r for r in extra['throttle'] if r not in ('idle', 'display_clock')]
                if active_throttle:
                    items.append(tr('Троттлинг: {reasons}', reasons=', '.join(active_throttle)))
            if hasattr(self, 'gpu_detail_widgets') and d.uuid in self.gpu_detail_widgets:
                self.gpu_detail_widgets[d.uuid].configure(text='   ·   '.join(items))
        p2p = str(top.cuda_p2p_cliques) if top.cuda_p2p_cliques else tr('нет') if top.p2p_verified else tr('неизвестно')
        nvlink = str(top.physical_nvlink_cliques) if top.physical_nvlink_cliques else tr('нет') if top.nvlink_verified else tr('неизвестно')
        self.topology_label.configure(text=f'GPU: {top.gpu_count}  ·  CUDA P2P: {p2p}  ·  NVLink: {nvlink}\n' + tr('Трафик NVLink не измерялся.') +
            ('\n' + top.discovery_error if top.discovery_error else '') + ('\n' + tr('Видеокарты не обнаружены: доступен только CPU.') if not top.devices else ''))
