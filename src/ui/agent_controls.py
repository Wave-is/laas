"""Per-agent frontend controls share the dashboard/tray process ownership rules."""
import customtkinter as ctk
from ..config import config


def frontend_action_state(frontend, running, busy=False, owned=None):
    installed = frontend.get('status') in ('INSTALLED', 'SUPPORTED (experimental)')
    owned = running if owned is None else owned
    # Station stops only processes it started; an app opened elsewhere is closed in its own window.
    return {'start': installed and not running and not busy, 'stop': running and owned and not busy}


def frontend_state_text(frontend, state):
    """One unambiguous line: installed? running? who started it?"""
    if frontend.get('status') not in ('INSTALLED', 'SUPPORTED (experimental)'):
        return 'Не установлен'
    if not state.get('running'):
        return 'Не запущен · установлен'
    pid = f', PID {state["pid"]}' if state.get('pid') else ''
    return f'Запущен из Station{pid}' if state.get('owned') else f'Запущен вне Station{pid}' 


class AgentControls:
    def _build_agent_launch(self, card, runtime):
        choices = [id for id, frontend in self.controller.frontends.items() if frontend['runtime_id'] == runtime]
        if not choices:
            return
        preferred = config.get('preferred_frontend')
        selected = preferred if preferred in choices else next((id for id in choices if
            self.controller.frontends[id].get('status') == 'INSTALLED'), choices[0])
        row = self.row(card)
        combo = self.combo(row, choices, selected, width=375)
        combo.configure(command=lambda value: self._refresh_agent_launch_states())
        start = self.button(row, 'Запустить агента', lambda: self._launch_frontend(combo.get()), True, width=160)
        stop = self.button(row, 'Остановить агента', lambda: self._stop_agent_frontend(combo.get()), width=160)
        label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color='#91a2b4', wraplength=770)
        label.pack(fill='x', padx=20, pady=(0, 14))
        self.agent_launch_widgets[runtime] = (combo, start, stop, label)

    def _refresh_agent_launch_states(self):
        for combo, start, stop, label in getattr(self, 'agent_launch_widgets', {}).values():
            id = combo.get()
            frontend = self.controller.frontends.get(id, {})
            state = self.frontend_states.get(id, {})
            running = state.get('running', False)
            actions = frontend_action_state(frontend, running, self.busy, state.get('owned', False))
            start.configure(state='normal' if actions['start'] else 'disabled')
            stop.configure(state='normal' if actions['stop'] else 'disabled')
            combo.configure(state='disabled' if self.busy else 'readonly')
            workspace = config.get('workspace') or 'домашняя папка'
            label.configure(text=(frontend_state_text(frontend, state) + ('. Перед остановкой завершите текущую задачу агента.' if state.get('owned')
                else '. Он открыт не из Station — закройте его в его собственном окне.') if running else
                f'Не запущен · установлен. Рабочая папка: {workspace}. «Остановить агента» закрывает только процесс, запущенный из Station.'
                if actions['start'] or frontend.get('status') == 'INSTALLED' else
                'Этот вариант запуска не установлен. Установите его и нажмите «Найти агенты заново».'))

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
        self.worker(lambda: self.controller.stop_frontend(id), self._agent_frontend_done, label='Остановка агента')
