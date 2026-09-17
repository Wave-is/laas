"""The model server (llama-swap) lifecycle. Every start uses the Station-generated configuration."""
import time
from pathlib import Path
from . import model_server
from .supervisor import supervisor
from .engines.llama_swap import LlamaSwapEngine
from .i18n import tr

KEY = 'service:llama-swap'


def _result(success, message, **details):
    return {'Success': success, 'Message': message, **details}


class ProcessManager:
    def is_llama_swap_running(self):
        return LlamaSwapEngine().is_online()

    def _owned_command(self):
        process = supervisor.owned_process(KEY)
        if not process:
            return None
        try:
            return process.cmdline()
        except Exception:
            return None

    def _listener(self):
        """(PID, bind address) of the process listening on the server port."""
        try:
            import psutil
            port = model_server.port()
            for connection in psutil.net_connections('tcp'):
                if connection.status == 'LISTEN' and connection.laddr and connection.laddr.port == port:
                    ip = connection.laddr.ip
                    return connection.pid, ('0.0.0.0' if ip in ('0.0.0.0', '::') else ip) + f':{port}'
        except Exception:
            pass
        return None, None

    def _port_owner(self):
        return self._listener()[0]

    def backend_info(self, online=None):
        """Everything the UI needs to say which server runs, where, and who owns it."""
        online = self.is_llama_swap_running() if online is None else online
        status = supervisor.status(KEY)
        command = self._owned_command() if status['owned'] else None
        listen = None
        config_path = None
        if command:
            for flag, value in zip(command, command[1:]):
                if flag == '-listen':
                    listen = value
                elif flag == '-config':
                    config_path = value
        generated = model_server.generated_config_path()
        info = {'online': online, 'owned': status['owned'], 'pid': status['pid'],
                'url': model_server.local_url(), 'api_url': model_server.api_url(),
                'listen': listen, 'lan': bool(listen and listen.startswith(('0.0.0.0', '[::]'))),
                'lan_urls': [], 'config_path': config_path or str(generated),
                'stale_config': bool(config_path) and Path(config_path) != generated,
                'log': supervisor.records.get(KEY, {}).get('log'),
                'executable': str(model_server.swap_executable() or '')}
        if online and not status['owned']:
            info['pid'], info['listen'] = self._listener()
            info['lan'] = bool(info['listen'] and info['listen'].startswith('0.0.0.0'))
        if info['lan']:
            port = model_server.port()
            info['lan_urls'] = [f'http://{ip}:{port}/v1' for ip in model_server.lan_addresses()]
        return info

    def describe(self, info=None):
        info = info or self.backend_info()
        if not info['online']:
            return tr('Остановлен')
        if info['owned']:
            who = tr('запущен Station, PID {pid}', pid=info['pid'])
        else:
            who = tr('запущен вне Station, PID {pid}', pid=info['pid']) if info['pid'] else tr('запущен вне Station')
        where = info['listen'] or info['url'].removeprefix('http://')
        return tr('Работает на {address} ({owner})', address=where, owner=who)

    def start_llama_swap(self, config_path=None):
        """Start the server with the generated configuration. Returns a result dictionary."""
        path = Path(config_path) if config_path else model_server.generated_config_path()
        if self.is_llama_swap_running():
            info = self.backend_info(True)
            if not info['owned']:
                if info['pid']:
                    message = tr('{server} уже работает на {url}, но запущен не Station (PID {pid}). Station не управляет его конфигурацией.',
                        server=model_server.SERVER_TITLE, url=info['url'], pid=info['pid'])
                else:
                    message = tr('{server} уже работает на {url}, но запущен не Station. Station не управляет его конфигурацией.',
                        server=model_server.SERVER_TITLE, url=info['url'])
                return _result(True, message, Info=info)
            return _result(True, tr('{server} уже работает: {url}', server=model_server.SERVER_TITLE, url=info['url']), Info=info)
        exe = model_server.swap_executable()
        if not exe:
            return _result(False, model_server.describe_missing_runtime())
        if not path.is_file():
            return _result(False, tr('Нет конфигурации сервера моделей ({path}). Загрузите модель на странице «Станция» — '
                'Station создаст конфигурацию из профилей моделей.', path=path))
        listen = model_server.listen_address()
        supervisor.start(KEY, [str(exe), '-config', str(path), '-listen', listen], cwd=str(exe.parent))
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if not supervisor.status(KEY)['running']:
                log = supervisor.records.get(KEY, {}).get('log', '')
                return _result(False, tr('{server} завершился сразу после запуска. Возможно, порт {port} занят. Журнал: {log}',
                    server=model_server.SERVER_TITLE, port=model_server.port(), log=log))
            if self.is_llama_swap_running():
                info = self.backend_info(True)
                extra = (' ' + tr('Доступ из сети: {urls}', urls=', '.join(info['lan_urls']))) if info['lan_urls'] else ''
                return _result(True, tr('{server} запущен: {url} (PID {pid}).', server=model_server.SERVER_TITLE,
                    url=info['api_url'], pid=info['pid']) + extra, Info=info)
            time.sleep(.25)
        return _result(False, tr('{server} не ответил за 15 секунд на {url}. Журнал: {log}', server=model_server.SERVER_TITLE,
            url=model_server.local_url(), log=supervisor.records.get(KEY, {}).get('log', '')))

    def stop_llama_swap(self):
        if not self.is_llama_swap_running() and not supervisor.status(KEY)['running']:
            return _result(True, tr('{server} уже остановлен.', server=model_server.SERVER_TITLE))
        if self.is_llama_swap_running() and not supervisor.status(KEY)['owned']:
            pid = self._port_owner()
            if pid:
                message = tr('{server} на {url} запущен не Station (PID {pid}). Остановите его там, где запускали.',
                    server=model_server.SERVER_TITLE, url=model_server.local_url(), pid=pid)
            else:
                message = tr('{server} на {url} запущен не Station. Остановите его там, где запускали.',
                    server=model_server.SERVER_TITLE, url=model_server.local_url())
            return _result(False, message)
        pid = supervisor.status(KEY)['pid']
        result = supervisor.stop(KEY)
        if result['success']:
            return _result(True, tr('{server} остановлен (PID {pid}), порт {port} свободен.',
                server=model_server.SERVER_TITLE, pid=pid, port=model_server.port()))
        return _result(False, tr('Не удалось остановить {server} (PID {pid}): {error}',
            server=model_server.SERVER_TITLE, pid=pid, error=result['message']))

    def free_gpu(self):
        # No kill-by-name fallback. Backend API manages its own models.
        if self.is_llama_swap_running() and not supervisor.status(KEY)['owned']:
            return False
        if not self.is_llama_swap_running():
            return True
        engine = LlamaSwapEngine()
        if not engine.unload_models():
            # Some Windows llama-swap builds cannot signal a CREATE_NO_WINDOW child.
            # Only the independently identified Station-owned process tree may be stopped.
            return self.stop_llama_swap()['Success']
        try:
            if not engine.request('/running', timeout=5).get('running', []):
                return True
            return self.stop_llama_swap()['Success']
        except Exception:
            return False

    def stop_all(self):
        success = True
        for key in list(supervisor.records):
            if key.startswith('service:'):
                success = supervisor.stop(key)['success'] and success
        return success

pm = ProcessManager()
