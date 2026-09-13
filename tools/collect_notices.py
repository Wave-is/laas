"""Collect installed dependency notices and versions for a local Windows build."""
import ast
import importlib.metadata as metadata
import json
from pathlib import Path
import re
import shutil
import sys

root = Path(__file__).resolve().parents[1]
out = root / 'dist/licenses'
out.mkdir(parents=True, exist_ok=True)
entries = ast.literal_eval((root/'build/LocalAgentAIStation/PYZ-00.toc').read_text(encoding='utf-8'))[1]
def records(value):
    if isinstance(value, (tuple, list)):
        if len(value) == 3 and all(isinstance(v, str) for v in value) and value[2] in ('PYMODULE', 'EXTENSION', 'BINARY'):
            yield value
        else:
            for child in value:
                yield from records(child)
entries += list(records(ast.literal_eval((root/'build/LocalAgentAIStation/Analysis-00.toc').read_text(encoding='utf-8'))))
modules = {row[0].split('.')[0] for row in entries}
files = {str(Path(row[1]).resolve()).casefold() for row in entries if row[1]}
package_map = metadata.packages_distributions()
names = {name for module in modules for name in package_map.get(module, [])}
names.add('pyinstaller')
versions = {}
for name in sorted(names):
    dist = metadata.distribution(name)
    listed = dist.files or []
    if name != 'pyinstaller' and not any(str(Path(dist.locate_file(f)).resolve()).casefold() in files for f in listed):
        continue
    versions[dist.metadata['Name']] = dist.version
    for f in listed:
        if re.match(r'(?i)^(license|copying|notice|copyright)([._-].*)?$', f.name):
            source = Path(dist.locate_file(f))
            if source.is_file():
                parts = [p for p in f.parts if p not in ('..', '.')]
                target = out / re.sub(r'[^a-zA-Z0-9._-]', '_', name) / Path(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
base = Path(sys.base_prefix)
for source in (base/'tcl').glob('*/license.terms'):
    target = out/'Tcl-Tk'/source.parent.name/'license.terms'
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
for filename in ('LICENSE_PYTHON.txt', 'LICENSE.txt', 'LICENSE'):
    source = base / filename
    if source.is_file():
        shutil.copy2(source, out / ('Python-' + filename))
# Conda runtime packages can carry Tcl/Tk, OpenSSL, zlib and libffi notices in their package cache.
for record in (base/'conda-meta').glob('*.json'):
    data = json.loads(record.read_text(encoding='utf-8'))
    if data.get('name') not in ('tk', 'openssl', 'zlib', 'libzlib', 'libffi'):
        continue
    package = f"{data['name']}-{data['version']}-{data['build']}"
    notices = base/'pkgs'/package/'info/licenses'
    if notices.is_dir():
        for source in notices.rglob('*'):
            if source.is_file():
                target = out/package/source.relative_to(notices)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    else:
        print('Review native dependency notice:', package)
(root/'dist/dependency-versions.json').write_text(json.dumps(versions, indent=2), encoding='utf-8')
(root/'dist/build-requirements.lock.txt').write_text(''.join(f'{name}=={version}\n' for name,version in versions.items()), encoding='utf-8')
print(json.dumps({'dependency_versions': versions, 'notice_files': len(list(out.rglob('*.*')))}, indent=2))
