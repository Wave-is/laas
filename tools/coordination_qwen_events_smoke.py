"""Synthetic durable SSE smoke; does NOT contact Qwen, a model or user services."""
from pathlib import Path
import json
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.coordination.qwen import QwenJournal, QwenToolGuard, ToolRule
from src.coordination.qwen_events import QwenEventObserver, ObservationLost


def main():
    with tempfile.TemporaryDirectory(prefix='laas-events-') as folder:
        store = QwenJournal(Path(folder) / 'private/journal.db')
        store.open_project('project', Path(folder) / 'work')
        observer = QwenEventObserver(store, project_id='project', runtime_id='synthetic-boot', session_id='session')
        observer.connect('synthetic-epoch')
        observer.ingest({'v': 1, 'type': 'replay_complete', 'data': {'replayedCount': 0}})
        entry = store.capture('project', runtime_id='synthetic-boot', session_id='session',
                              message_id='user-1', text='Preserve the existing database.')
        store.claim_input(entry['event_id'])
        store.record_admission(entry['event_id'], state='accepted', prompt_id='prompt', last_event_id=0)
        store.create_task('project', 'task', 'Synthetic write task')
        attempt = store.start_attempt('task', 'synthetic-worker', write_access=True)
        delivery = store.prepare_delivery(attempt, token_count=lambda p: 20, context_limit=128, reserve_tokens=16)
        # Deliberate SYNTHETIC transport acknowledgement, never claimed as live.
        store.bind_delivered_prompt(runtime_id='synthetic-boot', session_id='session', prompt_id='prompt',
            attempt_id=attempt, delivery_id=delivery['delivery_id'], digest=delivery['digest'])
        guard = QwenToolGuard(store, 'synthetic-boot', {'write_file': ToolRule(True, lambda a: a == {'path': 'fixture'})})
        request = {'protocolVersion': 1, 'requestId': 'r', 'sessionId': 'session', 'promptId': 'prompt',
                   'toolCallId': 'call', 'toolName': 'write_file', 'arguments': {'path': 'fixture'}}
        assert guard.prepare(request)['allowed']
        # Correction arrives after permission but before the simulated tool settles.
        store.capture('project', runtime_id='synthetic-boot', session_id='session', message_id='user-2',
                      text='Do not change the schema.', intent='steer')
        result = {'id': 1, 'v': 1, 'type': 'session_update', 'promptId': 'prompt', 'data': {
            'sessionUpdate': 'tool_call_update', 'toolCallId': 'call', 'status': 'completed', 'rawOutput': 'fixture'}}
        assert observer.ingest(result) == 'tool_needs_review'
        observer.disconnect()
        reopened = QwenEventObserver(QwenJournal(store.path), project_id='project',
                                     runtime_id='synthetic-boot', session_id='session')
        reopened.connect('synthetic-epoch')
        assert reopened.status()['cursor'] == 1
        assert reopened.ingest(result) == 'duplicate'
        try:
            reopened.ingest({'id': 3, 'v': 1, 'type': 'turn_complete',
                             'data': {'promptId': 'prompt', 'stopReason': 'end_turn'}})
        except ObservationLost:
            pass
        else:
            raise AssertionError('Gap was incorrectly accepted')
        assert reopened.status()['cursor'] == 1
        assert not guard.prepare(dict(request, requestId='r2', toolCallId='call2'))['allowed']
        print(json.dumps({'synthetic': True, 'live_qwen_verified': False,
            'durable_cursor_and_replay': 'PASS', 'late_result_requires_review': 'PASS',
            'gap_denies_tools': 'PASS', 'automatic_failover': False}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
