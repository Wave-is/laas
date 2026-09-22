"""Package the committed source, dependency notices and checksummed installer."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.version import VERSION


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--finalize', action='store_true')
    args = parser.parse_args()
    dist = ROOT / 'dist'
    out = dist / 'release'
    out.mkdir(parents=True, exist_ok=True)
    onedir_exe = dist / 'LocalAgentAIStation' / 'LocalAgentAIStation.exe'
    exe = onedir_exe if onedir_exe.exists() else (dist / 'LocalAgentAIStation.exe')
    source = out / f'LocalAgentAIStation-{VERSION}-source.zip'
    installer = out / f'LocalAgentAIStation-{VERSION}-Setup-x64.exe'
    if args.finalize:
        manifest = json.loads((dist / 'BUILD.json').read_text(encoding='utf-8'))
        assert manifest['source_commit'] == git('rev-parse', 'HEAD'), 'Source commit changed during build'
        assert manifest['exe_sha256'] == sha(exe), 'Executable changed during packaging'
        assert installer.is_file() and installer.stat().st_size > 1_000_000
        manifest['installer_sha256'] = sha(installer)
        manifest['source_archive_sha256'] = sha(source)
        (out / 'BUILD.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
        files = [installer, source, out / 'BUILD.json']
        (out / 'SHA256SUMS.txt').write_text(''.join(f'{sha(p)}  {p.name}\n' for p in files), encoding='ascii')
        print(json.dumps({'version': VERSION, 'assets': [p.name for p in files]}, indent=2))
        return
    dirty = git('status', '--porcelain', '--untracked-files=normal')
    if dirty:
        raise RuntimeError('Commit release inputs before packaging; private files belong in ignored folders.\n' + dirty)
    assert exe.is_file()
    subprocess.run([sys.executable, str(ROOT / 'tools/collect_notices.py')], cwd=ROOT, check=True)
    # LGPL component source accompanies the installed application and can be rebuilt/replaced.
    version = metadata.version('pystray')
    target = dist / 'third-party-source' / f'pystray-{version}.tar.gz'
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        import requests
        response = requests.get(f'https://github.com/moses-palmer/pystray/archive/refs/tags/v{version}.tar.gz', timeout=120)
        response.raise_for_status()
        target.write_bytes(response.content)
    with tarfile.open(target, 'r:gz') as archive:
        assert any(p.name.endswith('/COPYING.LGPL') for p in archive.getmembers())
    subprocess.run(['git', 'archive', '--format=zip', '--output', str(source), 'HEAD'], cwd=ROOT, check=True)
    with zipfile.ZipFile(source) as archive:
        assert archive.testzip() is None
        assert not any(p.startswith(('runtime/', 'handoff-local/', '.venv/', '.git/', 'dist/')) for p in archive.namelist())
    manifest = {
        'application': 'Local Agent AI Station', 'version': VERSION,
        'status': 'early prerelease', 'source_commit': git('rev-parse', 'HEAD'),
        'built_at_utc': datetime.now(timezone.utc).isoformat(), 'exe_sha256': sha(exe),
        'python_version': sys.version.split()[0], 'platform': 'Windows x64',
        'compiler': 'Inno Setup 6.7.3', 'code_signed': False,
    }
    (dist / 'BUILD.json').write_text(json.dumps(manifest, indent=2)+'\n', encoding='utf-8')
    print('Release inputs ready:', VERSION)


if __name__ == '__main__':
    main()
