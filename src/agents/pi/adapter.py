"""Pi terminal and local providers; compatible with both npm package names."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from ..base import AgentRuntimeAdapter, Result, Support, probe, unsupported
from ..discovery import npm_installation
from ..local_provider import provider_id, provider_record, enabled_models
from ...agent_sync import preview_merge
from ...storage import read_document, atomic_write
from ...hardware import hidden_options
from ...supervisor import supervisor
from ...i18n import tr


class PiAdapter(AgentRuntimeAdapter):
    def __init__(self, manifest=None):
        super().__init__(manifest or {'id': 'pi', 'name': 'Pi Coding Agent'})
        self.package = None

    def detect(self):
        self.command, self.package = npm_installation('pi',
            ('@earendil-works/pi-coding-agent', '@mariozechner/pi-coding-agent'), self.settings)
        self.frontends = [{'id': 'pi-terminal', 'runtime_id': self.id, 'name': tr('Pi Coding Agent — терминал'),
            'type': 'terminal', 'optional': True, 'status': 'NOT INSTALLED'}]
        if not self.command:
            return unsupported(tr('Pi Coding Agent не установлен (необязательный агент). Чтобы использовать его, установите Pi и нажмите «Найти агенты заново».'))
        try:
            self.version = probe(self.command + ['--version'], env=self.process_environment())
            self.help_text = probe(self.command + ['--help'], env=self.process_environment())
            if not all(flag in self.help_text for flag in ('--provider', '--model')):
                raise ValueError(tr('Эта версия Pi Coding Agent не поддерживает выбор модели при запуске (--provider, --model). Обновите Pi.'))
            self.frontends[0]['status'] = 'INSTALLED'
            return Result(Support.SUPPORTED, tr('Pi Coding Agent найден'), {'version': self.version})
        except Exception as exc:
            self.frontends[0]['status'] = 'UNSUPPORTED BY INSTALLED VERSION'
            return Result(Support.DEGRADED, tr('Pi Coding Agent найден, но проверка не пройдена: {error}', error=exc))

    def get_config_locations(self, workspace=None):
        home = Path(self.settings.get('home') or os.environ.get('PI_CODING_AGENT_DIR', Path.home() / '.pi/agent')).expanduser()
        return Result(Support.SUPPORTED, data={'user': str(home / 'models.json'), 'settings': str(home / 'settings.json')})

    def process_environment(self):
        return dict(super().process_environment(), PI_CODING_AGENT_DIR=str(Path(self.get_config_locations().data['user']).parent))

    def get_capabilities(self):
        return Result(Support.SUPPORTED, data={'headless': '--print' in self.help_text,
            'provider_sync': bool(self.command and '--provider' in self.help_text), 'daemon': False, 'task_control': False})

    def configure_model_provider(self, models):
        if not self.get_capabilities().data['provider_sync']:
            return unsupported(tr('Pi Coding Agent не найден или не поддерживает выбор модели. Установите или обновите Pi и нажмите «Найти агенты заново».'))
        changes = [(['providers', provider_id(m)], provider_record(m)) for m in enabled_models(models)]
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], changes))

    def configure_model_binding(self, model):
        if not self.get_capabilities().data['provider_sync']:
            return unsupported(tr('Выбор модели для Pi Coding Agent недоступен. Установите или обновите Pi и нажмите «Найти агенты заново».'))
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['settings'], [
            (['defaultProvider'], provider_id(model)), (['defaultModel'], model.backend_model_id)]))

    def list_model_bindings(self):
        doc = read_document(Path(self.get_config_locations().data['user']), {})
        return Result(Support.SUPPORTED, data={k: v for k, v in doc.get('providers', {}).items() if k.startswith('local-agent-station-')})

    def validate_configuration(self):
        try:
            locations = self.get_config_locations().data
            settings = read_document(Path(locations['settings']), {})
            providers = self.list_model_bindings().data
            provider = providers.get(settings.get('defaultProvider'), {})
            if settings.get('defaultModel') not in [m['id'] for m in provider.get('models', [])]:
                raise ValueError(tr('Модель, выбранная в Pi ({settings}), отсутствует в списке моделей Station ({models}). '
                    'Нажмите «Синхронизировать с агентами».', settings=locations['settings'], models=locations['user']))
            return Result(Support.SUPPORTED, tr('Настройки модели в Pi Coding Agent в порядке'))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def start(self, workspace=None, model=None):
        if not self.command:
            return unsupported(tr('Pi Coding Agent не установлен. Установите его и нажмите «Найти агенты заново».'))
        args = list(self.command)
        if model:
            if not all(flag in self.help_text for flag in ('--provider', '--model')):
                return unsupported(tr('Эта версия Pi Coding Agent не поддерживает выбор модели при запуске (--provider, --model). Обновите Pi.'))
            args += ['--provider', provider_id(model), '--model', model.backend_model_id]
        try:
            return Result(Support.SUPPORTED, tr('Терминал Pi Coding Agent открыт'), supervisor.start('agent:' + self.id,
                args, cwd=workspace, env=self.process_environment(), visible=True))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def smoke(self, model, workspace, timeout=180, configuration_path=None):
        flags = ('--print', '--mode', '--no-tools', '--no-extensions', '--no-skills',
                 '--no-prompt-templates', '--no-themes', '--no-session', '--system-prompt')
        if not self.command or not all(flag in self.help_text for flag in flags):
            return unsupported(tr('Проверка Pi Coding Agent через модель недоступна: Pi не установлен или его версия не поддерживает изолированный режим проверки. Обновите Pi.'))
        try:
            with tempfile.TemporaryDirectory(prefix='pi-smoke-', dir=workspace) as temporary:
                home = Path(temporary)
                if configuration_path:
                    catalog = read_document(Path(configuration_path), {})
                    binding = read_document(Path(configuration_path).parent / 'settings.json', {})
                    selected = binding.get('defaultProvider')
                    settings = {k: binding[k] for k in ('defaultProvider', 'defaultModel') if k in binding}
                    providers = {selected: catalog.get('providers', {}).get(selected)} if selected else {}
                else:
                    selected = provider_id(model)
                    settings = {'defaultProvider': selected, 'defaultModel': model.backend_model_id}
                    providers = {selected: provider_record(model)}
                if selected != provider_id(model) or settings.get('defaultModel') != model.backend_model_id or not providers.get(selected):
                    return Result(Support.ERROR, tr('Сохранённые настройки Pi не указывают на выбранную модель Station. Нажмите «Синхронизировать с агентами».'))
                atomic_write(home / 'models.json', {'providers': providers})
                atomic_write(home / 'settings.json', settings)
                args = self.command + ['--print', '--mode', 'json', '--no-tools', '--no-extensions', '--no-skills',
                    '--no-prompt-templates', '--no-themes', '--no-session', '--system-prompt',
                    'Connectivity test. Reply exactly STATION_OK.', 'Reply exactly STATION_OK. Do not use tools.']
                env = dict(self.process_environment(), PI_CODING_AGENT_DIR=str(home), PI_OFFLINE='1', PI_SKIP_VERSION_CHECK='1')
                p = subprocess.run(args, cwd=temporary, env=env, input='', capture_output=True,
                    text=True, encoding='utf-8', errors='replace', timeout=timeout, **hidden_options())
                events = [json.loads(line) for line in p.stdout.splitlines() if line.strip().startswith('{')]
                messages = [e.get('message', {}) for e in events if e.get('type') == 'message_end']
                answers = [m for m in messages if m.get('role') == 'assistant']
                passed = p.returncode == 0 and bool(answers) and all(
                    m.get('provider') == selected and m.get('model') == model.backend_model_id and
                    m.get('stopReason') not in ('error', 'aborted', 'toolUse') for m in answers) and \
                    ''.join(c.get('text', '') for c in answers[-1].get('content', []) if c.get('type') == 'text').strip() == 'STATION_OK'
                return Result(Support.SUPPORTED if passed else Support.ERROR,
                    tr('Проверка Pi Coding Agent через модель пройдена') if passed else
                    tr('Проверка Pi Coding Agent через модель не пройдена (код выхода {code}). Убедитесь, что модель запущена, и посмотрите вывод проверки.', code=p.returncode),
                    {'exit_code': p.returncode, 'output': p.stdout[-3000:], 'stderr': p.stderr[-1500:]})
        except Exception as exc:
            return Result(Support.ERROR, str(exc))
