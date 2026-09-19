import json
import os
from pathlib import Path
import re
import subprocess
from ..base import AgentRuntimeAdapter, Result, Support, unsupported, probe
from ..discovery import qwen_installations
from ...agent_sync import preview_merge
from ...storage import read_document
from ...supervisor import supervisor
from ...hardware import hidden_options
from ...i18n import tr

MANAGED_ID = 'local-agent-station'

class QwenCodeAdapter(AgentRuntimeAdapter):
    def __init__(self, manifest=None):
        super().__init__(manifest or {'id': 'qwen-code'})
        self.package = None
        self.schema_confirmed = False

    def detect(self):
        self.command, self.package, desktops = qwen_installations()
        self.frontends = [{'id': 'qwen-desktop', 'runtime_id': self.id, 'name': 'Qwen Code Desktop',
            'type': 'desktop', 'executable': str(desktops[0]) if desktops else '',
            'status': 'INSTALLED' if desktops else 'NOT INSTALLED', 'optional': True}]
        if self.command:
            try:
                self.version = probe(self.command + ['--version'])
                self.help_text = probe(self.command + ['--help'])
                if self.package:
                    for source in (self.package / 'chunks').glob('*.js'):
                        if source.stat().st_size > 700000:
                            continue
                        text = source.read_text(encoding='utf-8')
                        if 'modelProviders:' in text and 'providerProtocol:' in text:
                            self.schema_confirmed = True
                            break
            except Exception as exc:
                return Result(Support.DEGRADED, tr('Qwen Code найден, но не отвечает на проверку версии: {error}', error=exc), {'installed': True})
        serve = bool(re.search(r'^\s*qwen serve\s', self.help_text, re.M))
        self.frontends.extend([
            {'id': 'qwen-terminal', 'runtime_id': self.id, 'name': tr('Qwen Code — терминал'), 'type': 'terminal',
             'status': 'INSTALLED' if self.command else 'NOT INSTALLED', 'optional': False},
            {'id': 'qwen-daemon', 'runtime_id': self.id, 'name': tr('Qwen Code — фоновый сервер (serve)'), 'type': 'daemon',
             'status': 'SUPPORTED (experimental)' if serve else 'UNSUPPORTED BY INSTALLED VERSION', 'optional': True},
        ])
        extensions = list((Path.home() / '.vscode/extensions').glob('*qwen*'))
        self.frontends.append({'id': 'qwen-vscode', 'runtime_id': self.id, 'name': 'Qwen Code — VS Code',
            'type': 'vscode', 'status': 'INSTALLED' if extensions else 'NOT INSTALLED', 'optional': True})
        return Result(Support.SUPPORTED if self.command else Support.UNSUPPORTED,
            tr('Qwen Code найден') if self.command else tr('Qwen Code не установлен. Установите его и нажмите «Найти агенты заново».'),
            {'installed': bool(self.command), 'version': self.version, 'frontends': self.frontends,
             'provider_schema_confirmed': self.schema_confirmed})

    def get_capabilities(self):
        return Result(Support.SUPPORTED, data={
            'headless': '--prompt' in self.help_text,
            'daemon': bool(re.search(r'^\s*qwen serve\s', self.help_text, re.M)),
            'provider_sync': self.schema_confirmed, 'task_control': False})

    def get_config_locations(self, workspace=None):
        home = Path(self.settings.get('home') or os.environ.get('QWEN_HOME', Path.home() / '.qwen')).expanduser()
        paths = {'user': str(home / 'settings.json')}
        if workspace:
            paths['project'] = str(Path(workspace) / '.qwen/settings.json')
        return Result(Support.SUPPORTED, data=paths)

    def configure_model_provider(self, models, cluster_models=None):
        if not self.schema_confirmed:
            return unsupported(tr('Формат настроек этой версии Qwen Code не распознан — автоматическая настройка модели отключена.'))
        entries = []
        seen_keys = set()
        for model in models:
            if model.id == 'none' or model.status == 'disabled':
                continue
            key = (model.backend_model_id, model.endpoint)
            seen_keys.add(key)
            entries.append({'id': model.backend_model_id, 'name': model.name,
                'baseUrl': model.endpoint, 'envKey': 'LOCAL_AGENT_STATION_API_KEY',
                'generationConfig': {'contextWindowSize': model.context,
                    'timeout': model.startup_timeout * 1000, 'maxRetries': 0,
                    'modalities': {'image': bool(model.vision and model.qualified)}},
                'capabilities': {'vision': bool(model.vision and model.qualified),
                    'agent': bool(model.tool_calling and model.qualified)}})
        # Append models discovered from remote cluster nodes
        for rm in (cluster_models or []):
            key = (rm.id, rm.endpoint)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            entries.append({'id': rm.id, 'name': f'{rm.name} @ {rm.node_name}' if rm.node_name else rm.name,
                'baseUrl': rm.endpoint, 'envKey': 'LOCAL_AGENT_STATION_API_KEY',
                'generationConfig': {'contextWindowSize': rm.context,
                    'timeout': 240000, 'maxRetries': 0,
                    'modalities': {'image': rm.vision}},
                'capabilities': {'vision': rm.vision, 'agent': rm.tool_calling}})
        path = self.get_config_locations().data['user']
        preview = preview_merge(path, [(['modelProviders', MANAGED_ID], entries),
            (['providerProtocol', MANAGED_ID], 'openai')])
        return Result(Support.SUPPORTED, data=preview)

    def configure_model_binding(self, model):
        if not self.schema_confirmed:
            return unsupported(tr('Формат настроек этой версии Qwen Code не распознан — автоматическая настройка модели отключена.'))
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], [
            (['model', 'name'], model.backend_model_id), (['model', 'baseUrl'], model.endpoint),
            (['security', 'auth', 'selectedType'], 'openai')]))

    def binding_ready(self, model):
        try:
            doc = read_document(Path(self.get_config_locations().data['user']), {})
        except Exception:
            return False
        entries = doc.get('modelProviders', {}).get(MANAGED_ID, [])
        listed = any(isinstance(e, dict) and e.get('id') == model.backend_model_id and
                     e.get('baseUrl') == model.endpoint for e in entries)
        selected = doc.get('model', {})
        return (listed and selected.get('name') == model.backend_model_id and
                selected.get('baseUrl') == model.endpoint and
                doc.get('security', {}).get('auth', {}).get('selectedType') == 'openai')

    def list_model_bindings(self):
        doc = read_document(Path(self.get_config_locations().data['user']), {})
        return Result(Support.SUPPORTED, data=doc.get('modelProviders', {}).get(MANAGED_ID, []))

    def validate_configuration(self):
        try:
            values = self.list_model_bindings()
            ids = [entry['id'] for entry in values.data]
            if len(ids) != len(set(ids)):
                raise ValueError(tr('В настройках Qwen Code ({path}) одна и та же модель указана несколько раз. '
                    'Удалите повторы и повторите синхронизацию.', path=self.get_config_locations().data['user']))
            return Result(Support.SUPPORTED, tr('Настройки Qwen Code в порядке'))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def start(self, workspace=None, model=None):
        if not self.command:
            return unsupported(tr('Qwen Code не установлен. Установите его и нажмите «Найти агенты заново».'))
        args = list(self.command)
        if model:
            required = ('--model', '--openai-base-url', '--auth-type')
            if not all(flag in self.help_text for flag in required):
                return unsupported(tr('Эта версия Qwen Code не поддерживает выбор модели при запуске (--model, --openai-base-url, --auth-type). Обновите Qwen Code.'))
            args += ['--model', model.backend_model_id, '--openai-base-url', model.endpoint, '--auth-type', 'openai']
        try:
            environment = dict(self.process_environment(), LOCAL_AGENT_STATION_API_KEY='local-station',
                OPENAI_API_KEY='local-station', QWEN_HOME=str(Path(self.get_config_locations().data['user']).parent))
            return Result(Support.SUPPORTED, data=supervisor.start('agent:' + self.id, args, cwd=workspace,
                env=environment, visible=True))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def smoke(self, model, workspace, timeout=180, configuration_path=None):
        flags = ('--bare', '--safe-mode', '--max-tool-calls', '--max-wall-time', '--auth-type')
        if not self.command or not all(flag in self.help_text for flag in flags):
            return unsupported(tr('Проверка Qwen Code через модель недоступна: Qwen Code не установлен или его версия не поддерживает безопасный режим проверки. Обновите Qwen Code.'))
        args = self.command + ['--bare', '--safe-mode', '--auth-type', 'openai', '--model', model.backend_model_id,
            '--openai-base-url', model.endpoint, '--prompt', 'Reply exactly STATION_OK. Do not use tools.',
            '--system-prompt', 'You are a connectivity test. Reply STATION_OK.', '--max-tool-calls', '0',
            '--max-session-turns', '1', '--max-wall-time', str(timeout - 5), '--output-format', 'json',
            '--chat-recording=false']
        env = dict(os.environ, OPENAI_API_KEY='local-station', LOCAL_AGENT_STATION_API_KEY='local-station',
            QWEN_HOME=str(Path(workspace) / '.qwen-smoke'), QWEN_CODE_DISABLE_AUTO_UPDATE='1')
        if configuration_path:
            args = self.command + ['--safe-mode', '--prompt', 'Reply exactly STATION_OK. Do not use tools.',
                '--max-tool-calls', '0', '--max-session-turns', '1', '--max-wall-time', str(timeout-5),
                '--output-format', 'json', '--chat-recording=false']
            env['QWEN_HOME'] = str(Path(configuration_path).parent)
        try:
            p = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=timeout, **hidden_options())
            events = json.loads(p.stdout) if p.returncode == 0 else []
            results = [e for e in events if e.get('type') == 'result'] if isinstance(events, list) else []
            selected = [e.get('model') for e in events if e.get('type') == 'system' and e.get('subtype') == 'init'] if isinstance(events, list) else []
            passed = p.returncode == 0 and model.backend_model_id in selected and any(
                not e.get('is_error') and e.get('result', '').strip() == 'STATION_OK' for e in results)
            return Result(Support.SUPPORTED if passed else Support.ERROR,
                tr('Проверка Qwen Code через модель пройдена') if passed else
                tr('Проверка Qwen Code через модель не пройдена (код выхода {code}). Убедитесь, что модель запущена, и посмотрите вывод проверки.', code=p.returncode),
                {'exit_code': p.returncode, 'output': p.stdout[-3000:], 'stderr': p.stderr[-1500:]})
        except Exception as exc:
            return Result(Support.ERROR, str(exc))
