from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
import subprocess
from ..hardware import hidden_options
from ..supervisor import supervisor
from ..i18n import tr

class Support(str, Enum):
    SUPPORTED = 'SUPPORTED'
    UNSUPPORTED = 'UNSUPPORTED'
    DEGRADED = 'DEGRADED'
    ERROR = 'ERROR'

@dataclass
class Result:
    status: Support
    message: str = ''
    data: object = None
    @property
    def ok(self):
        return self.status == Support.SUPPORTED
    def to_dict(self):
        return asdict(self)

def unsupported(message=None):
    return Result(Support.UNSUPPORTED, tr('Эта функция не поддерживается установленной версией агента.') if message is None else message)

def probe(argv, timeout=12, env=None):
    result = subprocess.run(argv, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=timeout, env=env, **hidden_options())
    if result.returncode:
        raise RuntimeError(result.stderr[-1500:] or tr('Программа {name} завершилась с кодом {code} '
            'без описания ошибки. Проверьте, что агент установлен полностью.', name=Path(argv[0]).name, code=result.returncode))
    return result.stdout.strip()

class AgentRuntimeAdapter:
    def __init__(self, manifest):
        self.manifest = manifest
        self.id = manifest['id']
        self.command = []
        self.version = None
        self.help_text = ''
        self.frontends = []
        self.settings = {}

    def process_environment(self):
        import os
        env = dict(os.environ)
        source = self.settings.get('source_root')
        if source:
            if not Path(source).is_dir():
                raise ValueError(tr('Папка исходников агента не найдена: {path}. Исправьте путь в настройках агента.', path=source))
            env.update(PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE='1')
        return env
    def detect(self):
        return unsupported()
    def get_version(self):
        return Result(Support.SUPPORTED, data=self.version) if self.version else unsupported(tr('Версия агента не определена. Нажмите «Найти агенты заново».'))
    def get_capabilities(self):
        return Result(Support.SUPPORTED, data={'headless': False, 'daemon': False, 'task_control': False})
    def get_status(self):
        return Result(Support.SUPPORTED, data=supervisor.status('agent:' + self.id))
    def get_installation_info(self):
        return Result(Support.SUPPORTED, data={'command': self.command, 'version': self.version, 'frontends': self.frontends})
    def get_config_locations(self, workspace=None):
        return unsupported()
    def backup_configuration(self):
        return unsupported(tr('Резервная копия настроек агента создаётся автоматически при применении изменений.'))
    def configure_model_provider(self, models, cluster_models=None):
        return unsupported()
    def configure_model_binding(self, model):
        return unsupported()
    def list_model_bindings(self):
        return unsupported()
    def binding_ready(self, model):
        """True when saved settings already point the agent to this model on the Station server.

        Unlike a full sync preview, stale entries for other models do not matter here: they must
        not block launching the agent with the selected model.
        """
        return False
    def get_active_model(self):
        return unsupported(tr('Агент не сообщает, какая модель используется в открытом окне.'))
    def start(self, workspace=None, model=None):
        if not self.command:
            return unsupported(tr('{name} не установлен. Установите его и нажмите «Найти агенты заново».', name=self.manifest.get('name', self.id)))
        try:
            return Result(Support.SUPPORTED, data=supervisor.start('agent:' + self.id, self.command,
                cwd=workspace, visible=True))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))
    def stop(self):
        value = supervisor.stop('agent:' + self.id)
        return Result(Support.SUPPORTED if value['success'] else Support.ERROR, value['message'])
    def restart(self, **kwargs):
        stopped = self.stop()
        return self.start(**kwargs) if stopped.ok else stopped
    def get_active_tasks(self):
        return unsupported(tr('Station не может узнать, выполняет ли агент задачу. Убедитесь сами, что агент ничего не делает.'))
    def request_graceful_stop(self):
        return unsupported(tr('Завершите задачу в окне агента, прежде чем менять режим видеокарт.'))
    def wait_until_idle(self, timeout):
        return unsupported(tr('Station не может определить, что агент закончил работу. Проверьте окно агента.'))
    def cancel_task(self, task_id=None):
        return unsupported(tr('Отменить задачу из Station нельзя — остановите её в окне агента.'))
    def get_health(self):
        return self.get_status()
    def tail_logs(self):
        return Result(Support.SUPPORTED, data=supervisor.tail('agent:' + self.id))
    def validate_configuration(self):
        return unsupported()
    def restore_configuration(self, backup):
        return unsupported(tr('Настройки агента восстанавливаются через просмотр изменений: нажмите «Синхронизировать с агентами».'))
