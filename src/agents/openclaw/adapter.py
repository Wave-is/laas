"""OpenClaw local TUI, optional foreground gateway, and reviewed model bindings."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from copy import deepcopy
from ..base import AgentRuntimeAdapter, Result, Support, probe, unsupported
from ..discovery import npm_installation
from ..local_provider import provider_id, provider_record, enabled_models
from ...agent_sync import preview_merge
from ...storage import read_document, atomic_write
from ...hardware import hidden_options
from ...supervisor import supervisor
from ...i18n import tr


class OpenClawAdapter(AgentRuntimeAdapter):
    def __init__(self, manifest=None):
        super().__init__(manifest or {'id': 'openclaw', 'name': 'OpenClaw'})
        self.package = None
        self.tui_help = self.exec_help = self.gateway_help = self.config_help = ''

    def detect(self):
        self.command, self.package = npm_installation('openclaw', ('openclaw',), self.settings)
        self.frontends = [
            {'id': 'openclaw-terminal', 'runtime_id': self.id, 'name': tr('OpenClaw — локальный терминал'),
             'type': 'terminal', 'optional': True, 'status': 'NOT INSTALLED'},
            {'id': 'openclaw-gateway', 'runtime_id': self.id, 'name': tr('OpenClaw — шлюз (Gateway)'),
             'type': 'gateway', 'optional': True, 'status': 'NOT INSTALLED', 'adapter_launch': True},
        ]
        if not self.command:
            return unsupported(tr('OpenClaw не установлен (необязательный агент). Чтобы использовать его, установите OpenClaw и нажмите «Найти агенты заново».'))
        try:
            env = self.process_environment()
            self.version = probe(self.command + ['--version'], timeout=30, env=env)
            self.help_text = probe(self.command + ['--help'], timeout=30, env=env)
            for attr, args in [('tui_help', ['tui']), ('exec_help', ['agent', 'exec']),
                               ('gateway_help', ['gateway', 'run']), ('config_help', ['config'])]:
                try:
                    setattr(self, attr, probe(self.command + args + ['--help'], timeout=30, env=env))
                except Exception:
                    setattr(self, attr, '')
            self.frontends[0]['status'] = 'INSTALLED' if '--local' in self.tui_help else 'UNSUPPORTED BY INSTALLED VERSION'
            self.frontends[1]['status'] = 'INSTALLED' if '--bind' in self.gateway_help else 'UNSUPPORTED BY INSTALLED VERSION'
            return Result(Support.SUPPORTED if '--local' in self.tui_help else Support.DEGRADED,
                tr('OpenClaw найден') if '--local' in self.tui_help else
                tr('OpenClaw найден, но эта версия не поддерживает локальный терминал (tui --local). Обновите OpenClaw.'), {'version': self.version})
        except Exception as exc:
            for frontend in self.frontends:
                frontend['status'] = 'UNSUPPORTED BY INSTALLED VERSION'
            return Result(Support.DEGRADED, tr('OpenClaw найден, но не отвечает на проверку версии: {error}', error=exc))

    def get_config_locations(self, workspace=None):
        home = Path(self.settings.get('home') or os.environ.get('OPENCLAW_STATE_DIR', Path.home() / '.openclaw')).expanduser()
        configured = self.settings.get('config_path')
        # A Station home override must not accidentally edit the ambient user's config.
        path = configured or (None if self.settings.get('home') else os.environ.get('OPENCLAW_CONFIG_PATH')) or home / 'openclaw.json'
        return Result(Support.SUPPORTED, data={'user': str(Path(path).expanduser()), 'state': str(home)})

    def process_environment(self):
        locations = self.get_config_locations().data
        return dict(super().process_environment(), OPENCLAW_STATE_DIR=locations['state'], OPENCLAW_CONFIG_PATH=locations['user'])

    def get_capabilities(self):
        return Result(Support.SUPPORTED, data={'headless': '--config' in self.exec_help,
            'provider_sync': bool(self.command and 'validate' in self.config_help),
            'daemon': '--bind' in self.gateway_help, 'task_control': False})

    def _configuration(self):
        doc = read_document(Path(self.get_config_locations().data['user']), {}, allow_json5=True)
        def included(value):
            return isinstance(value, dict) and ('$include' in value or any(included(v) for v in value.values())) or isinstance(value, list) and any(included(v) for v in value)
        if included(doc):
            raise ValueError(tr('Настройки OpenClaw ({path}) подключают другие файлы через $include — '
                'Station не может изменять их автоматически. Укажите для OpenClaw отдельную папку настроек Station.', path=self.get_config_locations().data['user']))
        if not isinstance(doc, dict):
            raise ValueError(tr('Файл настроек OpenClaw повреждён или имеет неожиданный формат: {path}. '
                'Исправьте его или укажите другую папку настроек.', path=self.get_config_locations().data['user']))
        return doc

    def configure_model_provider(self, models):
        if not self.get_capabilities().data['provider_sync']:
            return unsupported(tr('OpenClaw не найден или его версия не поддерживает проверку настроек (config validate). Установите или обновите OpenClaw и нажмите «Найти агенты заново».'))
        self._configuration()
        changes = [(['models', 'providers', provider_id(m)], provider_record(m)) for m in enabled_models(models)]
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], changes, allow_json5=True))

    def configure_model_binding(self, model):
        if not self.get_capabilities().data['provider_sync']:
            return unsupported(tr('Настройка модели для OpenClaw недоступна. Установите или обновите OpenClaw и нажмите «Найти агенты заново».'))
        doc = self._configuration()
        reference = provider_id(model) + '/' + model.backend_model_id
        agents = doc.get('agents', {})
        selected = agents.get('defaults', {}).get('model', {})
        selected = dict(selected) if isinstance(selected, dict) else {}
        selected.update(primary=reference, fallbacks=[])
        changes = [(['agents', 'defaults', 'model'], selected),
            (['agents', 'defaults', 'models', reference], {'alias': model.name})]
        # Explicit per-agent model pins take priority over defaults in the TUI.
        rows = deepcopy(agents.get('list', []))
        default = next((a for a in rows if a.get('default')), rows[0] if rows else None)
        if default and 'model' in default:
            default['model'] = {'primary': reference, 'fallbacks': []}
            changes.append((['agents', 'list'], rows))
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], changes, allow_json5=True))

    def list_model_bindings(self):
        providers = self._configuration().get('models', {}).get('providers', {})
        return Result(Support.SUPPORTED, data={k: v for k, v in providers.items() if k.startswith('local-agent-station-')})

    def validate_configuration(self):
        if not self.command or 'validate' not in self.config_help:
            return unsupported(tr('Проверка настроек OpenClaw недоступна: OpenClaw не установлен или его версия не поддерживает config validate. Обновите OpenClaw.'))
        try:
            probe(self.command + ['config', 'validate'], timeout=40, env=self.process_environment())
            return Result(Support.SUPPORTED, tr('Настройки OpenClaw в порядке (проверено самим OpenClaw)'))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def start(self, workspace=None, model=None):
        if not self.command or '--local' not in self.tui_help:
            return unsupported(tr('OpenClaw не установлен или эта версия не поддерживает локальный терминал (tui --local). Установите или обновите OpenClaw.'))
        if model:
            binding = self._configuration().get('agents', {}).get('defaults', {}).get('model', {})
            if not isinstance(binding, dict) or binding.get('primary') != provider_id(model) + '/' + model.backend_model_id:
                return unsupported(tr('В настройках OpenClaw выбрана другая модель. Нажмите «Синхронизировать с агентами», просмотрите изменения и запустите снова.'))
        checked = self.validate_configuration()
        if not checked.ok:
            return checked
        try:
            return Result(Support.SUPPORTED, tr('Локальный терминал OpenClaw открыт'), supervisor.start('agent:' + self.id,
                self.command + ['tui', '--local'], cwd=workspace, env=self.process_environment(), visible=True))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def launch_frontend(self, frontend, workspace=None, model=None):
        if frontend.get('type') != 'gateway' or not self.command or '--bind' not in self.gateway_help:
            return unsupported(tr('Шлюз OpenClaw недоступен: OpenClaw не установлен или эта версия не поддерживает gateway run --bind. Обновите OpenClaw.'))
        checked = self.validate_configuration()
        if not checked.ok:
            return checked
        doc = self._configuration()
        if doc.get('gateway', {}).get('mode') != 'local':
            return unsupported(tr('Шлюз OpenClaw не настроен на локальный режим. Выполните настройку OpenClaw (gateway.mode=local) и запустите шлюз снова.'))
        args = self.command + ['gateway', 'run', '--bind', 'loopback', '--tailscale', 'off']
        port = self.settings.get('gateway_port')
        if port is not None:
            if type(port) is not int or not 1024 <= port <= 65535:
                return Result(Support.ERROR, tr('Порт шлюза OpenClaw указан неверно ({port}). Укажите целое число от 1024 до 65535 в настройках агента.', port=port))
            args += ['--port', str(port)]
        try:
            return Result(Support.SUPPORTED, tr('Шлюз OpenClaw запущен в фоне'), supervisor.start('frontend:' + frontend['id'],
                args, cwd=workspace, env=self.process_environment(), visible=False))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def smoke(self, model, workspace, timeout=180, configuration_path=None):
        flags = ('--config', '--cwd', '--json', '--timeout', '--state-dir')
        if not self.command or not all(flag in self.exec_help for flag in flags):
            return unsupported(tr('Проверка OpenClaw через модель недоступна: OpenClaw не установлен или его версия не поддерживает agent exec. Обновите OpenClaw.'))
        try:
            if configuration_path:
                checked = self.validate_configuration()
                if not checked.ok:
                    return checked
            with tempfile.TemporaryDirectory(prefix='openclaw-smoke-', dir=workspace) as temporary:
                home = Path(temporary)
                selected = provider_id(model)
                reference = selected + '/' + model.backend_model_id
                if configuration_path:
                    doc = read_document(Path(configuration_path), {}, allow_json5=True)
                    binding = doc.get('agents', {}).get('defaults', {}).get('model', {})
                    provider = doc.get('models', {}).get('providers', {}).get(selected)
                    if not isinstance(binding, dict) or binding.get('primary') != reference or not provider:
                        return Result(Support.ERROR, tr('Сохранённые настройки OpenClaw не указывают на выбранную модель Station. Нажмите «Синхронизировать с агентами».'))
                else:
                    provider = provider_record(model)
                # Copy only the reviewed model binding. No channels, hooks, plugins, credentials or user sessions.
                isolated = {'models': {'providers': {selected: provider}},
                    'agents': {'defaults': {'model': {'primary': reference, 'fallbacks': []},
                        'workspace': str(home / 'workspace'), 'skipBootstrap': True}},
                    'tools': {'deny': ['*']}, 'plugins': {'enabled': False}}
                path = home / 'openclaw.json'
                atomic_write(path, isolated)
                state = home / 'state'
                state.mkdir()
                env = dict(self.process_environment(), OPENCLAW_STATE_DIR=str(state), OPENCLAW_CONFIG_PATH=str(path))
                args = self.command + ['agent', 'exec', 'Reply exactly STATION_OK. Do not use tools.',
                    '--config', str(path), '--cwd', str(home), '--state-dir', str(state),
                    '--timeout', str(max(1, timeout - 10)), '--json']
                p = subprocess.run(args, cwd=home, env=env, input='', capture_output=True,
                    text=True, encoding='utf-8', errors='replace', timeout=timeout, **hidden_options())
                result = json.loads(p.stdout) if p.returncode == 0 else {}
                passed = p.returncode == 0 and result.get('ok') is True and result.get('status') == 'ok' and \
                    result.get('model') == model.backend_model_id and result.get('provider') == selected and \
                    result.get('final', '').strip() == 'STATION_OK' and result.get('toolSummary', {}).get('calls', 0) == 0
                return Result(Support.SUPPORTED if passed else Support.ERROR,
                    tr('Проверка OpenClaw через модель пройдена') if passed else
                    tr('Проверка OpenClaw через модель не пройдена (код выхода {code}). Убедитесь, что модель запущена, и посмотрите вывод проверки.', code=p.returncode),
                    {'exit_code': p.returncode, 'output': p.stdout[-3000:], 'stderr': p.stderr[-1500:]})
        except Exception as exc:
            return Result(Support.ERROR, str(exc))
