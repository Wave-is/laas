"""Validated atomic persistence with optimistic concurrency and rollback copies."""
import hashlib
import json
import os
import tempfile
from pathlib import Path
from datetime import datetime, timezone
import yaml
from .i18n import tr

class ConfigurationError(ValueError):
    pass

class ConfigurationConflict(ConfigurationError):
    pass

def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationError(tr('Повторяющийся ключ конфигурации: {key}', key=key))
        result[key] = value
    return result

class UniqueLoader(yaml.SafeLoader):
    pass

def yaml_mapping(loader, node):
    loader.flatten_mapping(node)
    return unique_pairs((loader.construct_object(k), loader.construct_object(v)) for k, v in node.value)

UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)

def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ''

def read_document(path: Path, default=None, *, allow_json5=False):
    if not path.exists():
        return default
    try:
        content = path.read_text(encoding='utf-8-sig')
        if allow_json5:
            import json5
            return json5.loads(content, allow_duplicate_keys=False)
        return json.loads(content, object_pairs_hook=unique_pairs) if path.suffix == '.json' else yaml.load(content, Loader=UniqueLoader)
    except (ValueError, yaml.YAMLError, OSError) as exc:
        raise ConfigurationError(tr('Не удалось прочитать {path}: {error}', path=path, error=exc)) from exc

def encode_document(path: Path, data) -> str:
    if path.suffix == '.json':
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False)

def atomic_write(path: Path, data, *, expected_digest=None, backup=True) -> Path | None:
    path = Path(path)
    content = encode_document(path, data)
    if expected_digest is not None and digest(path) != expected_digest:
        raise ConfigurationConflict(tr('Файл изменён извне: {path}. Перезагрузите его и проверьте изменения.', path=path))
    if path.exists() and path.read_text(encoding='utf-8') == content:
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    saved = None
    if backup and path.exists():
        backup_dir = path.parent / 'backups'
        backup_dir.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        saved = backup_dir / f'{path.name}.{stamp}.bak'
        saved.write_bytes(path.read_bytes())
    fd, tmp = tempfile.mkstemp(prefix=f'.{path.name}.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        if expected_digest is not None and digest(path) != expected_digest:
            raise ConfigurationConflict(tr('Конфигурация изменилась во время сохранения: {path}', path=path))
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return saved
