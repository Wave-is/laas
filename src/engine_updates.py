"""Engine updates: installed llama.cpp / llama-swap versions, GitHub releases, versioned installs, rollback.

Nothing here runs on import and nothing touches the network unless a function is called
explicitly (Maintenance page button or the opt-in setting ``engine_update_check``).
Installed builds live in ``data_dir()/engines/<component>-<version>/``; the engine Station
uses is a combined folder ``data_dir()/engines/active-<timestamp>/{llama.cpp,llama-swap}``
selected through the ``runtime_dir`` setting. A running model server is never restarted here.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from .i18n import tr
from .paths import data_dir
from .version import VERSION

LLAMA = 'llama.cpp'
SWAP = 'llama-swap'
REPOS = {LLAMA: 'ggml-org/llama.cpp', SWAP: 'mostlygeek/llama-swap'}
RELEASES_PAGE = {LLAMA: 'https://github.com/ggml-org/llama.cpp/releases', SWAP: 'https://github.com/mostlygeek/llama-swap/releases'}
KEEP_VERSIONS = 3
ACTIVE_MANIFEST = 'laas-engine.json'
CUDA_DLL = re.compile(r'^(cublas|cublasLt|cudart)64_\d+\.dll$', re.I)
EXPLICIT_KEYS = ('llama_swap_executable', 'llama_server_executable')


def engines_dir() -> Path:
    return data_dir() / 'engines'


def exe_name(component):
    base = 'llama-server' if component == LLAMA else 'llama-swap'
    return base + ('.exe' if os.name == 'nt' else '')


# ---------------------------------------------------------------- installed versions

def parse_llama_version(text):
    """``version: 0.4.0-dev (build 10869, commit 30b6a755e)`` or ``version: 6000 (abc1234)``."""
    text = text or ''
    match = re.search(r'version:\s*(\S+)\s*\(build\s+(\d+),\s*commit\s+([0-9a-f]+)\)', text, re.I)
    if match:
        return {'build': int(match.group(2)), 'commit': match.group(3), 'label': 'b' + match.group(2)}
    match = re.search(r'version:\s*(\d+)\s*\(([0-9a-f]+)\)', text, re.I)
    if match:
        return {'build': int(match.group(1)), 'commit': match.group(2), 'label': 'b' + match.group(1)}
    return None


def parse_swap_version(text):
    """``version: v255 (7761aa1), built at ...``."""
    match = re.search(r'version:\s*v?(\d+)\s*(?:\(([0-9a-f]+)\))?', text or '', re.I)
    if not match:
        return None
    return {'build': int(match.group(1)), 'commit': match.group(2), 'label': 'v' + match.group(1)}


def run_version(exe, component, timeout=20):
    """Output of ``llama-server --version`` / ``llama-swap -version`` (never starts a server)."""
    from .hardware import hidden_options
    args = [str(exe), '--version' if component == LLAMA else '-version']
    done = subprocess.run(args, capture_output=True, text=True, timeout=timeout, encoding='utf-8', errors='replace',
                          stdin=subprocess.DEVNULL, cwd=str(Path(exe).parent), **hidden_options())
    return (done.stdout or '') + '\n' + (done.stderr or '')


def parse_version(component, text):
    return parse_llama_version(text) if component == LLAMA else parse_swap_version(text)


def cuda_major(dll_name):
    match = re.search(r'cublas64_(\d+)\.dll', dll_name or '', re.I)
    return int(match.group(1)) if match else None


def engine_source(runtime, bundled, engines=None):
    """'bundled', 'managed' (installed by Station), 'custom' or 'missing'."""
    engines = engines or engines_dir()
    if not runtime:
        return 'missing'
    if bundled and _same(runtime, bundled):
        return 'bundled'
    if _inside(runtime, engines):
        return 'managed'
    return 'custom'


def installed(runner=run_version):
    """Current versions of both components and where the engine comes from."""
    from . import model_server
    result = {'runtime_dir': model_server.runtime_dir(), 'bundled': model_server.bundled_runtime_dir()}
    result['source'] = engine_source(result['runtime_dir'], result['bundled'])
    name, found = model_server.cuda_runtime_status()
    result['cuda_dll'], result['cuda_path'], result['cuda_major'] = name, found, cuda_major(name)
    for component, exe in ((LLAMA, model_server.llama_server_executable()), (SWAP, model_server.swap_executable())):
        row = {'path': exe, 'version': None, 'error': None}
        if exe:
            try:
                row['version'] = parse_version(component, runner(exe, component))
                if not row['version']:
                    row['error'] = tr('версия не распознана')
            except Exception as exc:
                row['error'] = str(exc)
        result[component] = row
    return result


# ---------------------------------------------------------------- GitHub releases

def fetch_json(url, timeout=20):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json',
                                                   'User-Agent': 'LAAS/' + VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def releases(component, fetch=fetch_json, per_page=15):
    rows = fetch(f'https://api.github.com/repos/{REPOS[component]}/releases?per_page={per_page}')
    if not isinstance(rows, list):
        raise ValueError(tr('GitHub вернул неожиданный ответ'))
    return [r for r in rows if not r.get('draft')]


def _asset(asset):
    digest = asset.get('digest') or ''
    return {'name': asset['name'], 'url': asset['browser_download_url'], 'size': asset.get('size'),
            'sha256': digest.split(':', 1)[1].lower() if digest.lower().startswith('sha256:') else None}


def pick_llama(rows, major):
    """Newest build ``b<N>`` with a Windows x64 asset for this CUDA major (CPU build when major is None)."""
    for release in rows:
        tag = release.get('tag_name') or ''
        match = re.fullmatch(r'b(\d+)', tag)
        if not match:
            continue
        flavor = rf'cuda-{major}(\.\d+)*' if major else 'cpu'
        pattern = re.compile(rf'^llama-b\d+-bin-win-{flavor}-x64\.zip$', re.I)
        assets = [a for a in release.get('assets', []) if pattern.match(a.get('name', ''))]
        if not assets:
            continue
        cudart = None
        if major:
            runtime = re.compile(rf'^cudart-llama-bin-win-cuda-{major}(\.\d+)*-x64\.zip$', re.I)
            cudart = next((_asset(a) for a in release.get('assets', []) if runtime.match(a.get('name', ''))), None)
        return {'component': LLAMA, 'build': int(match.group(1)), 'label': tag, 'asset': _asset(assets[0]),
                'cudart': cudart, 'page': release.get('html_url') or RELEASES_PAGE[LLAMA]}
    return None


def pick_swap(rows):
    pattern = re.compile(r'^llama-swap_\d+_windows_amd64\.zip$', re.I)
    for release in rows:
        match = re.fullmatch(r'v?(\d+)', release.get('tag_name') or '')
        if not match or release.get('prerelease'):
            continue
        asset = next((a for a in release.get('assets', []) if pattern.match(a.get('name', ''))), None)
        if asset:
            return {'component': SWAP, 'build': int(match.group(1)), 'label': 'v' + match.group(1),
                    'asset': _asset(asset), 'cudart': None, 'page': release.get('html_url') or RELEASES_PAGE[SWAP]}
    return None


def check_updates(current, fetch=fetch_json):
    """``current`` is the result of :func:`installed`. Returns per-component latest release and flags."""
    result = {}
    for component in (LLAMA, SWAP):
        row = {'latest': None, 'update': False, 'error': None}
        try:
            rows = releases(component, fetch)
            row['latest'] = pick_llama(rows, current.get('cuda_major')) if component == LLAMA else pick_swap(rows)
            if not row['latest']:
                row['error'] = tr('подходящая сборка для Windows не найдена')
            else:
                version = (current.get(component) or {}).get('version')
                row['update'] = not version or row['latest']['build'] > version['build']
        except Exception as exc:
            row['error'] = describe_http_error(exc)
        result[component] = row
    return result


def describe_http_error(exc):
    import urllib.error
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 403:
            return tr('GitHub ограничил число запросов, попробуйте позже')
        return tr('GitHub ответил ошибкой {code}', code=exc.code)
    if isinstance(exc, urllib.error.URLError):
        return tr('нет соединения с GitHub: {error}', error=exc.reason)
    return str(exc)


# ---------------------------------------------------------------- download and install

def download(asset, target, opener=urllib.request.urlopen, progress=None, chunk=1 << 20):
    """Download to ``target`` through a ``.part`` file and verify size and SHA-256 when GitHub provides them."""
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + '.part')
    request = urllib.request.Request(asset['url'], headers={'User-Agent': 'LAAS/' + VERSION})
    sha = hashlib.sha256()
    size = 0
    try:
        with opener(request, timeout=60) as response, open(part, 'wb') as out:
            while True:
                block = response.read(chunk)
                if not block:
                    break
                out.write(block)
                sha.update(block)
                size += len(block)
                if progress:
                    progress(size, asset.get('size'))
        if asset.get('size') and size != asset['size']:
            raise ValueError(tr('Размер файла {name} не совпадает: {got} вместо {expected} байт', name=asset['name'], got=size, expected=asset['size']))
        if asset.get('sha256') and sha.hexdigest() != asset['sha256']:
            raise ValueError(tr('Контрольная сумма {name} не совпадает — файл повреждён', name=asset['name']))
        os.replace(part, target)
    finally:
        if part.exists():
            part.unlink()
    return target


def verify_zip(path):
    """Reject damaged archives and entries that would escape the destination folder."""
    try:
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(tr('Архив повреждён: {name}', name=bad))
            names = archive.namelist()
    except zipfile.BadZipFile as exc:
        raise ValueError(tr('Файл не является zip-архивом: {path}', path=path)) from exc
    for name in names:
        parts = Path(name.replace('\\', '/')).parts
        if name.startswith(('/', '\\')) or '..' in parts or (parts and ':' in parts[0]):
            raise ValueError(tr('Недопустимый путь в архиве: {name}', name=name))
    if not names:
        raise ValueError(tr('Архив пуст: {path}', path=path))
    return names


def safe_extract(path, destination):
    verify_zip(path)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path) as archive:
        for member in archive.infolist():
            target = (destination / member.filename).resolve()
            if not _inside(target, destination.resolve()):
                raise ValueError(tr('Недопустимый путь в архиве: {name}', name=member.filename))
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(member) as source, open(target, 'wb') as out:
                shutil.copyfileobj(source, out)
    return destination


def find_exe(folder, component, depth=3):
    name = exe_name(component)
    queue = [(Path(folder), 0)]
    while queue:
        current, level = queue.pop(0)
        if (current / name).is_file():
            return current / name
        if level < depth:
            queue.extend((p, level + 1) for p in sorted(current.iterdir()) if p.is_dir())
    return None


def install_component(component, label, archives, runner=run_version, root=None):
    """Extract verified archives into ``engines/<component>-<label>`` and check that the program runs.

    ``archives`` is a list of zip paths (the llama.cpp build, optionally its CUDA runtime).
    An existing verified folder for the same version is reused.
    """
    root = Path(root or engines_dir())
    folder = root / f'{component}-{label}'
    if folder.exists():
        exe = find_exe(folder, component)
        if exe and parse_version(component, _try(runner, exe, component)):
            return folder
        shutil.rmtree(folder)
    staging = root / f'.{component}-{label}.staging'
    if staging.exists():
        shutil.rmtree(staging)
    try:
        for archive in archives:
            safe_extract(archive, staging)
        exe = find_exe(staging, component)
        if not exe:
            raise ValueError(tr('В архиве нет {name}', name=exe_name(component)))
        output = runner(exe, component)
        if not parse_version(component, output):
            raise ValueError(tr('{name} не запускается или не сообщает версию: {output}', name=exe_name(component), output=output.strip()[:300]))
        os.replace(staging, folder)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    return folder


def _try(runner, exe, component):
    try:
        return runner(exe, component)
    except Exception:
        return ''


def activate(parts, previous, *, root=None, clock=datetime.now, update_config=None):
    """Build ``engines/active-<ts>/{llama.cpp,llama-swap}`` from component folders and select it.

    ``parts`` maps component -> {'folder': Path, 'label': str}. For each component the folder
    containing its executable is copied. CUDA runtime DLLs next to the previous llama-server are
    carried over when the new build lacks them. ``previous`` stores the settings before the switch
    (runtime_dir and explicit executables) so the user can switch back.
    """
    root = Path(root or engines_dir())
    stamp = clock().strftime('%Y%m%d-%H%M%S')
    active = root / f'active-{stamp}'
    staging = root / f'.active-{stamp}.staging'
    if staging.exists():
        shutil.rmtree(staging)
    manifest = {'created': clock().isoformat(timespec='seconds'), 'components': {}, 'previous': previous}
    try:
        for component, part in parts.items():
            exe = find_exe(part['folder'], component) if Path(part['folder']).is_dir() else None
            if not exe:
                raise ValueError(tr('Не найден {name} в {path}', name=exe_name(component), path=part['folder']))
            destination = staging / component
            if component == SWAP:
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(exe, destination / exe.name)
            else:
                shutil.copytree(exe.parent, destination)
                old = part.get('cuda_from')
                if old and Path(old).is_dir():
                    present = {p.name.lower() for p in destination.iterdir()}
                    for dll in Path(old).iterdir():
                        if CUDA_DLL.match(dll.name) and dll.name.lower() not in present:
                            shutil.copy2(dll, destination / dll.name)
            manifest['components'][component] = {'label': part.get('label'), 'source': str(part['folder'])}
        (staging / ACTIVE_MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(staging, active)
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    select_runtime(str(active), update_config=update_config)
    return active


def select_runtime(runtime_dir, explicit=None, update_config=None):
    """Set runtime_dir ('' = bundled engine). Explicit executables are replaced by ``explicit`` (usually cleared)."""
    if update_config is None:
        from .config import config
        update_config = config.update
    changes = {'runtime_dir': runtime_dir or ''}
    for key in EXPLICIT_KEYS:
        changes[key] = (explicit or {}).get(key) or ''
    update_config(changes)
    return changes


def install_updates(current, updates, components, *, opener=urllib.request.urlopen, runner=run_version,
                    progress=None, root=None, clock=datetime.now, update_config=None, settings=None):
    """Download, verify, install the chosen components and switch runtime_dir to a new active folder.

    ``current`` comes from :func:`installed`, ``updates`` from :func:`check_updates`.
    Components that are not updated are copied from the engine in use.
    """
    root = Path(root or engines_dir())
    downloads = root / 'downloads'
    parts = {}
    for component in (LLAMA, SWAP):
        latest = (updates.get(component) or {}).get('latest')
        if component in components and latest:
            archives = []
            wanted = [latest['asset']]
            if component == LLAMA and latest.get('cudart') and not current.get('cuda_path'):
                wanted.append(latest['cudart'])
            for asset in wanted:
                target = downloads / asset['name']
                if not (target.is_file() and asset.get('size') and target.stat().st_size == asset['size']
                        and (not asset.get('sha256') or _sha256(target) == asset['sha256'])):
                    download(asset, target, opener=opener,
                             progress=(lambda done, total, name=asset['name']: progress(name, done, total)) if progress else None)
                archives.append(target)
            folder = install_component(component, latest['label'], archives, runner=runner, root=root)
            parts[component] = {'folder': folder, 'label': latest['label']}
        else:
            exe = (current.get(component) or {}).get('path')
            if not exe:
                raise ValueError(tr('{name} не установлен — установите оба компонента движка', name=component))
            version = (current.get(component) or {}).get('version') or {}
            parts[component] = {'folder': Path(exe).parent, 'label': version.get('label')}
    old_server = (current.get(LLAMA) or {}).get('path')
    if old_server:
        parts[LLAMA]['cuda_from'] = Path(old_server).parent
    if settings is None:
        from .config import config
        settings = {key: config.get(key, '') for key in ('runtime_dir',) + EXPLICIT_KEYS}
        if not settings['runtime_dir'] and current.get('source') == 'custom':
            settings['runtime_dir'] = str(current['runtime_dir'])
    active = activate(parts, settings, root=root, clock=clock, update_config=update_config)
    for archive in downloads.glob('*.zip') if downloads.is_dir() else []:
        archive.unlink()
    return active


def _sha256(path):
    sha = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 20), b''):
            sha.update(block)
    return sha.hexdigest()


def read_manifest(folder):
    try:
        return json.loads((Path(folder) / ACTIVE_MANIFEST).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None


def rollback_choices(current_runtime, bundled, root=None):
    """Engine folders the user can switch to: previous active folders, recorded custom folders, bundled engine."""
    root = Path(root or engines_dir())
    choices = []
    actives = sorted((p for p in root.glob('active-*') if p.is_dir()), key=lambda p: p.name, reverse=True) if root.is_dir() else []
    seen = set()
    for folder in actives:
        manifest = read_manifest(folder) or {}
        labels = {c: v.get('label') for c, v in manifest.get('components', {}).items()}
        choices.append({'kind': 'managed', 'runtime_dir': str(folder), 'explicit': {},
                        'labels': labels, 'current': bool(current_runtime) and _same(current_runtime, folder)})
        previous = manifest.get('previous') or {}
        path = previous.get('runtime_dir') or ''
        key = os.path.normcase(path)
        if path and not _inside(path, root) and key not in seen and Path(path).is_dir():
            seen.add(key)
            choices.append({'kind': 'custom', 'runtime_dir': path, 'labels': {},
                            'explicit': {k: previous.get(k, '') for k in EXPLICIT_KEYS},
                            'current': bool(current_runtime) and _same(current_runtime, path)})
    choices.append({'kind': 'bundled', 'runtime_dir': '', 'explicit': {}, 'labels': {}, 'available': bool(bundled),
                    'current': bool(bundled and current_runtime) and _same(current_runtime, bundled)})
    return choices


def cleanup_candidates(current_runtime, keep=KEEP_VERSIONS, root=None):
    """Old folders beyond the newest ``keep`` per kind; the active engine and what it was built from stay."""
    root = Path(root or engines_dir())
    if not root.is_dir():
        return []
    protected = set()
    if current_runtime and _inside(current_runtime, root):
        protected.add(os.path.normcase(str(Path(current_runtime))))
        for part in (read_manifest(current_runtime) or {}).get('components', {}).values():
            protected.add(os.path.normcase(str(Path(part.get('source', '')))))
    result = []
    groups = {'active': [p for p in root.glob('active-*') if p.is_dir()]}
    for component in (LLAMA, SWAP):
        groups[component] = [p for p in root.glob(component + '-*') if p.is_dir()]
    for kind, folders in groups.items():
        ordered = sorted(folders, key=_folder_order, reverse=True)
        kept = 0
        for folder in ordered:
            if os.path.normcase(str(folder)) in protected:
                continue
            kept += 1
            if kept > keep:
                result.append(folder)
    return result


def delete_folders(folders, root=None):
    root = Path(root or engines_dir()).resolve()
    removed = []
    for folder in folders:
        folder = Path(folder).resolve()
        if folder == root or not _inside(folder, root):
            raise ValueError(tr('Удалять можно только папки внутри {path}', path=root))
        shutil.rmtree(folder)
        removed.append(folder)
    return removed


def _folder_order(path):
    numbers = re.findall(r'\d+', path.name)
    return (tuple(int(n) for n in numbers), path.stat().st_mtime)


def _same(a, b):
    try:
        return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))
    except OSError:
        return False


def _inside(path, folder):
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
        return True
    except (ValueError, OSError):
        return False
