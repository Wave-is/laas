"""Offline M2a HTTP Guard smoke. Does not run Qwen, a model or any OS tool."""
from pathlib import Path
import http.client
import json
import sys
import tempfile
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.coordination.qwen import QwenJournal, QwenToolGuard, ToolRule
from src.coordination.qwen_http import running_tool_guard


def main():
    with tempfile.TemporaryDirectory() as root:
        store = QwenJournal(Path(root) / 'private' / 'journal.sqlite3')
        store.open_project('p', Path(root) / 'repo')
        original = store.capture('p', runtime_id='synthetic-boot', session_id='synthetic-session',
                                 message_id='m1', text='Implement import without deleting the database.')
        store.create_task('p', 't', 'Inspect import')
        attempt = store.start_attempt('t', 'synthetic-worker', write_access=True)
        packet = store.prepare_delivery(attempt, token_count=lambda _: 50,
                                        context_limit=1000, reserve_tokens=100)
        # A synthetic trusted observer in this smoke only. A real integration must
        # prove delivery of the full packet; HTTP 202 is never that proof.
        store.bind_delivered_prompt(runtime_id='synthetic-boot', session_id='synthetic-session',
            prompt_id='synthetic-prompt', attempt_id=attempt,
            delivery_id=packet['delivery_id'], digest=packet['digest'])
        guard = QwenToolGuard(store, 'synthetic-boot', {
            'read_file': ToolRule(False, lambda args: args == {'path': 'config.json'})})
        request = {'protocolVersion': 1, 'requestId': 'permit-1', 'sessionId': 'synthetic-session',
                   'promptId': 'synthetic-prompt', 'toolCallId': 'tool-1', 'toolName': 'read_file',
                   'arguments': {'path': 'config.json'}}
        with running_tool_guard(guard, 'synthetic-only-token') as origin:
            def post(path, body):
                conn = http.client.HTTPConnection('127.0.0.1', urlsplit(origin).port, timeout=3)
                try:
                    conn.request('POST', path, json.dumps(body), headers={
                        'Authorization': 'Bearer synthetic-only-token', 'Content-Type': 'application/json'})
                    response = conn.getresponse()
                    assert response.status == 200
                    return json.loads(response.read())
                finally:
                    conn.close()
            handshake = post('/v1/handshake', {'protocolVersion': 1, 'nonce': 'smoke', 'client': 'qwen-code'})
            assert handshake['nonce'] == 'smoke'
            assert post('/v1/prepare', request)['allowed'] is True
            guard.observe_result(session_id='synthetic-session', prompt_id='synthetic-prompt',
                                 tool_call_id='tool-1', success=True, result={'synthetic': True})
            edit = store.capture('p', runtime_id='synthetic-boot', session_id='synthetic-session',
                                 message_id='m2', edit_of=original['event_id'],
                                 text='Also preserve the existing schema.', intent='steer')
            store.record_summary('p', 'Implement import.', through_seq=edit['seq'])
            request.update(requestId='permit-2', toolCallId='tool-2')
            assert post('/v1/prepare', request)['allowed'] is False
        reopened = QwenJournal(store.path)
        assert reopened.input_status(edit['event_id'])['state'] == 'queued'
        assert reopened.task_status('t')['needs_delivery'] is True
        print(json.dumps({'status': 'PASS', 'synthetic_only': True, 'live_qwen_tested': False,
            'http_guard_handshake': True, 'permit_persisted': True,
            'mid_turn_edit_retained': True, 'old_revision_denied': True,
            'automatic_failover_enabled': False}, indent=2))


if __name__ == '__main__':
    main()
