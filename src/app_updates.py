"""Station update check and OTA self-updater through GitHub releases.
Runs only when requested or enabled (``app_update_check``).
"""
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request

from .i18n import tr
from .version import VERSION

log = logging.getLogger(__name__)

REPOSITORY = 'Wave-is/laas'
RELEASES_API = f'https://api.github.com/repos/{REPOSITORY}/releases'
RELEASES_PAGE = f'https://github.com/{REPOSITORY}/releases'
SEMVER = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')


def parse_semver(text):
    match = SEMVER.match((text or '').strip())
    if not match:
        return None
    pre = tuple(int(p) if p.isdigit() else p for p in match.group(4).split('.')) if match.group(4) else ()
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), pre


def _pre_key(pre):
    # SemVer 2.0: numeric identifiers sort before alphanumeric ones; a release sorts after its pre-releases.
    return tuple((0, p, '') if isinstance(p, int) else (1, 0, p) for p in pre)


def compare(a, b):
    """-1, 0 or 1 comparing two version strings (0.1.0-beta.1 < 0.1.0-beta.2 < 0.1.0 < 0.1.1)."""
    pa, pb = parse_semver(a), parse_semver(b)
    if not pa or not pb:
        raise ValueError(tr('Неверный номер версии: {version}', version=a if not pa else b))
    if pa[0] != pb[0]:
        return -1 if pa[0] < pb[0] else 1
    if pa[1] == pb[1]:
        return 0
    if not pa[1]:
        return 1
    if not pb[1]:
        return -1
    ka, kb = _pre_key(pa[1]), _pre_key(pb[1])
    return -1 if ka < kb else 1 if ka > kb else 0


def fetch_json(url, timeout=15):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'LAAS/' + VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def check(current=VERSION, fetch=None):
    """{'Success', 'Message', 'Release': {'version','name','url','prerelease','published','installer_url',...} or None}."""
    fetcher = fetch or fetch_json
    try:
        rows = fetcher(RELEASES_API)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404):
            return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: репозиторий недоступен или приватный.')}
        if exc.code == 403:
            return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: GitHub ограничил число запросов, попробуйте позже.')}
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: GitHub ответил ошибкой {code}.', code=exc.code)}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: нет соединения с GitHub ({error}).', error=getattr(exc, 'reason', exc))}
    if not isinstance(rows, list):
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: неожиданный ответ GitHub.')}
    allow_pre = bool(parse_semver(current) and parse_semver(current)[1])
    best = None
    for row in rows:
        version = (row.get('tag_name') or '').lstrip('v')
        parsed = parse_semver(version)
        if row.get('draft') or not parsed or (parsed[1] and not allow_pre):
            continue
        if version.startswith('3.0.0-alpha'):
            continue
        if best is None or compare(version, best['version']) > 0:
            installer = None
            checksums = None
            for a in row.get('assets', []):
                name = a.get('name', '')
                if name.endswith('-Setup-x64.exe') or (name.startswith('LocalAgentAIStation') and name.endswith('.exe')):
                    installer = a
                elif name == 'SHA256SUMS.txt':
                    checksums = a
            best = {
                'version': version,
                'name': row.get('name') or version,
                'url': row.get('html_url') or RELEASES_PAGE,
                'prerelease': bool(parsed[1]),
                'published': (row.get('published_at') or '')[:10],
                'body': row.get('body') or '',
                'installer_url': installer.get('browser_download_url') if installer else None,
                'installer_name': installer.get('name') if installer else None,
                'installer_size': installer.get('size', 0) if installer else 0,
                'checksums_url': checksums.get('browser_download_url') if checksums else None,
            }
    if best and compare(best['version'], current) > 0:
        return {'Success': True, 'Release': best,
                'Message': tr('Доступна новая версия Station {version} (у вас {current}).', version=best['version'], current=current)}
    return {'Success': True, 'Release': None, 'Message': tr('Установлена последняя версия Station ({current}).', current=current)}


class UpdateState:
    IDLE = 'IDLE'
    CHECKING = 'CHECKING'
    AVAILABLE = 'AVAILABLE'
    DOWNLOADING = 'DOWNLOADING'
    READY = 'READY'
    INSTALLING = 'INSTALLING'
    ERROR = 'ERROR'


class UpdateManager:
    STATE_IDLE = UpdateState.IDLE
    STATE_CHECKING = UpdateState.CHECKING
    STATE_AVAILABLE = UpdateState.AVAILABLE
    STATE_DOWNLOADING = UpdateState.DOWNLOADING
    STATE_READY = UpdateState.READY
    STATE_INSTALLING = UpdateState.INSTALLING
    STATE_ERROR = UpdateState.ERROR

    def __init__(self):
        self._lock = threading.Lock()
        self.state = self.STATE_IDLE
        self.release_info = None
        self.progress = 0.0
        self.downloaded_bytes = 0
        self.total_bytes = 0
        self.installer_path = None
        self.error_message = None
        self._cancel_flag = False
        self._subscribers = []
        self._download_thread = None

    def subscribe(self, callback):
        with self._lock:
            if callback not in self._subscribers:
                self._subscribers.append(callback)

    def unsubscribe(self, callback):
        with self._lock:
            if callback in self._subscribers:
                self._subscribers.remove(callback)

    def _notify(self):
        with self._lock:
            subscribers = list(self._subscribers)
        data = self.get_status_dict()
        for cb in subscribers:
            try:
                cb(self.state, data)
            except Exception:
                log.exception('UpdateManager subscriber failed')

    def get_status_dict(self):
        return {
            'state': self.state,
            'release': self.release_info,
            'progress': self.progress,
            'downloaded_bytes': self.downloaded_bytes,
            'total_bytes': self.total_bytes,
            'installer_path': str(self.installer_path) if self.installer_path else None,
            'error': self.error_message,
        }

    def is_running_from_source(self):
        return not getattr(sys, 'frozen', False)

    def check_updates(self, background=True, callback=None):
        def _run():
            self.state = self.STATE_CHECKING
            self.error_message = None
            self._notify()
            res = check()
            if res.get('Success') and res.get('Release'):
                self.release_info = res['Release']
                cache_dir = Path(tempfile.gettempdir()) / 'LAAS_Update'
                name = self.release_info.get('installer_name')
                existing = (cache_dir / name) if name else None
                if existing and existing.is_file() and existing.stat().st_size == self.release_info.get('installer_size', 0):
                    self.installer_path = existing
                    self.state = self.STATE_READY
                else:
                    self.state = self.STATE_AVAILABLE
            elif res.get('Success'):
                self.release_info = None
                self.state = self.STATE_IDLE
            else:
                self.error_message = res.get('Message')
                self.state = self.STATE_ERROR
            self._notify()
            if callback:
                callback(res)
            if self.state == self.STATE_AVAILABLE:
                try:
                    from .config import config
                    if config.get('app_update_auto_download', False):
                        self.start_download()
                except Exception:
                    pass

        if background:
            threading.Thread(target=_run, daemon=True).start()
        else:
            _run()

    def start_download(self):
        if self.state == self.STATE_DOWNLOADING or not self.release_info:
            return
        url = self.release_info.get('installer_url')
        if not url:
            self.error_message = tr('В релизе нет установочного файла Windows.')
            self.state = self.STATE_ERROR
            self._notify()
            return

        self._cancel_flag = False
        self.state = self.STATE_DOWNLOADING
        self.progress = 0.0
        self.downloaded_bytes = 0
        self.total_bytes = self.release_info.get('installer_size', 0)
        self.error_message = None
        self._notify()

        def _do_download():
            try:
                dest_dir = Path(tempfile.gettempdir()) / 'LAAS_Update'
                dest_dir.mkdir(parents=True, exist_ok=True)
                name = self.release_info.get('installer_name') or 'LocalAgentAIStation-Setup-x64.exe'
                target_file = dest_dir / name
                part_file = dest_dir / (name + '.part')

                expected_sha256 = None
                chk_url = self.release_info.get('checksums_url')
                if chk_url:
                    try:
                        req = urllib.request.Request(chk_url, headers={'User-Agent': 'LAAS/' + VERSION})
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            chk_text = resp.read().decode('utf-8', errors='ignore')
                            for line in chk_text.splitlines():
                                parts = line.strip().split()
                                if len(parts) >= 2 and parts[1].strip('*') == name:
                                    expected_sha256 = parts[0].lower()
                                    break
                    except Exception as exc:
                        log.warning('Could not fetch checksums: %s', exc)

                req = urllib.request.Request(url, headers={'User-Agent': 'LAAS/' + VERSION})
                hasher = hashlib.sha256()
                with urllib.request.urlopen(req, timeout=30) as resp, open(part_file, 'wb') as out_f:
                    total = int(resp.headers.get('Content-Length') or self.total_bytes or 0)
                    self.total_bytes = total
                    downloaded = 0
                    while not self._cancel_flag:
                        chunk = resp.read(131072)
                        if not chunk:
                            break
                        out_f.write(chunk)
                        hasher.update(chunk)
                        downloaded += len(chunk)
                        self.downloaded_bytes = downloaded
                        self.progress = (downloaded / total) if total > 0 else 0.0
                        self._notify()

                if self._cancel_flag:
                    if part_file.exists():
                        part_file.unlink()
                    self.state = self.STATE_AVAILABLE
                    self._notify()
                    return

                calc_hash = hasher.hexdigest().lower()
                if expected_sha256 and calc_hash != expected_sha256:
                    if part_file.exists():
                        part_file.unlink()
                    raise ValueError(tr('Контрольная сумма скачанного обновления не совпадает (ожидалось {expected}, получено {actual}).', expected=expected_sha256[:12], actual=calc_hash[:12]))

                if target_file.exists():
                    target_file.unlink()
                part_file.rename(target_file)
                self.installer_path = target_file
                self.state = self.STATE_READY
                self.progress = 1.0
                self._notify()
            except Exception as exc:
                log.exception('Download failed')
                self.error_message = str(exc)
                self.state = self.STATE_ERROR
                self._notify()

        self._download_thread = threading.Thread(target=_do_download, daemon=True)
        self._download_thread.start()

    def cancel_download(self):
        self._cancel_flag = True

    def apply_update(self, silent=True):
        if not self.installer_path or not self.installer_path.is_file():
            return False, tr('Файл обновления не найден. Скачайте его заново.')

        target_exe = str(Path(sys.executable).resolve()) if getattr(sys, 'frozen', False) else ''
        script = self.installer_path.parent / 'apply_update.ps1'
        current_pid = os.getpid()

        script_content = f"""# Local Agent AI Station detached updater
param([int]$WaitPid = {current_pid})
$ErrorActionPreference = 'SilentlyContinue'

if ($WaitPid -gt 0) {{
    try {{
        Wait-Process -Id $WaitPid -Timeout 25
    }} catch {{}}
}}
Start-Sleep -Seconds 1

$installer = '{str(self.installer_path)}'
$targetExe = '{target_exe}'
$silentFlag = '{('/SILENT /CLOSEAPPLICATIONS /NORESTART' if silent else '/CLOSEAPPLICATIONS')}'

$p = Start-Process -FilePath $installer -ArgumentList $silentFlag -Wait -PassThru
if ($p.ExitCode -eq 0 -and $targetExe -and (Test-Path $targetExe)) {{
    Start-Process -FilePath $targetExe
}}
Start-Sleep -Seconds 3
Remove-Item -Path $installer -Force -ErrorAction SilentlyContinue
Remove-Item -Path $MyInvocation.MyCommand.Path -Force -ErrorAction SilentlyContinue
"""
        script.write_text(script_content, encoding='utf-8-sig')

        DETACHED_FLAGS = 0x08000000 | 0x00000008
        try:
            subprocess.Popen([
                'powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                '-WindowStyle', 'Hidden', '-File', str(script),
                '-WaitPid', str(current_pid)
            ], creationflags=DETACHED_FLAGS, close_fds=True)
            self.state = self.STATE_INSTALLING
            self._notify()
            return True, tr('Обновление запущено. Приложение перезапускается…')
        except Exception as exc:
            log.exception('Could not spawn update script')
            return False, tr('Не удалось запустить процесс обновления: {error}', error=exc)


update_manager = UpdateManager()
