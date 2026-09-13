"""Agent-neutral application orchestration. Frontend changes do not touch GPUs/models."""
from pathlib import Path
import shutil
from .agents import load_adapters
from .config import config
from .profile_storage import profile_storage
from .gpu_modes import gpu_mode_manager
from .paths import data_dir
from .storage import read_document
from .supervisor import supervisor
from .agents.base import probe

class StationController:
    def __init__(self):
        self.adapters = load_adapters()
        self.agent_status = {}
        self.frontends = {}

    def discover_agents(self):
        self.frontends = {}
        settings = {row['id']: row for row in read_document(data_dir() / 'config/agent_runtimes.yaml', [])}
        for id, adapter in self.adapters.items():
            adapter.settings = settings.get(id, {})
            if adapter.settings.get('enabled') is False:
                continue
            try:
                result = adapter.detect()
            except Exception as exc:
                from .agents.base import Result, Support
                result = Result(Support.ERROR, str(exc))
            self.agent_status[id] = result.to_dict()
            for frontend in adapter.frontends:
                self.frontends[frontend['id']] = frontend
        for row in read_document(data_dir() / 'config/agent_frontends.yaml', []):
            row = dict(row)
            row.setdefault('name', row.get('display_name', row['id']))
            row.setdefault('type', row.get('frontend_type', 'desktop'))
            if 'status' not in row:
                row['status'] = 'INSTALLED' if Path(row.get('executable', '')).is_file() else 'NOT INSTALLED'
            self.frontends[row['id']] = row
        return self.agent_status

    def preview_sync(self, runtime_id, bind_model=None):
        from .agent_sync import combine_previews
        adapter = self.adapters[runtime_id]
        provider = adapter.configure_model_provider(list(profile_storage.model_profiles.values()))
        if not provider.ok:
            raise ValueError(provider.message)
        previews = [provider.data]
        if bind_model and bind_model.id != 'none':
            binding = adapter.configure_model_binding(bind_model)
            if not binding.ok:
                raise ValueError(binding.message)
            previews.append(binding.data)
        return combine_previews(previews)

    def installation_options(self, runtime_id):
        """Expose official release links even when discovery found no executable."""
        adapter = self.adapters[runtime_id]
        url = adapter.manifest.get('releases_url')
        if not url:
            return []
        from urllib.parse import urlsplit
        parsed = urlsplit(url)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('Official releases URL must be HTTPS without embedded credentials')
        options = [{'label': 'Официальные релизы' if adapter.command else 'Установить ↗',
                    'url': url, 'missing': not bool(adapter.command)}]
        if adapter.command and any(f['type'] == 'desktop' and f['status'] == 'NOT INSTALLED' for f in adapter.frontends):
            options.append({'label': 'Установить Desktop ↗', 'url': url, 'missing': True})
        return options

    def open_installation_page(self, runtime_id):
        import webbrowser
        options = self.installation_options(runtime_id)
        if not options:
            return {'Success': False, 'Message': 'Для этого агента страница релизов не задана.'}
        if not webbrowser.open(options[0]['url'], new=2):
            return {'Success': False, 'Message': 'Браузер не открылся. Страница релизов: ' + options[0]['url']}
        return {'Success': True, 'Message': 'Открыта официальная страница. После установки нажмите «Повторить обнаружение».'}

    def select_runtime(self, id):
        if id not in self.adapters:
            raise ValueError('Unknown runtime')
        config.set('primary_agent_runtime', id)
        return {'Success': True, 'Message': 'Agent runtime selected'}

    def select_frontend(self, id):
        frontend = self.frontends.get(id)
        if not frontend:
            raise ValueError('Unknown frontend')
        if frontend['status'] in ('NOT INSTALLED', 'UNSUPPORTED BY INSTALLED VERSION'):
            alternatives = [f['name'] for f in self.frontends.values() if f['runtime_id'] == frontend['runtime_id'] and f['status'] == 'INSTALLED']
            return {'Success': False, 'Message': 'Frontend is unavailable. Available: ' + ', '.join(alternatives)}
        config.update({'preferred_frontend': id, 'primary_agent_runtime': frontend['runtime_id']})
        return {'Success': True, 'Message': 'Frontend selected'}

    def frontend_status(self, id):
        frontend = self.frontends.get(id)
        if not frontend:
            return {'running': False, 'owned': False, 'pid': None}
        key = 'agent:' + frontend['runtime_id'] if frontend['type'] == 'terminal' else 'frontend:' + id
        return supervisor.status(key)

    def launch_frontend(self, id=None, *, remember=True):
        id = id or config.get('preferred_frontend')
        frontend = self.frontends.get(id)
        if not frontend or frontend.get('status') not in ('INSTALLED', 'SUPPORTED (experimental)'):
            return {'Success': False, 'Message': 'Интерфейс агента недоступен. Повторите обнаружение после установки.'}
        selected = self.select_frontend(id) if remember else {'Success': True}
        if not selected['Success']:
            return selected
        if self.frontend_status(id)['running']:
            return {'Success': True, 'Message': 'Этот интерфейс агента уже запущен Station.'}
        adapter = self.adapters[frontend['runtime_id']]
        workspace = config.get('workspace') or str(Path.home())
        model = gpu_mode_manager.get_active_model_profile()
        model = model if model and model.id != 'none' else None
        if frontend['type'] == 'terminal':
            result = adapter.start(workspace, model)
            return {'Success': result.ok, 'Message': result.message or 'Terminal opened', 'Details': result.data}
        if frontend.get('adapter_launch'):
            result = adapter.launch_frontend(frontend, workspace, model)
            return {'Success': result.ok, 'Message': result.message, 'Details': result.data}
        args = []
        visible = False
        if frontend['type'] == 'desktop':
            executable = frontend.get('executable')
            if not executable or not Path(executable).is_file():
                return {'Success': False, 'Message': 'Desktop executable not found'}
            args = [executable] + frontend.get('launch_arguments', [])
        elif frontend['type'] == 'daemon':
            help_text = probe(adapter.command + ['serve', '--help'])
            if '--hostname' not in help_text:
                return {'Success': False, 'Message': 'Installed daemon does not confirm loopback binding options'}
            port = adapter.settings.get('daemon_port', 4170)
            if type(port) is not int or not 1024 <= port <= 65535:
                return {'Success': False, 'Message': 'Daemon port must be an integer from 1024 to 65535'}
            args = adapter.command + ['serve', '--hostname', '127.0.0.1', '--port', str(port)]
        elif frontend['type'] == 'gateway':
            args = adapter.command + ['gateway', 'run']
        elif frontend['type'] == 'dashboard':
            args = adapter.command + ['dashboard']
        elif frontend['type'] == 'vscode':
            launcher = shutil.which('code')
            # Invoke the native editor executable, never interpolate a shell command.
            executable = Path(launcher).parent.parent / 'Code.exe' if launcher else None
            if not executable or not executable.is_file():
                return {'Success': False, 'Message': 'VS Code executable not found'}
            args = [str(executable), workspace]
        else:
            return {'Success': False, 'Message': 'Unsupported frontend type'}
        environment = adapter.process_environment()
        environment['LOCAL_AGENT_STATION_API_KEY'] = 'local-station'
        locations = adapter.get_config_locations().data or {}
        home_variable = adapter.manifest.get('home_environment')
        if home_variable and locations.get('user'):
            environment[home_variable] = str(Path(locations['user']).parent)
        result = supervisor.start('frontend:' + id, args, cwd=workspace, visible=visible, env=environment)
        return {'Success': result['running'], 'Message': 'Frontend process started', 'Details': result}

    def stop_frontend(self, id):
        frontend = self.frontends.get(id)
        if not frontend:
            return {'Success': False, 'Message': 'Unknown frontend'}
        if frontend['type'] == 'terminal':
            result = self.adapters[frontend['runtime_id']].stop()
            return {'Success': result.ok, 'Message': result.message}
        result = supervisor.stop('frontend:' + id)
        return {'Success': result['success'], 'Message': result['message']}

    def apply_preset(self, preset_id):
        preset = profile_storage.get_station_preset(preset_id)
        if not preset:
            return {'Success': False, 'Message': 'Preset not found'}
        if preset.primary_agent_runtime not in self.adapters:
            return {'Success': False, 'Message': 'Preset runtime is unavailable'}
        if preset.auto_start_agents and preset.preferred_frontend not in self.frontends:
            return {'Success': False, 'Message': 'Preset frontend is unavailable'}
        result = gpu_mode_manager.apply_preset(preset_id)
        if result.get('Success') and preset.auto_start_agents:
            result = self.launch_frontend(preset.preferred_frontend)
            if result.get('Success'):
                try:
                    for runtime in preset.background_agent_runtimes:
                        frontend = next((f['id'] for f in self.frontends.values() if f['runtime_id'] == runtime and f['type'] == 'gateway'), runtime)
                        background = self.launch_frontend(frontend)
                        if not background.get('Success'):
                            return background
                finally:
                    config.set('preferred_frontend', preset.preferred_frontend)
        return result
