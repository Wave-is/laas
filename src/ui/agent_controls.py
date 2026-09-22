"""Per-agent frontend controls share the dashboard/tray process ownership rules."""
import customtkinter as ctk
from ..config import config
from ..i18n import tr


def frontend_action_state(frontend, running, busy=False, owned=None):
    installed = frontend.get('status') in ('INSTALLED', 'SUPPORTED (experimental)')
    owned = running if owned is None else owned
    # Station stops only processes it started; an app opened elsewhere is closed in its own window.
    return {'start': installed and not running and not busy, 'stop': running and owned and not busy}


def frontend_state_text(frontend, state):
    """One unambiguous line: installed? running? who started it?"""
    if frontend.get('status') not in ('INSTALLED', 'SUPPORTED (experimental)'):
        return tr('Не установлен')
    if not state.get('running'):
        return tr('Не запущен · установлен')
    pid = f', PID {state["pid"]}' if state.get('pid') else ''
    return tr('Запущен из Station{pid}', pid=pid) if state.get('owned') else tr('Запущен вне Station{pid}', pid=pid)


class AgentControls:
    def _build_agent_launch(self, card, runtime):
        choices = [id for id, frontend in self.controller.frontends.items() if frontend['runtime_id'] == runtime]
        if not choices:
            return
        preferred = config.get('preferred_frontend')
        def is_installed(fid):
            return self.controller.frontends.get(fid, {}).get('status') in ('INSTALLED', 'SUPPORTED (experimental)')
        if preferred in choices and is_installed(preferred):
            selected = preferred
        else:
            selected = next((id for id in choices if is_installed(id)), preferred if preferred in choices else choices[0])
        row = self.row(card)
        combo = self.combo(row, choices, selected, width=375)
        combo.configure(command=lambda value: self._refresh_agent_launch_states())
        start = self.button(row, '▶', lambda: self._launch_frontend(combo.get()), True, width=44)
        stop = self.button(row, '⏹', lambda: self._stop_agent_frontend(combo.get()), width=44)
        config_btn = self.button(row, '🔧', lambda: self._configure_agent(combo.get()), width=44)
        label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color='#91a2b4', wraplength=770)
        label.pack(fill='x', padx=20, pady=(0, 14))
        self.agent_launch_widgets[runtime] = (combo, start, stop, config_btn, label)

    def _refresh_dashboard_buttons(self):
        """Start buttons on the overview are inactive while the thing is already running."""
        if not hasattr(self, 'dashboard_server_stop'):
            return
        info = getattr(self, 'backend_info', None) or {}
        online = bool(info.get('online'))
        loaded = self.model_combo.get() in self.ready_model_ids
        self.dashboard_model_start.configure(state='disabled' if self.busy or loaded else 'normal')
        self.dashboard_model_stop.configure(state='disabled' if self.busy or not self.ready_model_ids else 'normal')
        if hasattr(self, 'dashboard_model_config'):
            self.dashboard_model_config.configure(state='normal' if not self.busy else 'disabled')
        self.dashboard_server_start.configure(state='disabled' if self.busy or online else 'normal')
        self.dashboard_server_stop.configure(state='disabled' if self.busy or not (online and info.get('owned')) else 'normal')
        if hasattr(self, 'dashboard_server_config'):
            self.dashboard_server_config.configure(state='normal' if not self.busy else 'disabled')
        fid = self.frontend_combo.get()
        state = self.frontend_states.get(fid, {})
        frontend = self.controller.frontends.get(fid, {})
        actions = frontend_action_state(frontend, state.get('running', False), self.busy, state.get('owned', False))
        self.dashboard_agent_start.configure(state='normal' if actions['start'] else 'disabled')
        self.dashboard_agent_stop.configure(state='normal' if actions['stop'] else 'disabled')
        if hasattr(self, 'dashboard_agent_config'):
            self.dashboard_agent_config.configure(state='normal' if not self.busy else 'disabled')

    def _refresh_agent_launch_states(self):
        self._refresh_dashboard_buttons()
        for widgets in getattr(self, 'agent_launch_widgets', {}).values():
            if len(widgets) == 5:
                combo, start, stop, config_btn, label = widgets
            else:
                combo, start, stop, label = widgets
                config_btn = None
            id = combo.get()
            frontend = self.controller.frontends.get(id, {})
            state = self.frontend_states.get(id, {})
            running = state.get('running', False)
            actions = frontend_action_state(frontend, running, self.busy, state.get('owned', False))
            start.configure(state='normal' if actions['start'] else 'disabled')
            stop.configure(state='normal' if actions['stop'] else 'disabled')
            if config_btn:
                config_btn.configure(state='normal' if not self.busy else 'disabled')
            combo.configure(state='disabled' if self.busy else 'readonly')
            if running:
                hint = (tr('Перед остановкой завершите текущую задачу агента.') if state.get('owned')
                        else tr('Он открыт не из Station — закройте его в его собственном окне.'))
                text = frontend_state_text(frontend, state) + '. ' + hint
            elif actions['start'] or frontend.get('status') == 'INSTALLED':
                text = (tr('Не запущен · установлен. При запуске Station спросит папку проекта, с которой будет работать агент.')
                        if frontend.get('type') in ('terminal', 'vscode') else tr('Не запущен · установлен.'))
            else:
                text = tr('Этот вариант запуска не установлен. Установите его и нажмите «Найти агенты заново».')
            label.configure(text=text)

    def _agent_frontend_done(self, result):
        from .control_center import result_message
        processes = self.controller.running_executables()
        self.frontend_states = {id: self.controller.frontend_status(id, processes) for id in self.controller.frontends}
        self.frontend_running = {id: state['running'] for id, state in self.frontend_states.items()}
        self.runtime_combo.set(config.get('primary_agent_runtime'))
        self._refresh_frontend_choices()
        self._refresh_agent_launch_states()
        self.status_label.configure(text=result_message(result)[:400], text_color='#56d6b1' if result.get('Success') else '#f8ad88')
        self._refresh_tray(rebuild=True)

    def _stop_agent_frontend(self, id):
        self.worker(lambda: self.controller.stop_frontend(id), self._agent_frontend_done, label=tr('Остановка агента'))
