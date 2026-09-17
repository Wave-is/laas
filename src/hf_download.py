"""Hugging Face downloads: list .gguf files of a repository and download one with resume and SHA256.

Requests run only when the user asks for them. The optional access token is read from Windows
Credential Manager (reference ``huggingface/token``) and is never written to settings or YAML.
The token is sent to huggingface.co only; it is dropped when a download redirects to a CDN host.
"""
import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .i18n import tr

HF_BASE = 'https://huggingface.co'
TOKEN_REFERENCE = 'huggingface/token'
CHUNK = 1024 * 1024
REPO_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,95}/[A-Za-z0-9][A-Za-z0-9._-]{0,95}')
USER_AGENT = 'LocalAgentAIStation'


class Cancelled(Exception):
    pass


@dataclass
class RepoFile:
    name: str
    size: Optional[int]
    sha256: Optional[str]


def normalize_repo(text):
    """Accept 'owner/name' or a huggingface.co URL; return 'owner/name' or raise ValueError."""
    value = (text or '').strip()
    match = re.match(r'^https?://(?:www\.)?huggingface\.co/([^/?#]+/[^/?#]+)', value)
    if match:
        value = match.group(1)
    if not REPO_PATTERN.fullmatch(value) or '..' in value:
        raise ValueError(tr('Укажите репозиторий в виде владелец/название, например unsloth/Qwen3-8B-GGUF'))
    return value


def load_token():
    try:
        from .secrets_store import secret_store
        return secret_store.get(TOKEN_REFERENCE) or None
    except Exception:
        return None


def save_token(token):
    from .secrets_store import secret_store
    if token:
        secret_store.put(TOKEN_REFERENCE, token)
    else:
        secret_store.delete(TOKEN_REFERENCE)


class _StripAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).hostname != urllib.parse.urlsplit(req.full_url).hostname:
            for name in list(new.headers):
                if name.lower() == 'authorization':
                    del new.headers[name]
            new.unredirected_hdrs.pop('Authorization', None)
        return new


def default_opener():
    return urllib.request.build_opener(_StripAuthOnRedirect()).open


def _headers(token=None, extra=None):
    headers = {'User-Agent': USER_AGENT}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    headers.update(extra or {})
    return headers


def list_gguf_files(repo, token=None, opener=None, timeout=20) -> List[RepoFile]:
    repo = normalize_repo(repo)
    opener = opener or default_opener()
    url = f'{HF_BASE}/api/models/{repo}?blobs=true'
    try:
        with opener(urllib.request.Request(url, headers=_headers(token)), timeout=timeout) as response:
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PermissionError(tr('Нет доступа к репозиторию {repo}: нужен токен Hugging Face или принятие условий на сайте.', repo=repo))
        if exc.code == 404:
            raise FileNotFoundError(tr('Репозиторий не найден: {repo}', repo=repo))
        raise
    files = []
    for sibling in data.get('siblings') or []:
        name = sibling.get('rfilename') or ''
        if not name.lower().endswith('.gguf'):
            continue
        lfs = sibling.get('lfs') or {}
        size = sibling.get('size') if sibling.get('size') is not None else lfs.get('size')
        sha = lfs.get('sha256') or lfs.get('oid')
        files.append(RepoFile(name, int(size) if size is not None else None,
                              sha.lower() if isinstance(sha, str) and re.fullmatch(r'[0-9a-fA-F]{64}', sha) else None))
    return sorted(files, key=lambda f: f.name.lower())


def file_url(repo, filename, revision='main'):
    repo = normalize_repo(repo)
    return f'{HF_BASE}/{repo}/resolve/{urllib.parse.quote(revision, safe="")}/{urllib.parse.quote(filename)}'


def safe_target(folder, filename):
    """Destination inside folder; repository sub-folders are kept, escaping paths are refused."""
    folder = Path(folder).resolve()
    parts = [p for p in filename.replace('\\', '/').split('/') if p]
    if not parts or any(p in ('.', '..') or ':' in p for p in parts):
        raise ValueError(tr('Недопустимое имя файла: {name}', name=filename))
    target = folder.joinpath(*parts).resolve()
    if folder not in target.parents:
        raise ValueError(tr('Недопустимое имя файла: {name}', name=filename))
    return target


@dataclass
class Progress:
    done: int
    total: Optional[int]
    speed: float          # bytes per second
    eta: Optional[float]  # seconds
    stage: str = 'download'


def _file_sha256(path, cancel=None, progress=None, total=None):
    digest = hashlib.sha256()
    done = 0
    last = 0.0
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(8 * CHUNK), b''):
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            digest.update(chunk)
            done += len(chunk)
            now = time.monotonic()
            if progress and now - last > 0.25:
                last = now
                progress(Progress(done, total, 0.0, None, 'verify'))
    return digest.hexdigest()


def download(url, target, *, size=None, sha256=None, token=None, progress=None, cancel=None, opener=None, timeout=60):
    """Download url into target via target + '.part', resuming an existing part with HTTP Range.

    Returns the final Path. Raises Cancelled (the .part file is kept for a later resume).
    """
    target = Path(target)
    part = target.with_name(target.name + '.part')
    if target.exists():
        if size is None or target.stat().st_size == size:
            raise FileExistsError(tr('Файл уже существует: {path}', path=target))
        raise FileExistsError(tr('Файл уже существует и отличается размером: {path}', path=target))
    target.parent.mkdir(parents=True, exist_ok=True)
    opener = opener or default_opener()
    existing = part.stat().st_size if part.exists() else 0
    if size is not None and existing > size:
        part.unlink()
        existing = 0
    if size is None or existing < size:
        headers = {'Range': f'bytes={existing}-'} if existing else {}
        request = urllib.request.Request(url, headers=_headers(token, headers))
        try:
            response = opener(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code == 416 and existing and (size is None or existing == size):
                response = None
            elif exc.code in (401, 403):
                raise PermissionError(tr('Нет доступа к файлу: нужен токен Hugging Face или принятие условий на сайте.'))
            else:
                raise
        if response is not None:
            with response:
                status = getattr(response, 'status', None) or response.getcode()
                if existing and status != 206:
                    existing = 0  # The server ignored Range: start over.
                total = size
                if total is None:
                    length = response.headers.get('Content-Length')
                    total = existing + int(length) if length and length.isdigit() else None
                started, last = time.monotonic(), 0.0
                received = 0
                with open(part, 'ab' if existing else 'wb') as handle:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        handle.write(chunk)
                        received += len(chunk)
                        now = time.monotonic()
                        if progress and (now - last > 0.25):
                            last = now
                            speed = received / max(now - started, 1e-6)
                            done = existing + received
                            eta = (total - done) / speed if total and speed > 0 else None
                            progress(Progress(done, total, speed, eta))
    final_size = part.stat().st_size
    if size is not None and final_size != size:
        raise IOError(tr('Загрузка прервана: получено {done} из {total} байт. Повторите, чтобы продолжить.', done=final_size, total=size))
    if sha256:
        actual = _file_sha256(part, cancel, progress, final_size)
        if actual.lower() != sha256.lower():
            part.unlink()
            raise IOError(tr('Контрольная сумма SHA256 не совпадает; файл удалён, скачайте его заново.'))
    os.replace(part, target)
    if progress:
        progress(Progress(final_size, final_size, 0.0, 0.0, 'done'))
    return target


def format_bytes(value):
    if value is None:
        return '?'
    for unit, scale in (('GiB', 1024 ** 3), ('MiB', 1024 ** 2), ('KiB', 1024)):
        if value >= scale:
            return f'{value / scale:.1f} {unit}'
    return f'{value} B'


def format_eta(seconds):
    if seconds is None:
        return '—'
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    return f'{hours}:{minutes:02d}:{seconds:02d}' if hours else f'{minutes}:{seconds:02d}'
