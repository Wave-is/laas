"""Preview, detect manual changes, atomically merge and optionally smoke/rollback."""
from copy import deepcopy
from dataclasses import dataclass
import difflib
import hashlib
from pathlib import Path
from .paths import data_dir
from .storage import read_document, encode_document, atomic_write, digest, ConfigurationConflict
from .i18n import tr

@dataclass
class SyncPreview:
    path: Path
    before: dict
    after: dict
    expected: str
    diff: str
    status: str
    blocks: list
    allow_json5: bool = False


@dataclass
class SyncTransaction:
    previews: list

    @property
    def path(self):
        # The provider catalog is the configuration entry point for smoke checks.
        return self.previews[0].path

    @property
    def status(self):
        states = {p.status for p in self.previews}
        return 'CUSTOM MODIFIED' if 'CUSTOM MODIFIED' in states else 'OUT OF SYNC' if 'OUT OF SYNC' in states else 'IN SYNC'

    @property
    def diff(self):
        return '\n\n'.join(str(p.path) + '\n' + p.diff for p in self.previews)


def combine_previews(previews):
    grouped = {}
    for preview in previews:
        key = preview.path.resolve()
        grouped.setdefault(key, []).append(preview)
    merged = []
    for group in grouped.values():
        changes = [(key, block(p.after, key)) for p in group for key in p.blocks]
        merged.append(preview_merge(group[0].path, changes, allow_json5=group[0].allow_json5))
    return merged[0] if len(merged) == 1 else SyncTransaction(merged)

def block(document, path):
    current = document
    for part in path:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current

def redacted(value):
    if isinstance(value, dict):
        return {k: ('<redacted>' if any(s in k.lower().replace('_', '') for s in ('apikey', 'password', 'token', 'secret')) else redacted(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redacted(v) for v in value]
    return value

def preview_merge(path, changes, *, allow_json5=False):
    path = Path(path)
    before = read_document(path, {}, allow_json5=allow_json5)
    if not isinstance(before, dict):
        raise ValueError(tr('Файл настроек агента повреждён или имеет неожиданный формат: {path}. Исправьте его или удалите.', path=path))
    after = deepcopy(before)
    state_path = data_dir() / 'sync' / (hashlib.sha256(str(path.resolve()).encode()).hexdigest() + '.json')
    state = read_document(state_path, {})
    custom = False
    for key, value in changes:
        previous = state.get('/'.join(key))
        current = block(before, key)
        if (previous is not None and current != previous) or (previous is None and current is not None and current != value):
            custom = True
        target = after
        for part in key[:-1]:
            if part in target and not isinstance(target[part], dict):
                raise ValueError(tr('В файле настроек агента {path} поле «{part}» имеет неожиданный формат. Исправьте его вручную.', path=path, part=part))
            target = target.setdefault(part, {})
        target[key[-1]] = deepcopy(value)
    # Display only affected blocks; unrelated credentials never enter the diff.
    safe_before = {'/'.join(key): block(before, key) for key, _ in changes}
    safe_after = {'/'.join(key): value for key, value in changes}
    diff = ''.join(difflib.unified_diff(encode_document(path, redacted(safe_before)).splitlines(True),
        encode_document(path, redacted(safe_after)).splitlines(True), fromfile='current managed fields', tofile='proposed managed fields'))
    status = 'IN SYNC' if before == after else 'CUSTOM MODIFIED' if custom else 'OUT OF SYNC'
    return SyncPreview(path, before, after, digest(path), diff, status, [key for key, _ in changes], allow_json5)

def apply_preview(preview, *, accept_custom=False, smoke=None):
    if isinstance(preview, SyncTransaction):
        return apply_transaction(preview, accept_custom=accept_custom, smoke=smoke)
    if preview.status == 'CUSTOM MODIFIED' and not accept_custom:
        raise ConfigurationConflict(tr('Файл настроек агента {path} был изменён вручную после последней синхронизации. '
            'Просмотрите изменения и подтвердите, что их можно перезаписать.', path=preview.path))
    saved = atomic_write(preview.path, preview.after, expected_digest=preview.expected)
    applied_digest = digest(preview.path)
    try:
        if smoke is not None and not smoke():
            raise RuntimeError(tr('Проверка агента через модель не пройдена — изменения настроек отменены, файл восстановлен. '
                'Убедитесь, что модель запущена, и повторите синхронизацию.'))
    except Exception:
        if digest(preview.path) != applied_digest:
            raise ConfigurationConflict(tr('Проверка агента не пройдена, а файл настроек {path} за это время изменил кто-то другой. '
                'Эти изменения сохранены; при необходимости восстановите резервную копию вручную.', path=preview.path))
        if not preview.expected:
            preview.path.unlink()
        else:
            atomic_write(preview.path, preview.before, expected_digest=applied_digest)
        raise
    state_path = data_dir() / 'sync' / (hashlib.sha256(str(preview.path.resolve()).encode()).hexdigest() + '.json')
    state = read_document(state_path, {})
    expected = digest(state_path)
    state.update({'/'.join(key): block(preview.after, key) for key in preview.blocks})
    atomic_write(state_path, state, expected_digest=expected)
    return {'status': 'IN SYNC', 'backup': str(saved) if saved else None}


def apply_transaction(transaction, *, accept_custom=False, smoke=None):
    """Save all files before checking the runtime; roll back our writes on failure."""
    previews = transaction.previews
    for p in previews:
        if p.status == 'CUSTOM MODIFIED' and not accept_custom:
            raise ConfigurationConflict(tr('Файл настроек агента {path} был изменён вручную после последней синхронизации. '
                'Просмотрите изменения и подтвердите, что их можно перезаписать.', path=p.path))
        if digest(p.path) != p.expected:
            raise ConfigurationConflict(tr('Файл настроек агента изменился после просмотра: {path}. Откройте синхронизацию заново.', path=p.path))
    applied = []
    backups = []
    try:
        for p in previews:
            saved = atomic_write(p.path, p.after, expected_digest=p.expected)
            applied.append((p, digest(p.path)))
            backups.append(str(saved) if saved else None)
        if smoke is not None and not smoke():
            raise RuntimeError(tr('Проверка агента через модель не пройдена — изменения настроек отменены, файл восстановлен. '
                'Убедитесь, что модель запущена, и повторите синхронизацию.'))
    except Exception as exc:
        conflicts = []
        for p, written in reversed(applied):
            if digest(p.path) != written:
                conflicts.append(str(p.path))
            elif not p.expected:
                p.path.unlink()
            else:
                atomic_write(p.path, p.before, expected_digest=written)
        if conflicts:
            raise ConfigurationConflict(tr('Синхронизация не удалась, а эти файлы настроек за это время изменил кто-то другой (их изменения сохранены): '
                '{files}. При необходимости восстановите резервные копии вручную.', files=', '.join(conflicts))) from exc
        raise
    for p in previews:
        state_path = data_dir() / 'sync' / (hashlib.sha256(str(p.path.resolve()).encode()).hexdigest() + '.json')
        state = read_document(state_path, {})
        expected = digest(state_path)
        state.update({'/'.join(key): block(p.after, key) for key in p.blocks})
        atomic_write(state_path, state, expected_digest=expected)
    return {'status': 'IN SYNC', 'backups': backups}
