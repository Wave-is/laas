import os
import shutil
import subprocess
from pathlib import Path
from ..base import AgentRuntimeAdapter, Result, Support, unsupported, probe
from ..discovery import local_appdata, registered_executables
from ...agent_sync import preview_merge
from ...storage import read_document
from ...supervisor import supervisor

class HermesAdapter(AgentRuntimeAdapter):
    def __init__(self, manifest=None):
        super().__init__(manifest or {'id': 'hermes'})

    def detect(self):
        error = None
        executable = self.settings.get('executable') or shutil.which('hermes')
        self.command = [executable] if executable else []
        self.frontends = [{'id': 'hermes-cli', 'runtime_id': self.id, 'name': 'Hermes CLI',
            'type': 'terminal', 'status': 'INSTALLED' if executable else 'NOT INSTALLED'}]
        if executable:
            try:
                self.version = probe(self.command + ['--version'], env=self.process_environment())
                self.help_text = probe(self.command + ['--help'], env=self.process_environment())
            except Exception as exc:
                error = str(exc)
        desktops = registered_executables('Hermes')
        configured_desktop = self.settings.get('desktop_executable')
        if configured_desktop and Path(configured_desktop).is_file():
            desktops.insert(0, Path(configured_desktop))
        if self.settings.get('source_root'):
            portable = Path(self.settings['source_root']) / 'apps/desktop/release/win-unpacked/Hermes.exe'
            if portable.is_file():
                desktops.append(portable)
        self.frontends.extend([
            {'id': 'hermes-desktop', 'runtime_id': self.id, 'name': 'Hermes Desktop', 'type': 'desktop',
             'executable': str(desktops[0]) if desktops else '', 'status': 'INSTALLED' if desktops else 'NOT INSTALLED'},
            {'id': 'hermes-gateway', 'runtime_id': self.id, 'name': 'Hermes Gateway / Telegram', 'type': 'gateway',
             'status': 'INSTALLED' if 'gateway' in self.help_text else 'NOT INSTALLED'},
            {'id': 'hermes-dashboard', 'runtime_id': self.id, 'name': 'Hermes Dashboard', 'type': 'dashboard',
             'status': 'INSTALLED' if 'dashboard' in self.help_text else 'NOT INSTALLED'},
        ])
        return Result(Support.DEGRADED if error else Support.SUPPORTED if executable else Support.UNSUPPORTED, message=error or '', data={
            'installed': bool(executable), 'version': self.version, 'frontends': self.frontends,
            'config_locations': self.get_config_locations().data})

    def get_config_locations(self, workspace=None):
        explicit = self.settings.get('home') or os.environ.get('HERMES_HOME')
        candidates = [Path(explicit)] if explicit else [local_appdata() / 'hermes', Path.home() / '.hermes']
        primary = next((p for p in candidates if (p / 'config.yaml').exists()), candidates[0])
        return Result(Support.SUPPORTED, data={'user': str(primary / 'config.yaml'),
            'alternatives': [str(p / 'config.yaml') for p in candidates if p != primary and (p / 'config.yaml').exists()],
            'skills': str(primary / 'skills'), 'memory': str(primary / 'memories'),
            'sessions': str(primary / 'sessions'), 'state_db': str(primary / 'state.db')})

    def configure_model_provider(self, models):
        if not self.command:
            return unsupported('Hermes runtime is not installed')
        entries = {m.id: {'api': m.endpoint, 'transport': 'chat_completions',
            'key_env': 'LOCAL_AGENT_STATION_API_KEY', 'default_model': m.backend_model_id,
            'models': [m.backend_model_id], 'context_length': m.context}
            for m in models if m.id != 'none' and m.status != 'disabled'}
        changes = [(['providers', 'local-agent-station-' + mid], value) for mid, value in entries.items()]
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], changes))

    def configure_model_binding(self, model):
        return Result(Support.SUPPORTED, data=preview_merge(self.get_config_locations().data['user'], [
            (['model', 'default'], model.backend_model_id),
            (['model', 'provider'], 'local-agent-station-' + model.id),
            (['model', 'base_url'], model.endpoint)]))

    def list_model_bindings(self):
        doc = read_document(Path(self.get_config_locations().data['user']), {})
        return Result(Support.SUPPORTED, data={k: v for k, v in doc.get('providers', {}).items()
            if k.startswith('local-agent-station-')})

    def validate_configuration(self):
        try:
            locations = self.get_config_locations().data
            current = read_document(Path(locations['user']), {})
            for other in locations['alternatives']:
                if read_document(Path(other), {}).get('model') != current.get('model'):
                    return Result(Support.DEGRADED, 'CONFIGURATION DIVERGED')
            return Result(Support.SUPPORTED, 'Configuration is valid')
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def start(self, workspace=None, model=None):
        if not self.command:
            return unsupported('Hermes runtime is not installed')
        args = list(self.command)
        if model:
            if '--model' not in self.help_text:
                return unsupported('Installed CLI does not confirm model binding flag')
            args += ['--model', model.backend_model_id]
            if '--provider' not in self.help_text:
                return unsupported('Installed CLI does not confirm explicit provider selection')
            args += ['--provider', 'custom']
        env = dict(self.process_environment(), LOCAL_AGENT_STATION_API_KEY='local-station',
            HERMES_HOME=str(Path(self.get_config_locations().data['user']).parent))
        if model:
            env.update(OPENAI_BASE_URL=model.endpoint, OPENAI_API_KEY='local-station')
        try:
            return Result(Support.SUPPORTED, data=supervisor.start('agent:' + self.id, args, cwd=workspace, env=env, visible=True))
        except Exception as exc:
            return Result(Support.ERROR, str(exc))

    def smoke(self, model, workspace, timeout=180, configuration_path=None):
        from ...hardware import hidden_options
        from ...storage import atomic_write
        if not self.command:
            return unsupported('Hermes runtime is not installed')
        home = Path(workspace) / '.hermes-smoke'
        home.mkdir(parents=True, exist_ok=True)
        atomic_write(home / 'config.yaml', {'model': {'provider': 'custom', 'default': model.backend_model_id,
            'base_url': model.endpoint}, 'agent': {'max_iterations': 1}})
        args = self.command + ['--safe-mode', '--toolsets', 'context_engine', '--provider', 'custom', '--model', model.backend_model_id,
            '--reasoning', 'none', '--oneshot', 'Reply exactly STATION_OK. Do not use any tools.']
        env = dict(self.process_environment(), HERMES_HOME=str(home), HERMES_MAX_ITERATIONS='1',
            OPENAI_BASE_URL=model.endpoint, OPENAI_API_KEY='local-station', LOCAL_AGENT_STATION_API_KEY='local-station')
        if configuration_path:
            document = read_document(Path(configuration_path), {})
            provider_id = 'local-agent-station-' + model.id
            provider = document.get('providers', {}).get(provider_id)
            if not provider:
                return Result(Support.ERROR, 'Managed provider is missing from the saved configuration')
            # Verify the saved provider in an isolated home, without user hooks or sessions.
            atomic_write(home / 'config.yaml', {'providers': {provider_id: provider},
                'model': {'provider': provider_id, 'default': model.backend_model_id},
                'agent': {'max_iterations': 1}})
            args = self.command + ['--ignore-rules', '--toolsets', 'context_engine', '--provider', provider_id,
                '--model', model.backend_model_id, '--reasoning', 'none',
                '--oneshot', 'Reply exactly STATION_OK. Do not use any tools.']
            env.pop('OPENAI_BASE_URL', None)
        try:
            p = subprocess.run(args, cwd=workspace, env=env, capture_output=True, text=True,
                encoding='utf-8', errors='replace', timeout=timeout, **hidden_options())
            passed = p.returncode == 0 and p.stdout.strip() == 'STATION_OK'
            return Result(Support.SUPPORTED if passed else Support.ERROR, 'Hermes smoke passed' if passed else 'Hermes smoke failed',
                {'exit_code': p.returncode, 'output': p.stdout[-1200:], 'stderr': p.stderr[-1200:]})
        except Exception as exc:
            return Result(Support.ERROR, str(exc))
