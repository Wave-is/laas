"""Backup and transfer of Station settings, model profiles and (optionally) agent configurations.

Export writes a zip with ``manifest.json``. Agent configurations are stored with secrets
replaced by a marker; the manifest lists every redacted field. Import is two-step:
:func:`preview_import` describes what will change, :func:`apply_import` first copies the
current files to ``data_dir()/backups/pre-import-<ts>/`` and then writes with ``atomic_write``.
Redacted fields are never written back: the value already on this computer is kept.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import zipfile
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import yaml

from .i18n import tr
from .paths import data_dir
from .storage import atomic_write, encode_document, unique_pairs, UniqueLoader
from .version import VERSION

FORMAT = 1
REDACTED = '<redacted by LAAS>'
AGENT_IDS = ('qwen-code', 'hermes')
SECRET_KEY = re.compile(r'(api[_-]?key|apikey|secret|(access|auth|refresh|bearer|api|session)[_-]?token|^token$|password|passwd|credential|private[_-]?key|authorization|bearer|cookie)', re.I)
SECRET_VALUE = re.compile(r'^(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9-]{10,})$')
STATION_NAME = re.compile(r'^config/[A-Za-z0-9_.-]+\.yaml$')
AGENT_NAME = re.compile(r'^agents/([a-z0-9-]+)/([A-Za-z0-9_.-]+\.(?:json|yaml|yml))$')
LOCAL_KEYS = ('runtime_dir', 'models_dir', 'llama_swap_executable', 'llama_server_executable', 'workspace', 'last_agent_folder')


def backups_dir() -> Path:
    return data_dir() / 'backups'


def _stamp(now=None):
    return (now or datetime.now()).strftime('%Y%m%d-%H%M%S')


def redact(data, prefix=''):
    """(copy with secrets replaced, list of dotted paths that were redacted)."""
    found = []

    def walk(value, path, key=''):
        if isinstance(value, dict):
            return {k: walk(v, f'{path}.{k}' if path else str(k), str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [walk(v, f'{path}[{i}]', key) for i, v in enumerate(value)]
        if isinstance(value, str) and value and value != REDACTED and (
                (SECRET_KEY.search(key) and not key.lower().endswith(('_env', 'env'))) or SECRET_VALUE.match(value.strip())):
            found.append(path)
            return REDACTED
        return value
    return walk(deepcopy(data), prefix), found


def parse_bytes(name, content):
    text = content.decode('utf-8-sig')
    if name.endswith('.json'):
        return json.loads(text, object_pairs_hook=unique_pairs)
    return yaml.load(text, Loader=UniqueLoader)


def station_files(root=None):
    folder = Path(root or data_dir()) / 'config'
    return sorted(folder.glob('*.yaml')) if folder.is_dir() else []


def agent_files(adapters):
    """(agent id, file) for supported agents whose user configuration exists."""
    result = []
    for id in AGENT_IDS:
        adapter = (adapters or {}).get(id)
        if not adapter:
            continue
        try:
            path = Path(adapter.get_config_locations().data['user'])
        except Exception:
            continue
        if path.is_file():
            result.append((id, path))
    return result


def export_backup(target=None, include_agents=False, adapters=None, now=None, machine=None):
    now = now or datetime.now()
    target = Path(target or backups_dir() / f'laas-backup-{_stamp(now)}.zip')
    target.parent.mkdir(parents=True, exist_ok=True)
    from .config import config
    manifest = {'format': FORMAT, 'app': 'LAAS', 'app_version': VERSION, 'machine': machine or platform.node(),
                'created': now.isoformat(timespec='seconds'), 'data_dir': str(data_dir()),
                'models_dir': config.get('models_dir', ''), 'runtime_dir': config.get('runtime_dir', ''), 'files': []}
    warnings = []
    part = target.with_name(target.name + '.part')
    with zipfile.ZipFile(part, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in station_files():
            content = path.read_bytes()
            name = 'config/' + path.name
            archive.writestr(name, content)
            manifest['files'].append({'name': name, 'kind': 'station', 'source': str(path), 'redacted': [],
                                      'sha256': hashlib.sha256(content).hexdigest()})
        if include_agents:
            for id, path in agent_files(adapters):
                name = f'agents/{id}/{path.name}'
                try:
                    data = parse_bytes(path.name, path.read_bytes())
                except Exception as exc:
                    warnings.append(tr('{path} пропущен: {error}', path=path, error=exc))
                    continue
                clean, fields = redact(data)
                content = encode_document(path, clean).encode('utf-8')
                archive.writestr(name, content)
                manifest['files'].append({'name': name, 'kind': 'agent', 'agent': id, 'source': str(path), 'redacted': fields,
                                          'sha256': hashlib.sha256(content).hexdigest()})
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
    os.replace(part, target)
    redacted = sum(len(f['redacted']) for f in manifest['files'])
    message = tr('Резервная копия создана: {path}. Файлов: {count}.', path=target, count=len(manifest['files']))
    if redacted:
        message += ' ' + tr('Секретов скрыто: {count} (при восстановлении сохраняются текущие значения).', count=redacted)
    return {'Success': True, 'Message': message, 'Path': str(target), 'Manifest': manifest, 'Warnings': warnings}


def list_backups(limit=10):
    folder = backups_dir()
    if not folder.is_dir():
        return []
    rows = [p for p in folder.iterdir() if (p.is_file() and p.suffix == '.zip') or (p.is_dir() and p.name.startswith('pre-import-'))]
    return sorted(rows, key=lambda p: p.stat().st_mtime, reverse=True)[:limit]


def read_backup(path):
    with zipfile.ZipFile(path) as archive:
        try:
            manifest = json.loads(archive.read('manifest.json').decode('utf-8'))
        except KeyError as exc:
            raise ValueError(tr('Это не резервная копия Station: нет manifest.json')) from exc
        if manifest.get('format') != FORMAT or not isinstance(manifest.get('files'), list):
            raise ValueError(tr('Неподдерживаемый формат резервной копии'))
        contents = {}
        for row in manifest['files']:
            name = row.get('name', '')
            if not (STATION_NAME.match(name) or AGENT_NAME.match(name)):
                raise ValueError(tr('Недопустимый путь в архиве: {name}', name=name))
            content = archive.read(name)
            if row.get('sha256') and hashlib.sha256(content).hexdigest() != row['sha256']:
                raise ValueError(tr('Файл {name} в копии повреждён', name=name))
            contents[name] = content
    return manifest, contents


def restore_redacted(incoming, current, fields):
    """Put current values into redacted fields; drop fields this computer has no value for."""
    result = deepcopy(incoming)
    missing = []
    for field in fields:
        tokens = [int(t) if t.isdigit() else t for t in re.findall(r'[^.\[\]]+', field)]
        value = _get(current, tokens)
        if value is not _MISSING:
            _set(result, tokens, value)
        else:
            _delete(result, tokens)
            missing.append(field)
    return result, missing


_MISSING = object()


def _get(data, tokens):
    for token in tokens:
        try:
            data = data[token]
        except (KeyError, IndexError, TypeError):
            return _MISSING
    return data


def _set(data, tokens, value):
    parent = _get(data, tokens[:-1])
    if parent is not _MISSING:
        try:
            parent[tokens[-1]] = value
        except (IndexError, TypeError):
            pass


def _delete(data, tokens):
    parent = _get(data, tokens[:-1])
    if isinstance(parent, dict):
        parent.pop(tokens[-1], None)
    elif isinstance(parent, list) and isinstance(tokens[-1], int) and tokens[-1] < len(parent) and parent[tokens[-1]] == REDACTED:
        parent[tokens[-1]] = ''


def preview_import(path, adapters=None, keep_local_paths=True):
    """What importing ``path`` would change on this computer. Nothing is written."""
    manifest, contents = read_backup(path)
    from .config import config
    items, warnings = [], []
    for row in manifest['files']:
        name = row['name']
        agent = AGENT_NAME.match(name)
        if agent:
            adapter = (adapters or {}).get(agent.group(1))
            if not adapter:
                items.append({'name': name, 'target': None, 'action': 'skip', 'reason': tr('агент не поддерживается на этом компьютере')})
                continue
            try:
                target = Path(adapter.get_config_locations().data['user'])
            except Exception as exc:
                items.append({'name': name, 'target': None, 'action': 'skip', 'reason': str(exc)})
                continue
        else:
            target = data_dir() / name
        try:
            data = parse_bytes(name, contents[name])
            current = parse_bytes(target.name, target.read_bytes()) if target.is_file() else None
        except Exception as exc:
            items.append({'name': name, 'target': target, 'action': 'skip', 'reason': tr('не читается: {error}', error=exc)})
            continue
        notes = []
        if row.get('redacted'):
            data, missing = restore_redacted(data, current or {}, row['redacted'])
            notes.append(tr('скрытые секреты: {count}, сохраняются текущие значения', count=len(row['redacted'])))
            if missing:
                notes.append(tr('на этом ПК нет значений для: {fields} — укажите их заново', fields=', '.join(missing)))
        if name == 'config/station.yaml' and keep_local_paths and isinstance(data, dict) and isinstance(current, dict):
            for key in LOCAL_KEYS:
                if key in current:
                    data[key] = current[key]
            notes.append(tr('папки этого ПК (движок, модели) сохраняются'))
        action = 'create' if current is None else 'same' if encode_document(target, data) == encode_document(target, current) else 'update'
        items.append({'name': name, 'target': target, 'action': action, 'data': data, 'reason': '; '.join(notes)})
    old_models = manifest.get('models_dir') or ''
    new_models = config.get('models_dir', '') or ''
    hint = None
    if old_models and os.path.normcase(old_models.rstrip('\\/')) != os.path.normcase(new_models.rstrip('\\/')):
        hint = tr('Папка моделей в копии: {old}, на этом ПК: {new}. Профили с полными путями к файлам укажут на старую папку — '
                  'после восстановления проверьте раздел «Модели» или перенесите файлы.', old=old_models, new=new_models or tr('не задана'))
    return {'path': str(path), 'manifest': manifest, 'items': items, 'models_hint': hint, 'warnings': warnings}


def describe_preview(preview):
    actions = {'create': tr('будет создан'), 'update': tr('будет изменён'), 'same': tr('без изменений'), 'skip': tr('пропущен')}
    manifest = preview['manifest']
    lines = [tr('Копия от {date}, компьютер {machine}, Station {version}', date=manifest.get('created', '?'),
                machine=manifest.get('machine', '?'), version=manifest.get('app_version', '?')), '']
    for item in preview['items']:
        lines.append(f"{actions.get(item['action'], item['action'])}: {item['target'] or item['name']}")
        if item.get('reason'):
            lines.append('    ' + item['reason'])
    if preview.get('models_hint'):
        lines += ['', preview['models_hint']]
    lines += ['', tr('Перед записью текущие файлы копируются в {path}. После восстановления перезапустите Station.',
                     path=backups_dir() / 'pre-import-…')]
    return '\n'.join(lines)


def apply_import(preview, now=None):
    changes = [item for item in preview['items'] if item['action'] in ('create', 'update')]
    if not changes:
        return {'Success': True, 'Message': tr('Восстанавливать нечего: файлы совпадают с копией.'), 'Backup': None}
    safety = backups_dir() / f'pre-import-{_stamp(now)}'
    safety.mkdir(parents=True, exist_ok=False)
    saved = []
    for item in changes:
        target = Path(item['target'])
        if target.is_file():
            copy = safety / item['name']
            copy.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, copy)
            saved.append({'name': item['name'], 'target': str(target)})
    (safety / 'restore.json').write_text(json.dumps({'created': (now or datetime.now()).isoformat(timespec='seconds'),
        'source': preview['path'], 'files': saved}, ensure_ascii=False, indent=2), encoding='utf-8')
    written = []
    for item in changes:
        atomic_write(Path(item['target']), item['data'])
        written.append(str(item['target']))
    return {'Success': True, 'Backup': str(safety), 'Written': written,
            'Message': tr('Восстановлено файлов: {count}. Прежние файлы сохранены в {path}. Перезапустите Station, чтобы применить настройки.',
                          count=len(written), path=safety)}
