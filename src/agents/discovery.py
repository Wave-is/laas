"""Discovery of installed products without installation or downloads."""
import os
from pathlib import Path
import shutil
import json


def npm_installation(binary, packages, settings=None):
    """Resolve npm metadata to node + entrypoint; never execute a shell shim."""
    settings = settings or {}
    configured = settings.get('executable')
    found = configured or (None if settings.get('package_root') else shutil.which(binary))
    node = settings.get('node') or shutil.which('node')
    roots = []
    if settings.get('package_root'):
        roots.append(Path(settings['package_root']).expanduser())
    if found:
        executable = Path(found).expanduser()
        if configured and not executable.is_file():
            raise ValueError('Configured runtime executable is missing')
        if executable.suffix.lower() in ('.js', '.mjs', '.cjs'):
            if not node:
                raise ValueError('Node.js is required for this runtime')
            return [str(node), str(executable)], None
        # POSIX npm shims are symlinks to scripts; inspect their package metadata.
        if executable.is_symlink():
            resolved = executable.resolve()
            roots.extend(parent for parent in resolved.parents if (parent / 'package.json').is_file())
        for prefix in (executable.parent, executable.parent.parent / 'lib'):
            roots.extend(prefix / 'node_modules' / name for name in packages)
        if executable.suffix.lower() == '.exe' or (os.name != 'nt' and not executable.is_symlink()
                and executable.suffix.lower() not in ('.cmd', '.bat', '.ps1')):
            return [str(executable)], None
    prefixes = [] if configured or settings.get('package_root') else [Path(os.environ.get('APPDATA', Path.home() / 'AppData/Roaming')) / 'npm']
    npm = shutil.which('npm')
    if npm and not (configured or settings.get('package_root')):
        prefixes.extend([Path(npm).parent, Path(npm).parent.parent / 'lib'])
    for prefix in prefixes:
        roots.extend(prefix / 'node_modules' / name for name in packages)
    for root in dict.fromkeys(roots):
        metadata = root / 'package.json'
        if not metadata.is_file():
            continue
        meta = json.loads(metadata.read_text(encoding='utf-8'))
        if meta.get('name') not in packages:
            continue
        bins = meta.get('bin', {})
        entry = bins.get(binary) if isinstance(bins, dict) else bins
        if not isinstance(entry, str):
            continue
        path = (root / entry).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError('Invalid npm runtime entrypoint')
        if not node:
            raise ValueError('Node.js is required for this runtime')
        return [str(node), str(path)], root
    if configured or settings.get('package_root'):
        raise ValueError('Configured npm runtime package was not found')
    return [], None

def local_appdata():
    return Path(os.environ.get('LOCALAPPDATA', Path.home() / 'AppData/Local'))

def registered_executables(product):
    if os.name != 'nt':
        return []
    import winreg
    results = []
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
            try:
                root = winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall', 0, winreg.KEY_READ | view)
                with root:
                    for index in range(winreg.QueryInfoKey(root)[0]):
                        try:
                            with winreg.OpenKey(root, winreg.EnumKey(root, index)) as key:
                                name = winreg.QueryValueEx(key, 'DisplayName')[0]
                                if product.lower() not in name.lower():
                                    continue
                                icon = winreg.QueryValueEx(key, 'DisplayIcon')[0].split(',')[0].strip('"')
                                if Path(icon).is_file():
                                    results.append(Path(icon))
                        except OSError:
                            continue
            except OSError:
                pass
    return results

def qwen_installations():
    desktops = registered_executables('Qwen Code')
    standard = local_appdata() / 'Qwen Code Desktop/qwen-code-desktop.exe'
    if standard.is_file():
        desktops.append(standard)
    command, package = [], None
    found = shutil.which('qwen')
    if found and Path(found).suffix.lower() not in ('.cmd', '.bat', '.ps1'):
        command = [found]
    roots = [p.parent / 'runtime/qwen-code' for p in desktops]
    if found:
        roots.insert(0, Path(found).parent / 'node_modules/@qwen-code/qwen-code')
    for root in roots:
        for lib in (root / 'lib', root):
            entry = lib / 'cli-entry.js'
            if not entry.is_file():
                entry = lib / 'cli.js'
            node = root / 'node/node.exe'
            node_path = str(node) if node.is_file() else shutil.which('node')
            if entry.is_file() and node_path and (lib / 'package.json').is_file():
                if not command:
                    command = [node_path, str(entry)]
                package = lib
                break
        if package:
            break
    return command, package, list(dict.fromkeys(desktops))
