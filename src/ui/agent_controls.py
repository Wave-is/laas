"""Per-agent frontend controls share the dashboard/tray process ownership rules."""
import customtkinter as ctk
from ..config import config


def frontend_action_state(frontend, running, busy=False):
    installed = frontend.get('status') in ('INSTALLED', 'SUPPORTED (experimental)')
    return {'start': installed and not running and not busy, 'stop': running and not busy}


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
        start = self.button(row, 'Запустить', lambda: self._launch_frontend(combo.get()), True, width=135)
        stop = self.button(row, 'Остановить', lambda: self._stop_agent_frontend(combo.get()), width=135)
        label = ctk.CTkLabel(card, text='', anchor='w', justify='left', text_color='#91a2b4', wraplength=770)
        label.pack(fill='x', padx=20, pady=(0, 14))
        self.agent_launch_widgets[runtime] = (combo, start, stop, label)

    def _refresh_agent_launch_states(self):
        for combo, start, stop, label in getattr(self, 'agent_launch_widgets', {}).values():
            id = combo.get()
            frontend = self.controller.frontends.get(id, {})
            running = self.frontend_running.get(id, False)
            actions = frontend_action_state(frontend, running, self.busy)
            start.configure(state='normal' if actions['start'] else 'disabled')
            stop.configure(state='normal' if actions['stop'] else 'disabled')
            combo.configure(state='disabled' if self.busy else 'readonly')
            label.configure(text='Запущен Station. Перед остановкой завершите текущую задачу агента.' if running else
                'Готов к запуску. Остановить здесь можно процесс, запущенный Station.' if actions['start'] or frontend.get('status') == 'INSTALLED' else
                'Этот интерфейс недоступен. Установите его и повторите обнаружение.')

    def _agent_frontend_done(self, result):
        from .control_center import result_message
        self.frontend_running = {id: self.controller.frontend_status(id)['running'] for id in self.controller.frontends}
        self.runtime_combo.set(config.get('primary_agent_runtime'))
        self._refresh_frontend_choices()
        self._refresh_agent_launch_states()
        self.status_label.configure(text=result_message(result)[:150], text_color='#56d6b1' if result.get('Success') else '#f8ad88')
        self._refresh_tray(rebuild=True)

    def _stop_agent_frontend(self, id):
        self.worker(lambda: self.controller.stop_frontend(id), self._agent_frontend_done)
