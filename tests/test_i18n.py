"""Every user-visible Russian text goes through tr() and has English and Ukrainian translations."""
import ast
import json
import re
from pathlib import Path
import pytest
from src import i18n

ROOT = Path(__file__).resolve().parents[1]
SOURCES = sorted([*ROOT.glob('src/**/*.py'), ROOT / 'main.pyw'])
CYRILLIC = re.compile('[А-Яа-яЁёІіЇїЄєҐґ]')
PLACEHOLDER = re.compile(r'{(\w+)[^}]*}')
# Language names are shown in their own language on purpose.
ALLOWED = {'Русский', 'Українська'}


def calls_and_literals(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    msgids, bare = [], []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, 'id', getattr(node.func, 'attr', None)) == 'tr':
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                msgids.append(node.args[0].value)
            else:
                bare.append((node.lineno, 'tr() needs a literal Russian message id'))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and CYRILLIC.search(node.value):
            parent = parents.get(node)
            if isinstance(parent, ast.Expr):  # docstring
                continue
            if isinstance(parent, ast.Call) and getattr(parent.func, 'id', getattr(parent.func, 'attr', None)) == 'tr':
                continue
            if isinstance(parent, ast.Call) and getattr(getattr(parent.func, 'value', None), 'id', '') in ('logging', 'logger', 'log'):
                continue
            if isinstance(parent, ast.JoinedStr):
                bare.append((node.lineno, 'f-string with Russian text: use tr("... {name} ...", name=value)'))
            elif node.value not in ALLOWED:
                bare.append((node.lineno, node.value[:60]))
    return msgids, bare


def test_russian_text_is_wrapped_in_tr():
    problems = []
    for path in SOURCES:
        for line, text in calls_and_literals(path)[1]:
            problems.append(f'{path.relative_to(ROOT)}:{line}: {text}')
    assert not problems, 'Untranslated UI text:\n' + '\n'.join(problems)


@pytest.mark.parametrize('language', ['en', 'uk'])
def test_every_message_is_translated_with_same_placeholders(language):
    catalog = {}
    for path in sorted((ROOT / 'locales' / language).glob('*.json')):
        catalog.update(json.loads(path.read_text(encoding='utf-8')))
    problems = []
    for path in SOURCES:
        for msgid in calls_and_literals(path)[0]:
            if msgid not in catalog:
                problems.append(f'{path.relative_to(ROOT)}: missing: {msgid[:70]}')
            elif set(PLACEHOLDER.findall(msgid)) != set(PLACEHOLDER.findall(catalog[msgid])):
                problems.append(f'{path.relative_to(ROOT)}: placeholders differ: {msgid[:70]}')
            elif CYRILLIC.search(catalog[msgid]) and language == 'en':
                problems.append(f'{path.relative_to(ROOT)}: English text contains Cyrillic: {msgid[:70]}')
    assert not problems, '\n'.join(problems)


def test_tr_uses_selected_language(monkeypatch, tmp_path):
    folder = tmp_path / 'locales/en'
    folder.mkdir(parents=True)
    (folder / 'x.json').write_text(json.dumps({'Модель «{name}» загружена': 'Model “{name}” loaded'}), encoding='utf-8')
    monkeypatch.setattr(i18n, 'locales_dir', lambda: tmp_path / 'locales')
    monkeypatch.setattr(i18n, '_catalogs', {})
    monkeypatch.setenv('LOCAL_AGENT_STATION_LANGUAGE', 'en')
    assert i18n.tr('Модель «{name}» загружена', name='Q') == 'Model “Q” loaded'
    assert i18n.tr('Нет перевода') == 'Нет перевода'
    monkeypatch.setenv('LOCAL_AGENT_STATION_LANGUAGE', 'ru')
    assert i18n.tr('Модель «{name}» загружена', name='Q') == 'Модель «Q» загружена'
