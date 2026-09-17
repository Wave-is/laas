"""Log tail reading, error filter, secret redaction and the diagnostics bundle."""
import json
import zipfile
from datetime import datetime

import yaml

from src import diagnostics


def test_read_tail_of_large_file_starts_at_line_boundary(tmp_path):
    path = tmp_path / 'big.log'
    with path.open('wb') as handle:
        for index in range(200_000):
            handle.write(f'line {index:06d} some payload text\n'.encode())
    size = path.stat().st_size
    assert size > 5 * 1024 * 1024
    text = diagnostics.read_tail(path, limit=64 * 1024)
    lines = text.splitlines()
    assert len(text.encode()) <= 64 * 1024
    assert lines[-1] == 'line 199999 some payload text'
    assert lines[0].startswith('line ') and lines[0].endswith('payload text')
    assert diagnostics.read_tail(path, limit=10 * 1024 * 1024).splitlines()[0] == 'line 000000 some payload text'


def test_read_tail_decodes_broken_utf8_with_replacement(tmp_path):
    path = tmp_path / 'broken.log'
    path.write_bytes('ok\n'.encode() + b'\xff\xfe bad\n' + 'ошибка\n'.encode())
    text = diagnostics.read_tail(path)
    assert '\ufffd' in text and 'ошибка' in text


def test_error_filter_and_search():
    text = '\n'.join([
        '2026-09-17 INFO started',
        '2026-09-17 ERROR model failed',
        'WARNING: slow disk',
        'Traceback (most recent call last):',
        'ValueError exception here',
        'Произошла ошибка загрузки',
        'ОШИБКА в верхнем регистре',
        'request failed with 500',
        'all good',
    ])
    errors = diagnostics.filter_lines(text, errors_only=True)
    assert 'all good' not in errors and '2026-09-17 INFO started' not in errors
    assert len(errors) == 7
    assert diagnostics.filter_lines(text, query='SLOW') == ['WARNING: slow disk']
    assert diagnostics.filter_lines(text, errors_only=True, query='model') == ['2026-09-17 ERROR model failed']
    assert len(diagnostics.filter_lines(text)) == 9


def test_redact_nested_documents_and_env_items():
    document = {
        'api_key': 'sk-top', 'name': 'visible',
        'providers': [{'ApiKey': 'sk-nested', 'url': 'http://localhost'}, {'auth': {'Token': 'tok-1', 'password': 'p'}}],
        'env': ['OPENAI_API_KEY=sk-env-secret', 'PATH=C:/bin', 'HF_TOKEN=hf_abc'],
        'environment': {'MY_SECRET': 'shh', 'LANG': 'en'},
        'cmd': 'llama-server --port 1 --api-key sk-flag --api-key=sk-flag2',
        'headers': ['Authorization: Bearer abcdefghijklmnop'],
        'empty_token': '',
    }
    result = diagnostics.redact(document)
    dumped = json.dumps(result)
    for secret in ('sk-top', 'sk-nested', 'tok-1', 'sk-env-secret', 'hf_abc', 'shh', 'sk-flag', 'abcdefghijklmnop'):
        assert secret not in dumped
    assert result['name'] == 'visible'
    assert result['providers'][0]['url'] == 'http://localhost'
    assert result['env'][0] == 'OPENAI_API_KEY=' + diagnostics.REDACTED
    assert result['env'][1] == 'PATH=C:/bin'
    assert result['environment']['LANG'] == 'en'
    assert document['api_key'] == 'sk-top'  # the original is untouched


def test_redact_text_lines():
    text = 'set OPENAI_API_KEY=sk-1\nexport db_password="p w"\nnormal=value\nsecret: abc\n'
    redacted = diagnostics.redact_text(text)
    assert 'sk-1' not in redacted and 'p w' not in redacted and 'abc' not in redacted
    assert 'normal=value' in redacted


def test_redact_document_yaml_and_invalid(tmp_path):
    content = yaml.safe_dump({'agents': {'hermes': {'env': {'ANTHROPIC_API_KEY': 'sk-ant'}}}, 'port': 9292})
    redacted = diagnostics.redact_document(content, '.yaml')
    assert 'sk-ant' not in redacted and '9292' in redacted
    assert 'sk-x' not in diagnostics.redact_document('::: not yaml {\nAPI_KEY=sk-x', '.yaml')


def test_log_sources_friendly_names(tmp_path):
    logs = tmp_path / 'logs'
    logs.mkdir()
    (logs / 'station.log').write_text('a', encoding='utf-8')
    (logs / 'station.log.1').write_text('b', encoding='utf-8')
    (logs / 'service-llama-swap.log').write_text('c', encoding='utf-8')
    hashed = diagnostics._hashed('frontend:qwen-desktop')
    (logs / f'{hashed}.log').write_text('d', encoding='utf-8')
    (logs / 'startup-last.json').write_text('{}', encoding='utf-8')
    (logs / '.env').write_text('X=1', encoding='utf-8')
    records = {'service:llama-swap': {'log': str(logs / 'service-llama-swap.log')}}
    sources = diagnostics.log_sources(records=records, frontends={'qwen-desktop': {'name': 'Qwen Desktop'}}, folder=logs)
    names = {s['name'] for s in sources}
    assert sources[0]['path'].endswith('station.log')
    assert 'Сервер моделей (llama-swap)' in names
    assert 'Интерфейс агента: Qwen Desktop' in names
    assert 'Station (архив 1)' in names
    assert not any(s['path'].endswith('.env') for s in sources)
    assert len(sources) == 5  # the llama-swap log appears once


def test_bundle_excludes_secrets_and_limits_log_size(tmp_path, monkeypatch):
    monkeypatch.setenv('LOCAL_AGENT_STATION_HOME', str(tmp_path))
    config = tmp_path / 'config'
    (config / 'backups').mkdir(parents=True)
    (config / 'station.yaml').write_text(yaml.safe_dump({'language': 'ru', 'hf_token': 'hf_secret_value'}), encoding='utf-8')
    (config / 'agent_frontends.yaml').write_text(yaml.safe_dump(
        [{'id': 'x', 'env': ['OPENAI_API_KEY=sk-live-123', 'MODE=fast']}]), encoding='utf-8')
    (config / '.env').write_text('OPENAI_API_KEY=sk-dotenv', encoding='utf-8')
    (config / 'backups' / 'station.yaml').write_text('api_key: sk-backup', encoding='utf-8')
    (tmp_path / 'backend').mkdir()
    (tmp_path / 'backend' / 'llama-swap.yaml').write_text(
        'models:\n  q:\n    cmd: llama-server --api-key sk-cmd-secret --port 5\n', encoding='utf-8')
    (tmp_path / 'processes.json').write_text(json.dumps({'service:llama-swap': {'pid': 1, 'log': 'x'}}), encoding='utf-8')
    logs = tmp_path / 'logs'
    logs.mkdir()
    with (logs / 'station.log').open('w', encoding='utf-8') as handle:
        handle.write('HF_TOKEN=hf_in_log\n')
        for index in range(60_000):
            handle.write(f'{index:08d} INFO filler line for size\n')
        handle.write('ERROR last line password=hunter2\n')
    (logs / 'startup-last.json').write_text(json.dumps({'ok': True, 'token': 'startup-tok'}), encoding='utf-8')
    (logs / 'watchdog.jsonl').write_text('{"event": "restart"}\n', encoding='utf-8')
    limit = 256 * 1024
    report = {'application': 'LAAS', 'agents': {'hermes': {'env': {'OPENROUTER_API_KEY': 'sk-or-report'}}}}
    path = diagnostics.collect_diagnostics(report=report, records={}, log_limit=limit, now=datetime(2026, 9, 17, 12, 0, 0))

    assert path == tmp_path / 'diagnostics' / 'laas-diagnostics-20260917-120000.zip'
    with zipfile.ZipFile(path) as bundle:
        names = set(bundle.namelist())
        contents = {name: bundle.read(name) for name in names}
    assert {'README.txt', 'report.json', 'config/station.yaml', 'config/agent_frontends.yaml', 'backend/llama-swap.yaml',
            'processes.json', 'logs/station.log', 'reports/startup-last.json', 'reports/watchdog.jsonl'} <= names
    assert not any('.env' in name or 'backups' in name for name in names)
    blob = b'\n'.join(contents.values())
    for secret in (b'hf_secret_value', b'sk-live-123', b'sk-dotenv', b'sk-backup', b'sk-cmd-secret', b'hf_in_log',
                   b'hunter2', b'startup-tok', b'sk-or-report'):
        assert secret not in blob
    assert b'MODE=fast' in contents['config/agent_frontends.yaml']
    assert len(contents['logs/station.log']) <= limit
    assert contents['logs/station.log'].rstrip().endswith(b'password=' + diagnostics.REDACTED.encode())
    assert (logs / 'station.log').stat().st_size > limit
    assert not list((tmp_path / 'diagnostics').glob('*.partial'))


def test_build_report_uses_collectors_and_redacts():
    report = diagnostics.build_report(agent_status={'a': {'api_key': 'sk-1'}}, collectors={
        'hardware': lambda: {'gpus': 2}, 'gpu_helper': lambda: 1 / 0, 'backend': lambda: {'online': True},
        'model_server': lambda: {'runtime_dir': 'D:/engine'}})
    assert report['hardware'] == {'gpus': 2}
    assert 'ZeroDivisionError' in report['gpu_helper']['error']
    assert report['agents']['a']['api_key'] == diagnostics.REDACTED
    assert report['version'] and report['os']['python']
