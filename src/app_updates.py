"""Station update check through GitHub releases. Runs only when requested or enabled (``app_update_check``)."""
import json
import re
import urllib.error
import urllib.request

from .i18n import tr
from .version import VERSION

REPOSITORY = 'Wave-is/laas'
RELEASES_API = f'https://api.github.com/repos/{REPOSITORY}/releases'
RELEASES_PAGE = f'https://github.com/{REPOSITORY}/releases'
SEMVER = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$')


def parse_semver(text):
    match = SEMVER.match((text or '').strip())
    if not match:
        return None
    pre = tuple(int(p) if p.isdigit() else p for p in match.group(4).split('.')) if match.group(4) else ()
    return (int(match.group(1)), int(match.group(2)), int(match.group(3))), pre


def _pre_key(pre):
    # SemVer 2.0: numeric identifiers sort before alphanumeric ones; a release sorts after its pre-releases.
    return tuple((0, p, '') if isinstance(p, int) else (1, 0, p) for p in pre)


def compare(a, b):
    """-1, 0 or 1 comparing two version strings (0.1.0-beta.1 < 0.1.0-beta.2 < 0.1.0 < 0.1.1)."""
    pa, pb = parse_semver(a), parse_semver(b)
    if not pa or not pb:
        raise ValueError(tr('Неверный номер версии: {version}', version=a if not pa else b))
    if pa[0] != pb[0]:
        return -1 if pa[0] < pb[0] else 1
    if pa[1] == pb[1]:
        return 0
    if not pa[1]:
        return 1
    if not pb[1]:
        return -1
    ka, kb = _pre_key(pa[1]), _pre_key(pb[1])
    return -1 if ka < kb else 1 if ka > kb else 0


def fetch_json(url, timeout=15):
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'LAAS/' + VERSION})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode('utf-8'))


def check(current=VERSION, fetch=fetch_json):
    """{'Success', 'Message', 'Release': {'version','name','url','prerelease','published'} or None}."""
    try:
        rows = fetch(RELEASES_API)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 404):
            return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: репозиторий недоступен или приватный.')}
        if exc.code == 403:
            return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: GitHub ограничил число запросов, попробуйте позже.')}
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: GitHub ответил ошибкой {code}.', code=exc.code)}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: нет соединения с GitHub ({error}).', error=getattr(exc, 'reason', exc))}
    if not isinstance(rows, list):
        return {'Success': False, 'Release': None, 'Message': tr('Проверка обновлений Station: неожиданный ответ GitHub.')}
    allow_pre = bool(parse_semver(current) and parse_semver(current)[1])
    best = None
    for row in rows:
        version = (row.get('tag_name') or '').lstrip('v')
        parsed = parse_semver(version)
        if row.get('draft') or not parsed or (parsed[1] and not allow_pre):
            continue
        if best is None or compare(version, best['version']) > 0:
            best = {'version': version, 'name': row.get('name') or version, 'url': row.get('html_url') or RELEASES_PAGE,
                    'prerelease': bool(parsed[1]), 'published': (row.get('published_at') or '')[:10]}
    if best and compare(best['version'], current) > 0:
        return {'Success': True, 'Release': best,
                'Message': tr('Доступна новая версия Station {version} (у вас {current}).', version=best['version'], current=current)}
    return {'Success': True, 'Release': None, 'Message': tr('Установлена последняя версия Station ({current}).', current=current)}
