"""Model server watchdog: restarts only a crashed Station-owned server, within limits."""
import json
from src.watchdog import ModelServerWatchdog

ONLINE_OWNED = {'online': True, 'owned': True}
OFFLINE = {'online': False, 'owned': False}
EXTERNAL = {'online': True, 'owned': False}


class Fake:
    def __init__(self, tmp_path, **options):
        self.now = 0.0
        self.record = True
        self.alive = True
        self.model = 'qwen'
        self.enabled = True
        self.busy = False
        self.calls = []
        self.messages = []
        self.result = {'Success': True, 'Message': 'ok'}
        defaults = dict(threshold=3, hang_polls=5, backoff=(10, 30, 60), max_restarts=3, window=600)
        defaults.update(options)
        self.dog = ModelServerWatchdog(
            record_exists=lambda: self.record, process_alive=lambda: self.alive,
            load_model=lambda m: self.calls.append(('load', m)) or self.result,
            start_server=lambda: self.calls.append(('start',)) or self.result,
            stop_server=lambda: self.calls.append(('stop',)) or {'Success': True},
            active_model=lambda: self.model, model_name=lambda m: m.upper(), address=lambda: 'http://127.0.0.1:9292/v1',
            is_busy=lambda: self.busy, enabled=lambda: self.enabled, notify=self.messages.append,
            history_path=tmp_path / 'logs' / 'watchdog.jsonl', clock=lambda: self.now, spawn=lambda target: target(),
            **defaults)

    def crash(self):
        self.alive = False
        return [self.dog.poll(OFFLINE) for _ in range(3)]

    def wait(self, seconds):
        self.now += seconds


def test_crash_restarts_with_same_model_and_logs(tmp_path):
    f = Fake(tmp_path)
    assert f.dog.poll(ONLINE_OWNED) == 'ok'
    assert f.crash() == ['suspect', 'suspect', 'backoff']
    assert f.calls == [] and 'неожиданно завершился' in f.messages[0]
    f.wait(10)
    assert f.dog.poll(OFFLINE) == 'restart'
    assert f.calls == [('load', 'qwen')]
    assert 'QWEN' in f.messages[-1] and 'http://127.0.0.1:9292/v1' in f.messages[-1]
    events = [json.loads(line)['event'] for line in (tmp_path / 'logs' / 'watchdog.jsonl').read_text(encoding='utf-8').splitlines()]
    assert events == ['crash', 'restarted']
    assert f.dog.history()[-1]['model'] == 'qwen'


def test_crash_without_model_starts_empty_server(tmp_path):
    f = Fake(tmp_path)
    f.model = 'none'
    f.dog.poll(ONLINE_OWNED)
    f.crash(); f.wait(10)
    assert f.dog.poll(OFFLINE) == 'restart'
    assert f.calls == [('start',)]


def test_deliberate_stop_is_not_restarted(tmp_path):
    f = Fake(tmp_path)
    f.dog.poll(ONLINE_OWNED)
    f.record = False  # supervisor.stop() removes the record
    f.alive = False
    for _ in range(10):
        f.wait(60)
        assert f.dog.poll(OFFLINE) in ('stopped', 'idle')
    assert f.calls == [] and f.messages == []


def test_expect_stopped_suppresses_restart_even_if_record_remains(tmp_path):
    f = Fake(tmp_path)
    f.dog.poll(ONLINE_OWNED)
    f.dog.expect_stopped()
    assert f.dog.poll(ONLINE_OWNED) == 'stopping'
    f.crash(); f.wait(100); f.dog.poll(OFFLINE)
    assert f.calls == []
    assert f.dog.poll(ONLINE_OWNED) == 'ok'  # a later Station start is watched again


def test_external_server_is_never_touched(tmp_path):
    f = Fake(tmp_path)
    f.record = False
    for _ in range(5):
        assert f.dog.poll(EXTERNAL) == 'external'
    f.record = True  # stale record from an earlier session
    f.alive = False
    for _ in range(10):
        f.wait(60)
        assert f.dog.poll(OFFLINE) == 'idle'
    assert f.calls == [] and f.messages == []


def test_stale_record_at_startup_is_ignored(tmp_path):
    f = Fake(tmp_path)
    f.alive = False
    for _ in range(10):
        f.wait(60)
        assert f.dog.poll(OFFLINE) == 'idle'
    assert f.calls == []


def test_restart_limit_and_backoff(tmp_path):
    f = Fake(tmp_path)
    f.result = {'Success': False, 'Message': 'port busy'}
    f.dog.poll(ONLINE_OWNED)
    f.crash()
    f.wait(9)
    assert f.dog.poll(OFFLINE) == 'backoff'
    f.wait(1)
    assert f.dog.poll(OFFLINE) == 'restart'
    f.wait(29)
    assert f.dog.poll(OFFLINE) == 'backoff'
    f.wait(1)
    assert f.dog.poll(OFFLINE) == 'restart'
    f.wait(59)
    assert f.dog.poll(OFFLINE) == 'backoff'
    f.wait(1)
    assert f.dog.poll(OFFLINE) == 'restart'
    assert len(f.calls) == 3
    f.wait(60)
    assert f.dog.poll(OFFLINE) == 'gave_up'
    assert 'Автоперезапуск прекращён' in f.messages[-1]
    f.wait(1000)
    assert f.dog.poll(OFFLINE) == 'idle'
    assert len(f.calls) == 3
    assert [e['event'] for e in f.dog.history()] == ['crash', 'restart_failed', 'restart_failed', 'restart_failed', 'gave_up']


def test_successful_restarts_still_limited_within_window(tmp_path):
    f = Fake(tmp_path)
    for _ in range(3):
        f.alive = True
        f.dog.poll(ONLINE_OWNED)
        f.crash(); f.wait(60)
        assert f.dog.poll(OFFLINE) == 'restart'
    f.alive = True
    f.dog.poll(ONLINE_OWNED)
    assert f.crash()[-1] == 'gave_up'
    assert len(f.calls) == 3
    f.wait(700)  # old restarts leave the window once the user starts the server again
    f.alive = True
    f.dog.poll(ONLINE_OWNED)
    f.crash(); f.wait(10)
    assert f.dog.poll(OFFLINE) == 'restart'


def test_disabled_does_nothing(tmp_path):
    f = Fake(tmp_path)
    f.enabled = False
    assert f.dog.poll(ONLINE_OWNED) == 'disabled'
    f.alive = False
    for _ in range(10):
        f.wait(60)
        assert f.dog.poll(OFFLINE) == 'disabled'
    assert f.calls == [] and f.messages == [] and not (tmp_path / 'logs' / 'watchdog.jsonl').exists()


def test_waits_while_station_is_busy(tmp_path):
    f = Fake(tmp_path)
    f.dog.poll(ONLINE_OWNED)
    f.crash(); f.wait(10)
    f.busy = True
    assert f.dog.poll(OFFLINE) == 'busy'
    assert f.calls == []
    f.busy = False
    assert f.dog.poll(OFFLINE) == 'restart'


def test_hung_process_is_stopped_then_restarted(tmp_path):
    f = Fake(tmp_path)
    f.dog.poll(ONLINE_OWNED)
    states = [f.dog.poll(OFFLINE) for _ in range(5)]
    assert states == ['suspect'] * 4 + ['backoff']
    f.wait(10)
    assert f.dog.poll(OFFLINE) == 'restart'
    assert f.calls == [('stop',), ('load', 'qwen')]


def test_restart_runs_in_background_and_is_not_repeated(tmp_path):
    f = Fake(tmp_path)
    pending = []
    f.dog.spawn = pending.append
    f.dog.poll(ONLINE_OWNED)
    f.crash(); f.wait(10)
    assert f.dog.poll(OFFLINE) == 'restart'
    assert f.dog.poll(OFFLINE) == 'restarting' and f.calls == []
    pending[0]()
    assert f.calls == [('load', 'qwen')]


def test_history_is_capped(tmp_path):
    f = Fake(tmp_path, history_limit=5)
    for i in range(12):
        f.dog._event('crash', f'message {i}', notify=False)
    lines = (tmp_path / 'logs' / 'watchdog.jsonl').read_text(encoding='utf-8').splitlines()
    assert len(lines) == 5 and json.loads(lines[-1])['message'] == 'message 11'
