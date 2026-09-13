"""Save a local checkpoint without changing Git, application data, or services.

Run from any directory: python /path/to/repo/tools/handoff_snapshot.py
Only handoff-local/SNAPSHOT.json is written. No credentials, external configs,
environment variables, process command lines, or Git remote URLs are collected.
The semantic state/next action must still be maintained in docs/HANDOFF.md.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parents[1]


def git(*args):
    try:
        result = subprocess.run(
            ['git', *args], cwd=ROOT, capture_output=True, text=True,
            encoding='utf-8', errors='replace', timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'available': False, 'error': type(exc).__name__}
    if result.returncode:
        return {'available': False, 'exit_code': result.returncode}
    return {'available': True, 'value': result.stdout.rstrip()}


def artifact(relative):
    path = ROOT / relative
    if not path.is_file():
        return {'path': relative, 'exists': False}
    sha = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            sha.update(chunk)
    stat = path.stat()
    return {
        'path': relative, 'exists': True, 'bytes': stat.st_size,
        'modified_utc': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        'sha256': sha.hexdigest(),
    }


def main():
    report = {
        'schema_version': 1,
        'captured_at_utc': datetime.now(timezone.utc).isoformat(),
        'repository_path': str(ROOT),
        'scope': 'Local Git and artifact snapshot; no live OS or GPU verification',
        'read_first': ['AGENTS.md', 'docs/HANDOFF.md', 'handoff-local/README.md'],
        'git': {
            'head': git('rev-parse', 'HEAD'),
            'branch': git('branch', '--show-current'),
            'working_tree': git('status', '--short'),
            'unstaged_summary': git('diff', '--stat'),
            'staged_summary': git('diff', '--cached', '--stat'),
        },
        'artifacts': [artifact('dist/LocalAgentAIStation.exe')] + [
            artifact(path.relative_to(ROOT).as_posix())
            for path in sorted((ROOT / 'dist').rglob('LocalAgentAIStation-*'))
            if path.is_file()
        ],
    }
    manifest_path = ROOT / 'dist/BUILD.json'
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text(encoding='utf-8-sig'))
            report['build'] = {key: manifest.get(key) for key in (
                'application', 'version', 'status', 'source_commit', 'exe_sha256',
            )}
        except (OSError, ValueError, AttributeError) as exc:
            report['build'] = {'error': type(exc).__name__}
    else:
        report['build'] = {'available': False}
    target = ROOT / 'handoff-local/SNAPSHOT.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=target.parent,
                                         prefix='.snapshot-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    print(f'Checkpoint saved: {target}')
    print('Update docs/HANDOFF.md and handoff-local/README.md for the next action and live machine state.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
