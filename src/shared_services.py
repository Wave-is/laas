"""Local process ownership, remote connections, and opt-in HTTP health checks."""
from copy import deepcopy
from pathlib import Path
import threading
import time
import requests
from .paths import data_dir
from .storage import read_document, atomic_write, digest
from .supervisor import supervisor
from .validation import validate_registry
from .secrets_store import secret_store
from .i18n import tr


class SharedServices:
    def __init__(self, path=None):
        self.path = Path(path or data_dir() / 'config/services.yaml')
        self.desired = set()
        self.failures, self.retry_at = {}, {}
        self._health, self._next_probe, self._signatures = {}, {}, {}
        self._lock = threading.RLock()

    def profiles(self):
        rows = read_document(self.path, [])
        return {p['id']: p for p in validate_registry('services.yaml', rows)}

    def edit_snapshot(self, id=None):
        with self._lock:
            expected = digest(self.path)
            return deepcopy(self.profiles().get(id, {})), expected

    def save(self, profile, expected_digest):
        with self._lock:
            rows = self.profiles()
            id = profile['id']
            old = rows.get(id)
            if old and old != profile and supervisor.status('service:' + id)['running']:
                raise ValueError(tr('Сначала остановите запущенный Station сервис, затем измените его настройки.'))
            rows[id] = deepcopy(profile)
            validate_registry('services.yaml', list(rows.values()))
            atomic_write(self.path, list(rows.values()), expected_digest=expected_digest)
            self._health.pop(id, None)
            self._next_probe.pop(id, None)

    def remove(self, id, expected_digest):
        with self._lock:
            rows = self.profiles()
            if rows[id].get('type', 'local') == 'local' and supervisor.status('service:' + id)['running']:
                raise ValueError(tr('Сначала остановите этот сервис в Station.'))
            rows.pop(id)
            atomic_write(self.path, list(rows.values()), expected_digest=expected_digest)
            self.desired.discard(id)
            self._health.pop(id, None)
            self._next_probe.pop(id, None)

    def _probe(self, profile):
        from .service_profiles import http_url
        url = http_url(profile['health_url'])
        result = {'health': 'UNAVAILABLE', 'checked_at': time.time()}
        headers = {}
        try:
            if profile.get('token_reference'):
                headers['Authorization'] = 'Bearer ' + secret_store.get(profile['token_reference'])
            with requests.get(url, headers=headers, timeout=(2, 2), allow_redirects=False, stream=True) as response:
                result['http_status'] = response.status_code
                if not 200 <= response.status_code < 300:
                    result.update(health='ERROR', message=tr('Сервер ответил HTTP {code}. Проверьте адрес и доступ.', code=response.status_code))
                elif profile.get('kind') == 'comfyui':
                    import json
                    payload = bytearray()
                    for chunk in response.iter_content(8192):
                        payload.extend(chunk)
                        if len(payload) > 256 * 1024:
                            raise ValueError(tr('Слишком большой ответ проверки ComfyUI.'))
                    try:
                        info = json.loads(payload)
                    except ValueError:
                        info = None
                    if not isinstance(info, dict) or not isinstance(info.get('system'), dict) or not isinstance(info.get('devices'), list):
                        result.update(health='ERROR', message=tr('Ответ не похож на ComfyUI. Проверьте адрес; для отдельного image worker выберите HTTP-сервис.'))
                    else:
                        result.update(health='READY', message=tr('ComfyUI отвечает. Генерация при проверке не запускается.'))
                else:
                    result.update(health='READY', message=tr('Сервис отвечает на проверку доступности.'))
        except requests.Timeout:
            result['message'] = tr('Сервер не ответил вовремя. Возможно, он занят; повторите проверку позже.')
        except requests.RequestException:
            result['message'] = tr('Не удалось подключиться. Проверьте адрес, сеть/VPN и доступность сервера.')
        except Exception as exc:
            result.update(health='ERROR', message=tr('Не удалось проверить сервис: {error}', error=exc))
        return result

    def status(self, id, force=False):
        with self._lock:
            p = self.profiles()[id]
            remote = p.get('type', 'local') == 'remote'
            process = {'running': False, 'owned': False, 'pid': None} if remote else supervisor.status('service:' + id)
            signature = (p.get('health_url'), p.get('kind'), p.get('token_reference'))
            if self._signatures.get(id) != signature:
                self._health.pop(id, None)
                self._next_probe.pop(id, None)
                self._signatures[id] = signature
            monitoring = p.get('monitor_enabled', True)
            if p.get('health_url') and (force or monitoring and time.monotonic() >= self._next_probe.get(id, 0)):
                self._health[id] = self._probe(p)
                self._next_probe[id] = time.monotonic() + 30
            result = {**process, **deepcopy(self._health.get(id, {'health': 'UNKNOWN'})),
                'remote': remote, 'monitor_paused': not monitoring}
            if 'message' not in result:
                result['message'] = (tr('Нажмите «Проверить», когда сервер будет свободен.') if p.get('health_url') else
                    tr('Проверка HTTP не настроена; показано только состояние процесса.'))
            return result

    def check(self, id):
        state = self.status(id, force=True)
        return {'Success': state['health'] == 'READY', 'Message': state['message'], 'Service': id, 'State': state}

    def start(self, id):
        p = self.profiles()[id]
        if p.get('type') == 'remote':
            return self.check(id)
        exe = p.get('executable', '')
        if not Path(exe).is_file():
            raise ValueError(tr('Программа сервиса не найдена. Проверьте путь в настройках карточки.'))
        env = {key: secret_store.get(ref) for key, ref in p.get('environment_references', {}).items()}
        result = supervisor.start('service:' + id, [exe] + p.get('arguments', []), cwd=p.get('working_directory'), env=env)
        self.desired.add(id)
        self.failures[id] = 0
        self._next_probe.pop(id, None)
        return {'Success': result['running'], 'Message': tr('Процесс сервиса запущен. Доступность проверяется отдельно.'), 'Details': result}

    def stop(self, id):
        if self.profiles()[id].get('type') == 'remote':
            return {'Success': False, 'Message': tr('Этот сервис работает отдельно. Остановить его можно на том компьютере.')}
        self.desired.discard(id)
        result = supervisor.stop('service:' + id)
        self._health.pop(id, None)
        self._next_probe.pop(id, None)
        return {'Success': result['success'], 'Message': tr('Сервис остановлен') if result['success'] else result['message']}

    def poll(self, allow_restart=True):
        result = {}
        for id, p in self.profiles().items():
            try:
                state = self.status(id)
            except KeyError:
                continue
            result[id] = state
            if (allow_restart and p.get('type', 'local') == 'local' and id in self.desired and not state['running']
                    and p.get('restart_policy') == 'on_failure' and self.failures.get(id, 0) < 3
                    and time.monotonic() > self.retry_at.get(id, 0)):
                count = self.failures.get(id, 0) + 1
                try:
                    self.start(id)
                except Exception:
                    pass
                self.failures[id] = count
                self.retry_at[id] = time.monotonic() + min(300, 30 * 2 ** count)
        return result


shared_services = SharedServices()
