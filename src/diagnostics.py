"""Log reading and the diagnostics bundle. Secrets never enter the bundle.

The bundle contains a doctor-like report, redacted Station settings, the generated
llama-swap configuration, the process journal and the tails of logs. Keys that look like
credentials are replaced everywhere; .env files, Windows Credential Manager data and agent
chat histories are never read.
"""
import hashlib
import json
import logging
import os
import platform
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import yaml

from .i18n import tr
from .paths import APP_NAME, VERSION, data_dir, logs_dir as paths_logs_dir

VIEW_LIMIT = 2 * 1024 * 1024
BUNDLE_LOG_LIMIT = 5 * 1024 * 1024
REDACTED = '***REDACTED***'

SECRET_KEY = re.compile(r'api[_-]?key|token|secret|password|passwd', re.IGNORECASE)
_RUSSIAN_ERROR = ''.join(map(chr, (0x43E, 0x448, 0x438, 0x431, 0x43A)))  # stem of the Russian word for error
ERROR_LINE = re.compile(r'error|warn|exception|traceback|failed|fatal|critical|' + _RUSSIAN_ERROR, re.IGNORECASE)
# KEY=value / KEY: value where KEY looks like a credential (env blocks, .env-like text, logs).
_ASSIGNMENT = re.compile(
    r'(?P<key>[A-Za-z0-9_.\-]*(?:api[_-]?key|token|secret|password|passwd)[A-Za-z0-9_.\-]*)'
    r'(?P<sep>["\']?\s*[=:]\s*)(?P<value>"[^"]*"|\'[^\']*\'|[^\s,;}\]]+)', re.IGNORECASE)
# Command line flags: --api-key VALUE, --api-key=VALUE, -token VALUE.
_FLAG = re.compile(r'(?P<flag>--?[A-Za-z0-9_\-]*(?:api[_-]?key|token|secret|password)[A-Za-z0-9_\-]*)(?P<sep>[=\s]+)(?P<value>"[^"]*"|\'[^\']*\'|[^\s"\']+)',
                   re.IGNORECASE)
_BEARER = re.compile(r'(?P<prefix>\bBearer\s+)(?P<value>[A-Za-z0-9._~+/=\-]{8,})', re.IGNORECASE)
_EXCLUDED_NAME = re.compile(r'(^\.env)|(\.env$)|secret|credential|histor|conversation|transcript', re.IGNORECASE)
LOG_SUFFIXES = ('.log', '.jsonl', '.json', '.txt')
ROTATED = re.compile(r'\.log\.\d+$', re.IGNORECASE)


# ---------------------------------------------------------------- redaction
def is_secret_key(key):
    return isinstance(key, str) and bool(SECRET_KEY.search(key))


def redact_text(text):
    """Hide credential values in free text, line by line (KEY=value, --api-key value, Bearer ...)."""
    if not isinstance(text, str) or not text:
        return text
    text = _FLAG.sub(lambda m: m.group('flag') + m.group('sep') + REDACTED, text)
    text = _ASSIGNMENT.sub(lambda m: m.group('key') + m.group('sep') + REDACTED, text)
    return _BEARER.sub(lambda m: m.group('prefix') + REDACTED, text)


def redact(value):
    """Recursive copy of a JSON/YAML document with credential values replaced."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if is_secret_key(key) and item not in (None, ''):
                result[key] = REDACTED
            else:
                result[key] = redact(item)
        return result
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_document(content, suffix='.yaml'):
    """Redact YAML or JSON text; unparsable content is redacted line by line."""
    try:
        if suffix.lower() == '.json':
            return json.dumps(redact(json.loads(content)), indent=2, ensure_ascii=False, default=str)
        data = yaml.safe_load(content)
        if isinstance(data, (dict, list)):
            return yaml.safe_dump(redact(data), allow_unicode=True, sort_keys=False)
    except Exception:
        pass
    return redact_text(content)


# ---------------------------------------------------------------- reading logs
def read_tail(path, limit=VIEW_LIMIT):
    """Last `limit` bytes of a file as text, starting at a line boundary when truncated."""
    path = Path(path)
    with path.open('rb') as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        start = max(0, size - limit)
        handle.seek(start)
        data = handle.read(size - start)
    if start > 0:
        newline = data.find(b'\n')
        if 0 <= newline < len(data) - 1:
            data = data[newline + 1:]
    return data.decode('utf-8', errors='replace')


def is_error_line(line):
    return bool(ERROR_LINE.search(line))


def filter_lines(text, errors_only=False, query=''):
    """Lines matching the error filter and a case-insensitive search text."""
    query = (query or '').strip().lower()
    lines = text.splitlines()
    return [line for line in lines
            if (not errors_only or is_error_line(line)) and (not query or query in line.lower())]


def logs_dir():
    return paths_logs_dir()


def _hashed(key):
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def friendly_name(key, frontends=None, agents=None):
    """Readable source name for a supervisor key such as service:llama-swap or frontend:qwen-desktop."""
    frontends = frontends or {}
    agents = agents or {}
    kind, _, ident = key.partition(':')
    if key == 'service:llama-swap':
        return tr('Сервер моделей (llama-swap)')
    if kind == 'frontend':
        return tr('Интерфейс агента: {name}', name=frontends.get(ident, {}).get('name') or ident)
    if kind == 'agent':
        return tr('Агент: {name}', name=agents.get(ident) or ident)
    if kind == 'service':
        return tr('Служба: {name}', name=ident)
    return key


def _file_name(path):
    name = path.name
    lower = name.lower()
    if lower == 'station.log':
        return tr('Station (журнал приложения)')
    match = re.fullmatch(r'station\.log\.(\d+)', lower)
    if match:
        return tr('Station (архив {number})', number=match.group(1))
    if lower == 'startup-last.json':
        return tr('Отчёт последнего автозапуска')
    if lower.startswith('watchdog'):
        return tr('События сторожа сервера (watchdog)')
    if lower.startswith('crash_'):
        return tr('Аварийный вывод: {name}', name=name)
    return name


def _is_log_file(path):
    lower = path.name.lower()
    return path.is_file() and (lower.endswith(LOG_SUFFIXES) or bool(ROTATED.search(lower))) and not _EXCLUDED_NAME.search(path.name)


def log_sources(records=None, frontends=None, agents=None, folder=None):
    """[{'id', 'name', 'path'}] of every log that exists: Station, supervised processes, other files."""
    if records is None:
        try:
            from .supervisor import supervisor
            records = dict(supervisor.records)
        except Exception:
            records = {}
    folder = Path(folder or logs_dir())
    sources, seen = [], set()

    def add(ident, name, path):
        path = Path(path)
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen or not path.is_file():
            return
        seen.add(key)
        sources.append({'id': ident, 'name': name, 'path': str(path)})

    add('station', _file_name(folder / 'station.log'), folder / 'station.log')

    def readable(key):
        return re.sub(r'[^A-Za-z0-9._-]+', '-', key).strip('.-')[:80]

    known = {}
    keys = set(records) | {'service:llama-swap'}
    keys |= {'frontend:' + ident for ident in (frontends or {})} | {'agent:' + ident for ident in (agents or {})}
    for key in sorted(keys):
        name = friendly_name(key, frontends, agents)
        known[readable(key).lower()] = name
        known[_hashed(key)] = name
    for key, record in sorted(records.items()):
        if isinstance(record, dict) and record.get('log'):
            add(key, friendly_name(key, frontends, agents), record['log'])
    if folder.is_dir():
        for path in sorted((p for p in folder.iterdir() if _is_log_file(p)), key=lambda p: p.name.lower()):
            stem = path.name[:-4].lower() if path.name.lower().endswith('.log') else None
            add('file:' + path.name, known.get(stem) or _file_name(path), path)
    return sources


# ---------------------------------------------------------------- report
def _safe(function):
    try:
        return function()
    except Exception as exc:
        return {'error': f'{type(exc).__name__}: {exc}'}


def _model_server_info():
    from . import model_server
    cuda_name, cuda_found = model_server.cuda_runtime_status()
    return {
        'runtime_dir': str(model_server.runtime_dir() or ''),
        'bundled_runtime_dir': str(model_server.bundled_runtime_dir() or ''),
        'models_dir': str(model_server.models_dir() or ''),
        'llama_swap_executable': str(model_server.swap_executable() or ''),
        'llama_server_executable': str(model_server.llama_server_executable() or ''),
        'generated_config': str(model_server.generated_config_path()),
        'cuda': {'required_cublas': cuda_name, 'found': str(cuda_found) if cuda_found else None},
    }


def build_report(agent_status=None, collectors=None):
    """Doctor report equivalent to `main.pyw --doctor` plus backend, folders and OS details.

    agent_status: already discovered agents (discovery is slow); None means "not collected".
    collectors: optional {section: callable} overrides, used by tests.
    """
    def hardware():
        from .hardware_topology import topology_engine
        return topology_engine.discover_live().to_dict()

    def gpu_helper():
        from .services.gpu_mode_client import gpu_service_client
        return gpu_service_client.get_driver_modes()

    def backend():
        from .process_manager import pm
        return pm.backend_info()

    sections = {'hardware': hardware, 'gpu_helper': gpu_helper, 'backend': backend, 'model_server': _model_server_info}
    sections.update(collectors or {})
    report = {
        'application': APP_NAME, 'version': VERSION, 'generated': datetime.now().isoformat(timespec='seconds'),
        'data_directory': str(data_dir()),
        'os': {'platform': platform.platform(), 'release': platform.release(), 'version': platform.version(),
               'machine': platform.machine(), 'python': sys.version.split()[0], 'frozen': bool(getattr(sys, 'frozen', False))},
        'agents': agent_status if agent_status is not None else {'note': 'agent discovery was not finished when the bundle was collected'},
    }
    for name, function in sections.items():
        report[name] = _safe(function)
    return redact(report)


# ---------------------------------------------------------------- bundle
README = """Local Agent AI Station diagnostics bundle
=========================================

Created: {created}
Station version: {version}

Contents
--------
README.txt                 This file.
report.json                Doctor report: OS, hardware topology, GPU helper modes, model server
                           (address, owner, PID, generated config), engine/models folders,
                           CUDA runtime status, agent status.
config/*.yaml              Station settings (config folder, backups excluded).
backend/llama-swap.yaml    Model server configuration generated by Station.
processes.json             Journal of processes started by Station (PID, executable, log path).
logs/                      Last part of each log (at most {limit_mb} MB per file): Station,
                           model server, agents and frontends.
reports/                   Startup and watchdog reports, if present.

Privacy
-------
Values of keys containing apikey/api_key/token/secret/password are replaced with
{redacted} in settings, reports and logs (including KEY=value lines and --api-key flags).
Not included: .env files, Windows Credential Manager data, agent chat histories, model files.
Review the files before sharing the bundle.
{skipped}"""


def _limited_text(path, limit):
    text = redact_text(read_tail(path, limit))
    data = text.encode('utf-8')
    if len(data) > limit:
        text = data[len(data) - limit:].decode('utf-8', errors='ignore')
    return text


def collect_diagnostics(target_dir=None, report=None, agent_status=None, records=None, frontends=None,
                        log_limit=BUNDLE_LOG_LIMIT, now=None):
    """Write data_dir()/diagnostics/laas-diagnostics-<timestamp>.zip and return its path."""
    root = data_dir()
    now = now or datetime.now()
    target_dir = Path(target_dir or root / 'diagnostics')
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f'laas-diagnostics-{now:%Y%m%d-%H%M%S}.zip'
    partial = target.with_suffix('.zip.partial')
    if records is None:
        try:
            from .supervisor import supervisor
            records = dict(supervisor.records)
        except Exception:
            records = {}
    report = report if report is not None else build_report(agent_status)
    skipped = []
    with zipfile.ZipFile(partial, 'w', compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr('report.json', json.dumps(redact(report), indent=2, ensure_ascii=False, default=str))
        config_dir = root / 'config'
        if config_dir.is_dir():
            for path in sorted(config_dir.iterdir()):
                if not path.is_file() or _EXCLUDED_NAME.search(path.name):
                    continue
                if path.suffix.lower() not in ('.yaml', '.yml', '.json'):
                    continue
                try:
                    bundle.writestr('config/' + path.name, redact_document(path.read_text(encoding='utf-8', errors='replace'), path.suffix))
                except OSError as exc:
                    skipped.append(f'config/{path.name}: {exc}')
        generated = root / 'backend' / 'llama-swap.yaml'
        if generated.is_file():
            bundle.writestr('backend/llama-swap.yaml', redact_document(generated.read_text(encoding='utf-8', errors='replace'), '.yaml'))
        journal = root / 'processes.json'
        if journal.is_file():
            bundle.writestr('processes.json', redact_document(journal.read_text(encoding='utf-8', errors='replace'), '.json'))
        names = set()
        for source in log_sources(records=records, frontends=frontends, folder=root / 'logs'):
            path = Path(source['path'])
            if _EXCLUDED_NAME.search(path.name):
                continue
            is_report = path.name.lower() == 'startup-last.json' or path.name.lower().startswith('watchdog')
            folder = 'reports/' if is_report else 'logs/'
            name = path.name
            while folder + name in names:
                name = '_' + name
            names.add(folder + name)
            try:
                if is_report and path.suffix.lower() == '.json' and path.stat().st_size <= log_limit:
                    content = redact_document(path.read_text(encoding='utf-8', errors='replace'), '.json')
                else:
                    content = _limited_text(path, log_limit)
                bundle.writestr(folder + name, content)
            except OSError as exc:
                skipped.append(f'{path}: {exc}')
        note = ('\nSkipped\n-------\n' + '\n'.join(skipped) + '\n') if skipped else ''
        bundle.writestr('README.txt', README.format(created=now.isoformat(timespec='seconds'), version=VERSION,
                                                    limit_mb=round(log_limit / 1024 / 1024, 2), redacted=REDACTED, skipped=note))
    os.replace(partial, target)
    logging.getLogger(__name__).info('Diagnostics bundle written: %s', target)
    return target
