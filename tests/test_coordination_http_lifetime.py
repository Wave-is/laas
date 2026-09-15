"""Real loopback transport regressions; no installed agent or remote model involved."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import sqlite3
import threading
import time

import pytest

from src.coordination.http_lifetime import RequestInterrupted, bounded_response
from src.coordination.qwen import Busy, ProtocolError, QwenJournal
from src.coordination.qwen_events import QwenEventObserver
from src.coordination.qwen_http import QwenDaemonClient
from src.coordination.qwen_stream import QwenEventClient


FEATURES = {'features': ['session_prompt', 'non_blocking_prompt', 'session_events', 'external_tool_guard']}
REPLAY = b'data: {"v":1,"type":"replay_complete","data":{"replayedCount":0}}\n\n'
EVENT = b'id: 1\ndata: {"v":1,"id":1,"type":"session_update","data":{"sessionUpdate":"agent_message_chunk"}}\n\n'


@contextmanager
def peer(stage='idle', *, sent=None, release=None):
    """A bounded fixture: no teardown waits for the client to finish a request."""
    sent = sent or threading.Event()
    release = release or threading.Event()
    requests, sockets = [], []
    sockets_lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def setup(self):
            super().setup()
            self.connection.settimeout(2)
            with sockets_lock:
                sockets.append(self.connection)

        def log_message(self, *args):
            pass

        def do_GET(self):
            requests.append(self.path)
            prefix = 'cap' if self.path == '/capabilities' else 'stream'
            if stage == prefix + '-headers':
                sent.set()
                release.wait(3)
                self.close_connection = True
                return
            self.send_response(200)
            self.send_header('Content-Type', 'application/json' if prefix == 'cap' else 'text/event-stream')
            if prefix == 'stream':
                self.send_header('X-Qwen-Event-Epoch', 'epoch-1')
                if stage == 'connection-close':
                    self.send_header('Connection', 'close')
            if prefix == 'cap' and stage not in ('cap-body', 'cap-trickle'):
                body = json.dumps(FEATURES).encode()
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.end_headers()
            if stage in ('cap-body', 'cap-trickle'):
                self.wfile.write(b'{"features":[')
            else:
                self.wfile.write(REPLAY)
                if stage in ('events', 'connection-close'):
                    self.wfile.write(EVENT)
                elif stage == 'partial':
                    self.wfile.write(b'id: 1\ndata: {"v":1')
            self.wfile.flush()
            sent.set()
            if stage in ('heartbeat', 'cap-trickle'):
                for _ in range(150):
                    if release.wait(0.02):
                        return
                    self.wfile.write(b' ' if stage == 'cap-trickle' else b': keepalive\n\n')
                    self.wfile.flush()
            else:
                release.wait(3)
            self.close_connection = True

        def do_POST(self):
            requests.append(self.path)
            self.rfile.read(int(self.headers.get('Content-Length', '0')))
            sent.set()
            release.wait(3)
            self.close_connection = True

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        block_on_close = False

        def handle_error(self, *args):
            pass  # expected disconnect; do not echo headers or bearer tokens

    server = Server(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', requests
    finally:
        release.set()
        with sockets_lock:
            for connection in sockets:
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
        server.shutdown()
        server.server_close()
        thread.join(2)
        assert not thread.is_alive(), 'Fixture HTTP accept thread did not stop'


@pytest.fixture
def binding(tmp_path):
    store = QwenJournal(tmp_path / 'journal.db')
    store.open_project('p', tmp_path / 'workspace')
    observer = QwenEventObserver(store, project_id='p', runtime_id='boot-1', session_id='s')
    return store, observer


def receiver(observer, origin):
    return QwenEventClient(QwenDaemonClient(origin, runtime_id='boot-1', token='fixture-secret', timeout=1.5), observer)


@pytest.mark.parametrize('stage', ['cap-headers', 'cap-body', 'cap-trickle', 'stream-headers', 'idle', 'partial', 'heartbeat'])
@pytest.mark.parametrize('cause', ['deadline', 'stop'])
def test_entire_subscription_is_bounded_including_preflight(binding, stage, cause):
    store, observer = binding
    stop, sent = threading.Event(), threading.Event()
    with peer(stage, sent=sent) as (origin, requests):
        def cancel_when_reading():
            if sent.wait(1):
                stop.set()
        timer = threading.Thread(target=cancel_when_reading, daemon=True) if cause == 'stop' else None
        if timer:
            timer.start()
        started = time.monotonic()
        receiver(observer, origin).receive_once(stop=stop, max_seconds=0.25 if cause == 'deadline' else 2)
        elapsed = time.monotonic() - started
        if timer:
            timer.join(1)
        assert elapsed < 1.0, f'{stage}/{cause} exceeded cancellation budget: {elapsed:.2f}s'
    assert observer.status()['state'] == 'disconnected'
    assert observer.status()['cursor'] == 0
    assert all(path == '/capabilities' or path.endswith('/events') for path in requests)
    with pytest.raises(Busy):
        store.check_observation('boot-1', 's')


@pytest.mark.parametrize('stage', ['events', 'connection-close'])
def test_partial_http_response_is_closed_on_event_limit(binding, monkeypatch, stage):
    _, observer = binding
    responses = []
    original = http.client.HTTPConnection.getresponse
    def track(connection):
        response = original(connection)
        responses.append(response)  # prevent GC from hiding an unclosed body
        return response
    monkeypatch.setattr(http.client.HTTPConnection, 'getresponse', track)
    with peer(stage) as (origin, _):
        assert receiver(observer, origin).receive_once(max_events=2, max_seconds=1) == 2
        assert len(responses) == 2
        assert all(response.isclosed() for response in responses)
    assert observer.status()['cursor'] == 1


@pytest.mark.parametrize('error', [sqlite3.DatabaseError, ProtocolError, RuntimeError, OSError])
def test_stop_cannot_swallow_concurrent_storage_or_protocol_failure(binding, monkeypatch, error):
    _, observer = binding
    stop = threading.Event()
    original = observer.ingest
    def fault(event):
        if event.get('id') == 1:
            stop.set()
            raise error('fixture write/protocol failed')
        return original(event)
    monkeypatch.setattr(observer, 'ingest', fault)
    with peer('events') as (origin, _):
        with pytest.raises(error):
            receiver(observer, origin).receive_once(stop=stop, max_seconds=1)
    assert observer.status()['state'] == 'resync_required'
    assert observer.status()['cursor'] == 0


def test_cancel_before_socket_attachment_sends_no_http_request(binding, monkeypatch):
    _, observer = binding
    stop = threading.Event()
    original = http.client.HTTPConnection.connect
    def connect_then_cancel(connection):
        original(connection)
        stop.set()
    monkeypatch.setattr(http.client.HTTPConnection, 'connect', connect_then_cancel)
    with peer() as (origin, requests):
        assert receiver(observer, origin).receive_once(stop=stop, max_seconds=1) == 0
    assert requests == []
    assert observer.status()['state'] == 'disconnected'


def test_cancelled_prompt_post_remains_uncertain_not_retried(tmp_path):
    store = QwenJournal(tmp_path / 'journal.db')
    store.open_project('p', tmp_path / 'workspace')
    item = store.capture('p', runtime_id='boot-1', session_id='s', message_id='u', text='exact text')
    with peer() as (origin, requests):
        daemon = QwenDaemonClient(origin, runtime_id='boot-1', token='fixture-secret', timeout=0.2)
        assert daemon.dispatch(store, item['event_id'])['state'] == 'uncertain'
        with pytest.raises(Busy):
            daemon.dispatch(store, item['event_id'])
    assert requests.count('/session/s/prompt') == 1
    assert store.input_status(item['event_id'])['state'] == 'uncertain'


def test_reconnect_backoff_does_not_extend_total_deadline(binding):
    _, observer = binding
    with peer('cap-headers') as (origin, requests):
        started = time.monotonic()
        result = receiver(observer, origin).run(stop=threading.Event(), max_reconnects=10, max_seconds=0.2)
        assert time.monotonic() - started < 0.7
    assert result['connections'] == 1
    assert requests == ['/capabilities']
    assert observer.status()['state'] == 'disconnected'


def test_repeated_stops_leave_no_owned_watchdog_threads(binding):
    _, observer = binding
    before = {thread.ident for thread in threading.enumerate() if thread.name == 'laas-qwen-http-watchdog'}
    for _ in range(8):
        with peer('events') as (origin, _):
            receiver(observer, origin).receive_once(max_events=1, max_seconds=1)
    after = {thread.ident for thread in threading.enumerate() if thread.name == 'laas-qwen-http-watchdog'}
    assert after == before


@pytest.mark.parametrize('seconds', [True, False, 0, -1, float('inf'), float('nan')])
def test_bad_subscription_budget_rejected_before_io(binding, seconds):
    _, observer = binding
    client = receiver(observer, 'http://127.0.0.1:1')
    with pytest.raises(ValueError):
        client.receive_once(max_seconds=seconds)


@pytest.mark.parametrize('deadline', [float('nan'), float('inf'), True])
def test_transport_rejects_invalid_deadline_without_thread(deadline):
    with pytest.raises(ValueError):
        with bounded_response('127.0.0.1', 1, 'GET', '/', headers={}, timeout=1, deadline=deadline):
            pytest.fail('unreachable')


def test_expired_transport_never_connects(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail('Expired request attempted to connect')
    monkeypatch.setattr(http.client.HTTPConnection, 'connect', unexpected)
    with pytest.raises(RequestInterrupted):
        with bounded_response('127.0.0.1', 1, 'GET', '/', headers={}, timeout=1, deadline=time.monotonic()-1):
            pytest.fail('unreachable')
