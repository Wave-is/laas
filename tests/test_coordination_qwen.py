"""Synthetic SQLite/loopback contract tests, not a live Qwen or GPU acceptance."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import base64
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
import threading
from urllib.parse import urlsplit

import pytest

from src.coordination import Busy, IdempotencyConflict, JournalError, StaleRevision
from src.coordination.qwen import ProtocolError, QwenJournal, QwenToolGuard, ToolRule
from src.coordination.qwen_http import QwenDaemonClient, running_tool_guard


@pytest.fixture
def store(tmp_path):
    value = QwenJournal(tmp_path / 'private' / 'journal.sqlite3', clock=lambda: 1000)
    value.open_project('p', tmp_path / 'workspace')
    return value


def capture(store, key='m1', **kwargs):
    args = dict(runtime_id='boot-1', session_id='session-1', message_id=key, text='  Не удалять базу.\n')
    args.update(kwargs)
    return store.capture('p', **args)


def bound(store, write=True):
    capture(store)
    store.create_task('p', 'task', 'Keep the database')
    attempt = store.start_attempt('task', 'qwen', write_access=write, ttl=120)
    delivery = store.prepare_delivery(attempt, token_count=lambda p: 30, context_limit=128, reserve_tokens=16)
    store.bind_delivered_prompt(runtime_id='boot-1', session_id='session-1', prompt_id='prompt-1',
                                attempt_id=attempt, delivery_id=delivery['delivery_id'], digest=delivery['digest'])
    return attempt, delivery


def guard(store, write=True):
    attempt, delivery = bound(store, write=write)
    return QwenToolGuard(store, 'boot-1', {
        'read_file': ToolRule(False, lambda a: a == {'path': 'input.txt'}),
        'write_file': ToolRule(True, lambda a: a == {'path': 'output.txt'}),
    }), attempt, delivery


def request(**kwargs):
    value = dict(protocolVersion=1, requestId='r1', sessionId='session-1', promptId='prompt-1',
                 toolCallId='call-1', toolName='write_file', arguments={'path': 'output.txt'})
    value.update(kwargs)
    return value


def test_capture_is_durable_before_any_sender_and_keeps_exact_edits(store):
    first = capture(store)
    revised = capture(store, 'm2', text='\tТеперь сохранить оба файла.\r\n', edit_of=first['event_id'], intent='steer')
    reopened = QwenJournal(store.path, clock=lambda: 1000)
    assert reopened.input_status(first['event_id'])['payload']['text'] == '  Не удалять базу.\n'
    assert reopened.input_status(revised['event_id'])['payload']['text'] == '\tТеперь сохранить оба файла.\r\n'
    assert revised['revision'] == 2
    assert revised['state'] == 'queued'
    assert revised['payload']['edit_of'] == first['event_id']
    with pytest.raises(ProtocolError):
        reopened.prepare_request(revised['event_id'])


def test_queue_insert_failure_rolls_back_user_event_and_revision(store):
    with store._connection() as db:
        db.executescript("CREATE TRIGGER fail_input BEFORE INSERT ON qwen_inputs BEGIN SELECT RAISE(ABORT,'disk failure'); END;")
    with pytest.raises(sqlite3.DatabaseError):
        capture(store)
    assert store.events('p') == []
    with store._connection() as db:
        assert db.execute("SELECT revision FROM projects WHERE id='p'").fetchone()[0] == 0


def test_duplicate_capture_is_same_event_after_restart(store):
    first = capture(store)
    same = capture(QwenJournal(store.path, clock=lambda: 1000))
    assert same == first
    assert len(store.events('p')) == 1


@pytest.mark.parametrize('change', [{'text': 'different'}, {'intent': 'prompt'}, {'attachments': ['missing']}])
def test_conflicting_retry_refused(store, change):
    capture(store)
    with pytest.raises(JournalError):
        capture(store, **change)


def test_concurrent_capture_and_claim_are_single_admission(store):
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(lambda _: capture(store), range(12)))
    assert len({r['event_id'] for r in rows}) == 1
    def claim(_):
        try:
            return store.claim_input(rows[0]['event_id'])['state']
        except Busy:
            return 'blocked'
    with ThreadPoolExecutor(max_workers=6) as pool:
        states = list(pool.map(claim, range(12)))
    assert states.count('sending') == 1
    assert states.count('blocked') == 11


def test_attachment_bytes_survive_original_disappearance(store):
    data = b'PNG fixture\x00\xff'
    digest = store.put_attachment(data, 'image/png')
    entry = capture(store, text='', attachments=[digest])
    wire = QwenJournal(store.path).prepare_request(entry['event_id'])
    assert base64.b64decode(wire['prompt'][1]['data']) == data
    assert '_meta' not in wire


def test_unsupported_attachment_preserved_but_not_silently_dropped(store):
    digest = store.put_attachment(b'file body', 'application/octet-stream')
    entry = capture(store, attachments=[digest])
    with pytest.raises(ProtocolError):
        store.prepare_request(entry['event_id'])
    assert store.read_attachment(digest)[1] == b'file body'
    assert store.input_status(entry['event_id'])['state'] == 'queued'


@pytest.mark.parametrize('text', ['/fork', '  /settings', '\n/run'])
def test_slash_command_refused_before_runtime_but_preserved(store, text):
    entry = capture(store, text=text)
    with pytest.raises(ProtocolError):
        store.prepare_request(entry['event_id'])
    assert entry['payload']['text'] == text


def test_edit_cannot_cross_runtime_or_session(store):
    first = capture(store)
    for change in ({'session_id': 'other'}, {'runtime_id': 'boot-2'}):
        with pytest.raises(JournalError):
            capture(store, 'edit', edit_of=first['event_id'], **change)


def test_fifo_waits_for_terminal_observation_and_cancel_preserves_revision(store):
    first = capture(store)
    second = capture(store, 'm2', text='follow-up')
    with pytest.raises(Busy):
        store.claim_input(second['event_id'])
    store.claim_input(first['event_id'])
    store.record_admission(first['event_id'], state='accepted', prompt_id='prompt-1', last_event_id=0)
    with pytest.raises(Busy):
        store.claim_input(second['event_id'])
    store.record_terminal(first['event_id'], prompt_id='prompt-1', evidence_id='epoch-1:1', outcome='end_turn')
    store.cancel_queued(second['event_id'])
    assert store.input_status(second['event_id'])['state'] == 'cancelled'
    assert [e['revision'] for e in store.events('p') if e['kind'].startswith('user.')] == [1, 2]


def test_terminal_is_correlated_idempotent_and_not_implicit_tool_result(store):
    entry = capture(store)
    store.claim_input(entry['event_id'])
    store.record_admission(entry['event_id'], state='accepted', prompt_id='prompt-1', last_event_id=0)
    with pytest.raises(JournalError):
        store.record_terminal(entry['event_id'], prompt_id='wrong', evidence_id='e:1', outcome='end_turn')
    for _ in range(2):
        store.record_terminal(entry['event_id'], prompt_id='prompt-1', evidence_id='e:1', outcome='end_turn')
    with pytest.raises(IdempotencyConflict):
        store.record_terminal(entry['event_id'], prompt_id='prompt-1', evidence_id='e:2', outcome='error')


def test_sending_after_crash_blocks_retry_without_operator_proof(store):
    entry = capture(store)
    store.claim_input(entry['event_id'])
    reopened = QwenJournal(store.path)
    with pytest.raises(Busy):
        reopened.claim_input(entry['event_id'])
    with pytest.raises(ValueError):
        reopened.reconcile_input(entry['event_id'], note='not sure', expected_revision=1)
    with pytest.raises(StaleRevision):
        reopened.reconcile_input(entry['event_id'], note='proven absent', expected_revision=0, definitely_not_admitted=True)
    reopened.reconcile_input(entry['event_id'], note='Verified daemon never received it',
                             expected_revision=1, definitely_not_admitted=True)
    assert reopened.claim_input(entry['event_id'])['state'] == 'sending'


@contextmanager
def daemon_fixture(store, event_id, *, status=202, reply=None, features=None, callback=None):
    """A synthetic local HTTP protocol peer, NOT Qwen Code."""
    received = []
    features = features if features is not None else [
        'session_prompt', 'session_events', 'non_blocking_prompt', 'external_tool_guard']
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass
        def respond(self, code, value):
            wire = json.dumps(value).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)
        def do_GET(self):
            assert self.path == '/capabilities'
            self.respond(200, {'features': features})
        def do_POST(self):
            assert store.input_status(event_id)['state'] == 'sending'
            assert any(e['event_id'] == event_id for e in store.events('p'))
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, body, self.headers.get('Authorization')))
            if callback:
                callback()
            if status == 0:
                self.close_connection = True
                return
            self.respond(status, reply if reply is not None else {'promptId': 'prompt-1', 'lastEventId': 4})
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port), received
    finally:
        server.shutdown(); server.server_close(); thread.join(2)


def client(origin, **kwargs):
    return QwenDaemonClient(origin, runtime_id='boot-1', token='synthetic-test-token', **kwargs)


def test_real_http_posts_only_after_commit_202_does_not_ack_delivery(store):
    entry = capture(store)
    store.create_task('p', 'task', 'test')
    attempt = store.start_attempt('task', 'qwen')
    with daemon_fixture(store, entry['event_id']) as (origin, received):
        result = client(origin).dispatch(store, entry['event_id'])
    assert result['state'] == 'accepted'
    assert received[0][0] == '/session/session-1/prompt'
    assert received[0][1]['prompt'][0]['text'] == entry['payload']['text']
    assert received[0][1]['_meta']['qwen.submittedPrompt'] == entry['payload']['text']
    assert store.task_status('task')['attempt']['confirmed_revision'] == -1
    with pytest.raises(StaleRevision):
        store.begin_action(attempt, 'c1', 'read_file', {}, mutating=False)


@pytest.mark.parametrize('status,reply', [(0, None), (500, None), (503, None), (307, None),
                                         (202, {}), (202, {'promptId': 'p', 'lastEventId': True})])
def test_ambiguous_http_not_replayed(store, status, reply):
    entry = capture(store)
    with daemon_fixture(store, entry['event_id'], status=status, reply=reply) as (origin, received):
        result = client(origin).dispatch(store, entry['event_id'])
        assert result['state'] == 'uncertain'
        with pytest.raises(Busy):
            client(origin).dispatch(store, entry['event_id'])
        assert len(received) == 1


@pytest.mark.parametrize('status', [400, 401, 403, 404, 413, 415])
def test_explicit_rejection_never_retried_automatically(store, status):
    entry = capture(store)
    with daemon_fixture(store, entry['event_id'], status=status) as (origin, received):
        assert client(origin).dispatch(store, entry['event_id'])['state'] == 'rejected'
        with pytest.raises(Busy):
            client(origin).dispatch(store, entry['event_id'])
        assert len(received) == 1


@pytest.mark.parametrize('features', [[], ['session_prompt'],
    {'session_prompt': True, 'non_blocking_prompt': True, 'session_events': True, 'external_tool_guard': True},
    ['session_prompt', 'non_blocking_prompt', 'session_events', {'external_tool_guard': True}]])
def test_capability_failure_keeps_input_queued_without_post(store, features):
    entry = capture(store)
    with daemon_fixture(store, entry['event_id'], features=features) as (origin, received):
        with pytest.raises(ProtocolError):
            client(origin).dispatch(store, entry['event_id'])
        assert received == []
    assert store.input_status(entry['event_id'])['state'] == 'queued'


def test_new_user_input_can_arrive_during_send_and_is_not_acknowledged(store):
    entry = capture(store)
    with daemon_fixture(store, entry['event_id'], callback=lambda: capture(store, 'm2', text='Do not write')) as (origin, _):
        result = client(origin).dispatch(store, entry['event_id'])
    assert result['revision'] == 1
    assert store.events('p')[1]['revision'] == 2
    assert len(store.pending_inputs('boot-1', 'session-1')) == 2


@pytest.mark.parametrize('origin', ['http://example.com:4170', 'http://0.0.0.0:4170',
    'http://127.0.0.1:4170/path', 'http://user:pass@127.0.0.1:4170',
    'http://127.0.0.1:4170?token=secret', 'http://127.0.0.1:4170#token', 'file:///tmp/foo'])
def test_no_network_redirect_proxy_or_nonloopback_targets(origin):
    with pytest.raises(ValueError):
        client(origin)


def test_uses_no_ambient_proxy(monkeypatch):
    monkeypatch.setenv('HTTP_PROXY', 'http://external.invalid:1234')
    c = client('http://localhost:4170')
    assert c.host == '127.0.0.1'


def test_guard_persists_before_allow_and_observes_result(store):
    g, attempt, _ = guard(store)
    assert g.prepare(request())['allowed'] is True
    assert len(store.task_status('task')['unresolved_actions']) == 1
    assert g.observe_result(session_id='session-1', prompt_id='prompt-1', tool_call_id='call-1',
                            success=True, result={'exit_code': 0}) == 'succeeded'
    assert store.task_status('task')['unresolved_actions'] == []
    assert g.prepare(request(requestId='r2', toolCallId='call-2'))['allowed'] is True


def test_correction_in_queue_blocks_next_tool_even_if_summary_omits_it(store):
    g, attempt, _ = guard(store)
    correction = capture(store, 'm2', text='No more file changes', intent='steer')
    store.record_summary('p', 'just implement everything', through_seq=correction['seq'])
    assert g.prepare(request())['allowed'] is False
    assert store.task_status('task')['unresolved_actions'] == []
    assert store.task_status('task')['needs_delivery'] is True


def test_late_attempt_and_late_result_cannot_recover_authority(store):
    g, attempt, _ = guard(store)
    assert g.prepare(request())['allowed'] is True
    store.cancel_attempt(attempt, reason='source offline')
    assert g.prepare(request(requestId='r2', toolCallId='call-2'))['allowed'] is False
    assert g.observe_result(session_id='session-1', prompt_id='prompt-1', tool_call_id='call-1',
                            success=True, result={'exit_code': 0}) == 'needs_review'


@pytest.mark.parametrize('change', [{}, {'requestId': 'another'},
    {'toolCallId': 'another'}, {'sessionId': 'unknown'}, {'promptId': 'unknown'}])
def test_guard_replay_unknown_tuple_is_refused(store, change):
    g, _, _ = guard(store)
    assert g.prepare(request())['allowed'] is True
    assert g.prepare(request(**change))['allowed'] is False


def test_read_only_attempt_cannot_write(store):
    g, _, _ = guard(store, write=False)
    assert g.prepare(request())['allowed'] is False
    assert g.prepare(request(requestId='r2', toolCallId='r2', toolName='read_file', arguments={'path': 'input.txt'}))['allowed'] is True


@pytest.mark.parametrize('tool,args', [('unknown', {}), ('monitor', {}), ('agent', {}),
    ('write_file', {'path': '../outside'}), ('write_file', {'path': 'output.txt', 'is_background': True})])
def test_unapproved_nested_background_or_bad_arguments_denied(store, tool, args):
    g, _, _ = guard(store)
    assert g.prepare(request(toolName=tool, arguments=args))['allowed'] is False


def test_guard_reservation_crash_never_becomes_second_allow(store, monkeypatch):
    g, _, _ = guard(store)
    original = store.begin_action
    monkeypatch.setattr(store, 'begin_action', lambda *a, **k: (_ for _ in ()).throw(OSError('secret failure')))
    assert g.prepare(request())['allowed'] is False
    monkeypatch.setattr(store, 'begin_action', original)
    assert g.prepare(request(requestId='r2'))['allowed'] is False
    assert store.task_status('task')['unresolved_actions'] == []


def test_revision_race_after_reservation_prevents_allow(store, monkeypatch):
    g, _, _ = guard(store)
    original = store.begin_action
    def changed(*a, **k):
        capture(store, 'm2', text='stop writes')
        return original(*a, **k)
    monkeypatch.setattr(store, 'begin_action', changed)
    assert g.prepare(request())['allowed'] is False


def test_binding_is_not_reassignable_and_stale_packet_refused(store):
    attempt, delivery = bound(store)
    capture(store, 'm2', text='new revision')
    with pytest.raises(StaleRevision):
        store.bind_delivered_prompt(runtime_id='boot-1', session_id='session-1', prompt_id='prompt-2',
            attempt_id=attempt, delivery_id=delivery['delivery_id'], digest=delivery['digest'])
    new = store.prepare_delivery(attempt, token_count=lambda p: 30, context_limit=128, reserve_tokens=16)
    with pytest.raises(IdempotencyConflict):
        store.bind_delivered_prompt(runtime_id='boot-1', session_id='session-1', prompt_id='prompt-1',
            attempt_id=attempt, delivery_id=new['delivery_id'], digest=new['digest'])


def post(origin, path, body, *, token='test-secret', headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', urlsplit(origin).port, timeout=5)
    wire = body if isinstance(body, bytes) else json.dumps(body).encode()
    values = {'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}
    values.update(headers or {})
    try:
        conn.request('POST', path, wire, values)
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_http_guard_auth_handshake_allow_deny_and_no_admin_routes(store):
    g, _, _ = guard(store)
    with running_tool_guard(g, 'test-secret') as origin:
        status, raw = post(origin, '/v1/handshake', {'protocolVersion': 1, 'client': 'qwen-code', 'nonce': 'nonce'})
        assert status == 200
        assert json.loads(raw) == {'protocolVersion': 1, 'nonce': 'nonce', 'capabilities': {'prepare': True}}
        assert json.loads(post(origin, '/v1/prepare', request())[1])['allowed'] is True
        assert json.loads(post(origin, '/v1/prepare', request())[1])['allowed'] is False
        assert post(origin, '/v1/prepare', request(), token='wrong')[0] == 401
        for path in ('/reconcile', '/bind', '/run', '/v1/prepare?token=test-secret'):
            assert post(origin, path, {})[0] == 404
        assert post(origin, '/v1/prepare', request(), headers={'Origin': 'https://evil.invalid'})[0] == 403
        assert post(origin, '/v1/prepare', request(), headers={'Host': 'evil.invalid'})[0] == 403


@pytest.mark.parametrize('body', [b'{"protocolVersion":1,"protocolVersion":1}', b'{"x":NaN}',
    b'[]', b'not JSON', b'\xff', b'{"protocolVersion":true,"nonce":"n","client":"qwen-code"}'])
def test_http_malformed_never_allows(store, body):
    g, _, _ = guard(store)
    with running_tool_guard(g, 'test-secret') as origin:
        status, raw = post(origin, '/v1/handshake', body)
        assert status == 400
        assert b'allowed": true' not in raw


def test_http_does_not_echo_secret_or_payload_errors(store, capsys):
    g, _, _ = guard(store)
    secret_text = 'super-secret-tool-argument'
    with running_tool_guard(g, 'test-secret') as origin:
        status, raw = post(origin, '/v1/prepare', request(arguments={'path': secret_text}))
        assert status == 200 and json.loads(raw)['allowed'] is False
        assert secret_text.encode() not in raw
    output = capsys.readouterr()
    assert secret_text not in output.out + output.err
    assert 'test-secret' not in output.out + output.err


def test_http_storage_failure_denies_without_allow(store, monkeypatch):
    g, _, _ = guard(store)
    monkeypatch.setattr(store, 'begin_action', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('full')))
    with running_tool_guard(g, 'test-secret') as origin:
        status, raw = post(origin, '/v1/prepare', request())
        assert status == 200 and json.loads(raw)['allowed'] is False


def test_extension_tables_do_not_break_m1_reader(store):
    from src.coordination import JournalStore
    capture(store)
    assert JournalStore(store.path).events('p')[0]['kind'] == 'user.message'


def test_adapter_facade_has_no_implicit_network_or_protection_claim(tmp_path, monkeypatch):
    from src.agents.qwen_code.adapter import QwenCodeAdapter
    monkeypatch.setattr(QwenDaemonClient, '_request', lambda *a, **k: pytest.fail('implicit network'))
    adapter = QwenCodeAdapter()
    assert adapter.get_capabilities().data['task_control'] is False
    assert adapter.get_coordination_capabilities().data['automatic_failover'] is False
    bridge = adapter.open_managed_input(journal_path=tmp_path / 'private' / 'journal.sqlite3',
        project_id='p', workspace=tmp_path / 'repo', runtime_id='boot', session_id='session',
        daemon_origin='http://127.0.0.1:4170', daemon_token='private')
    first = bridge.capture(message_id='user-1', text='Не удалять старую базу')
    edit = bridge.capture(message_id='user-2', text='И её схему не менять',
                          edit_of=first['event_id'])
    assert edit['revision'] == 2
    assert bridge.status()['counts'] == {'queued': 2}
    assert bridge.status()['coverage']['native_desktop_capture'] is False
    assert 'private' not in json.dumps(bridge.status())


def test_managed_journal_must_not_be_in_workspace(tmp_path):
    from src.agents.qwen_code.managed import ManagedQwenInput
    with pytest.raises(ValueError):
        ManagedQwenInput(journal_path=tmp_path / 'repo' / 'private.sqlite3',
            project_id='p', workspace=tmp_path / 'repo', runtime_id='boot', session_id='s',
            daemon_origin='http://127.0.0.1:4170', daemon_token='secret')
    assert not (tmp_path / 'repo').exists()


def test_managed_facade_validates_origin_before_creating_store(tmp_path):
    from src.agents.qwen_code.managed import ManagedQwenInput
    with pytest.raises(ValueError):
        ManagedQwenInput(journal_path=tmp_path / 'private' / 'store.sqlite3',
            project_id='p', workspace=tmp_path / 'repo', runtime_id='boot', session_id='s',
            daemon_origin='http://example.com', daemon_token='secret')
    assert not (tmp_path / 'private').exists()


def test_managed_facade_does_not_skip_inflight_item(tmp_path, monkeypatch):
    from src.agents.qwen_code.managed import ManagedQwenInput
    bridge = ManagedQwenInput(journal_path=tmp_path / 'private' / 'store.sqlite3',
        project_id='p', workspace=tmp_path / 'repo', runtime_id='boot', session_id='s',
        daemon_origin='http://127.0.0.1:4170', daemon_token='secret')
    first = bridge.capture(message_id='1', text='one')
    bridge.capture(message_id='2', text='two')
    bridge.store.claim_input(first['event_id'])
    seen = []
    def dispatch(store, event_id):
        seen.append(event_id)
        return store.claim_input(event_id)
    monkeypatch.setattr(bridge.client, 'dispatch', dispatch)
    with pytest.raises(Busy):
        bridge.dispatch_next()
    assert seen == [first['event_id']]


def test_managed_facade_empty_queue_is_noop(tmp_path, monkeypatch):
    from src.agents.qwen_code.managed import ManagedQwenInput
    bridge = ManagedQwenInput(journal_path=tmp_path / 'private' / 'store.sqlite3',
        project_id='p', workspace=tmp_path / 'repo', runtime_id='boot', session_id='s',
        daemon_origin='http://127.0.0.1:4170', daemon_token='secret')
    monkeypatch.setattr(bridge.client, '_request', lambda *a, **k: pytest.fail('empty dispatch'))
    assert bridge.dispatch_next() is None


def test_post_admission_storage_failure_never_repeats_post(store, monkeypatch):
    entry = capture(store)
    calls = []
    client = QwenDaemonClient('http://127.0.0.1:4170', runtime_id='boot-1', token='secret')
    monkeypatch.setattr(client, 'capabilities', lambda: {})
    def request(method, path, body=None):
        calls.append(method)
        return 202, {'promptId': 'p', 'lastEventId': 0}
    monkeypatch.setattr(client, '_request', request)
    monkeypatch.setattr(store, 'record_admission', lambda *a, **k: (_ for _ in ()).throw(sqlite3.OperationalError('disk full')))
    with pytest.raises(sqlite3.OperationalError):
        client.dispatch(store, entry['event_id'])
    assert calls == ['POST']
    assert store.input_status(entry['event_id'])['state'] == 'sending'
    with pytest.raises(Busy):
        client.dispatch(store, entry['event_id'])
    assert calls == ['POST']


def test_queued_cancellation_is_not_a_user_retraction(store):
    first = capture(store, text='Do not modify schema')
    store.cancel_queued(first['event_id'])
    store.create_task('p', 't', 'Inspect')
    attempt = store.start_attempt('t', 'w')
    packet = store.prepare_delivery(attempt, token_count=lambda _: 10,
                                    context_limit=100, reserve_tokens=10)
    assert packet['packet']['user_events'][0]['payload']['text'] == 'Do not modify schema'
    assert packet['packet']['revision'] == 1


def test_zero_port_is_not_silently_rewritten_to_eighty():
    with pytest.raises(ValueError):
        client('http://127.0.0.1:0')


def test_tool_policy_cannot_rewrite_persisted_final_arguments(store):
    g, _, _ = guard(store)
    def validate(args):
        args['path'] = 'changed-by-validator'
        return True
    g.rules['write_file'] = ToolRule(True, validate)
    body = request()
    assert g.prepare(body)['allowed'] is True
    assert body['arguments']['path'] == 'output.txt'
    with store._connection() as db:
        saved = json.loads(db.execute('SELECT arguments FROM actions').fetchone()[0])
    assert saved['path'] == 'output.txt'
