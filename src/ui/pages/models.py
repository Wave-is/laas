"""Models page: registry, sync with agents, qualification."""
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
from .model_dialogs import ModelDialog, ScanDialog, MoveDialog, ChatDialog, HfDialog
from ..common import BG, PANEL, EDGE, TEXT, MUTED, ACCENT, WARNING, number


class ModelsPage:
    def _build_models(self):
        page = self.page('models')
        card = self.card(page, tr('Управление моделями'), tr('Добавляйте модели из файлов, сканируйте папку, загружайте с Hugging Face или проверяйте работу модели быстрым чатом.'))
        row = self.row(card)
        self.button(row, tr('Добавить модель'), lambda: ModelDialog(self, on_saved=self._refresh_models_text), True, width=160)
        self.button(row, tr('Сканировать папку'), lambda: ScanDialog(self, on_add=lambda path, on_saved=None: ModelDialog(self, weights_path=path, on_saved=on_saved or self._refresh_models_text), on_saved=self._refresh_models_text), width=170)
        self.button(row, tr('Загрузить с HF ↗'), lambda: HfDialog(self, on_add=lambda path, on_saved=None: ModelDialog(self, weights_path=path, on_saved=on_saved or self._refresh_models_text), on_saved=self._refresh_models_text), width=170)
        row = self.row(card)
        self.button(row, tr('Быстрый чат'), lambda: ChatDialog(self), width=130)
        self.button(row, tr('Переместить папку моделей'), lambda: MoveDialog(self, on_done=self._refresh_models_text), width=220)
        card = self.card(page, tr('Реестр моделей'), tr('Профили хранятся отдельно от агентов. Наличие файлов и возможности модели проверяются независимо.'))
        row = self.row(card)
        self.button(row, tr('Редактировать профили (YAML)'), lambda: self.edit_document('model_profiles.yaml'), width=230)
        self.button(row, tr('Синхронизировать с агентами'), self._sync_models, width=230)
        row = self.row(card)
        self.test_model_combo = self.combo(row, list(profile_storage.model_profiles), config.get('selected_model_profile', config.get('active_model_profile')))
        self.button(row, tr('Загрузить и проверить модель'),self._qualify_model, width=230)
        self.models_text = ctk.CTkTextbox(page, height=430, fg_color=PANEL, font=('Consolas', 13))
        self.models_text.pack(fill='both', expand=True)
        self._refresh_models_text()

    def _refresh_models_text(self):
        lines = []
        for model in profile_storage.model_profiles.values():
            if model.id == 'none':
                continue
            weights = model_server.resolve_model_file(model.weights_path)
            exists = bool(weights) and Path(weights).is_file()
            status = {'production': tr('основная'), 'stable': tr('стабильная'), 'fallback': tr('запасная'), 'experimental': tr('экспериментальная'),
                      'manual': tr('ручная'), 'disabled': tr('отключена')}.get(model.status, model.status)
            lines.append(model.name + '\n'
                + '  ' + tr('id для агентов: {id} · статус: {status}', id=model.backend_model_id, status=status) + '\n'
                + '  ' + tr('Файл: {path} — {state}', path=weights or tr('не задан'), state=tr('найден') if exists else tr('НЕ НАЙДЕН')) + '\n'
                + '  ' + tr('Контекст: {tokens} токенов · изображения: {vision} · вычисления: {backend}', tokens=f'{model.context:,}',
                    vision=tr('да') if model.vision else tr('нет'), backend=model.backend.upper()) + '\n'
                + '  ' + tr('Проверка запросом: {state}', state=tr('пройдена') if model.qualified else tr('не выполнялась')) + '\n')
        self.set_text(self.models_text, '\n'.join(lines) or tr('Добавьте модель через «Редактировать профили» и укажите путь к файлу весов.'))

    def _sync_models(self):
        previews = []
        errors = []
        for id, adapter in self.controller.adapters.items():
            try:
                model = gpu_mode_manager.get_active_model_profile()
                if not model or model.id == 'none':
                    raise ValueError(tr('Сначала загрузите модель: после синхронизации агент проверяется тестовым запросом к ней.'))
                preview = self.controller.preview_sync(id, model)
                previews.append((id, preview))
            except Exception as exc:
                errors.append(id + ': ' + str(exc))
        if not previews:
            messagebox.showinfo(APP_NAME, '\n'.join(errors) or tr('Нет доступных агентов'), parent=self)
            return
        states = {'IN SYNC': tr('уже совпадает'), 'OUT OF SYNC': tr('требуется обновление'), 'CUSTOM MODIFIED': tr('файл изменён вручную — проверьте разницу')}
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
            return {'Success': True, 'Message': tr('Профили синхронизированы и проверены запросом к модели.'), 'Details': results}
        self.review(tr('Синхронизация моделей с агентами'), content, apply, label=tr('Синхронизация с агентами'))

    def _qualify_model(self):
        selected = self.test_model_combo.get()
        model = profile_storage.get_model_profile(selected)
        if not model or model.id == 'none':
            messagebox.showinfo(APP_NAME, tr('Выберите установленную модель.'), parent=self)
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
            return {'Success': report['passed'], 'Message': (tr('Проверка модели «{name}» пройдена. Отчёт: {path}', name=model.name, path=target) if report['passed'] else
                tr('Проверка модели «{name}» не пройдена. Отчёт: {path}', name=model.name, path=target)), 'Report': report}
        def done(result):
            self._refresh_models_text()
            self.status_label.configure(text=result['Message'][:400], text_color=ACCENT if result.get('Success') else '#f8ad88')
            self.review(tr('Результаты проверки модели'), json.dumps(result, ensure_ascii=False, indent=2))
        self.worker(run, done, label=tr('Проверка модели «{name}»', name=model.name))

