"""Offline smoke of evolving requirements and failover. No models/GPU/servers.

Run from source: python tools/coordination_smoke.py
Temporary synthetic data is removed on exit; no installed Station settings touched.
"""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.coordination import JournalStore, StaleAttempt, StaleRevision


def run(root: Path) -> dict:
    store = JournalStore(root / 'coordinator.sqlite3')
    store.open_project('demo', root / 'workspace')
    store.append_user('demo', source='test-ui', message_id='1', text='Implement storage using SQLite.')
    store.create_task('demo', 'implementation', 'Prepare a storage patch')
    old = store.start_attempt('implementation', 'primary', write_access=True)

    def deliver(attempt):
        # Synthetic budget only. A live adapter must count actual template and images.
        packet = store.prepare_delivery(attempt, token_count=lambda p: 100,
                                        context_limit=1000, reserve_tokens=100)
        store.acknowledge_delivery(attempt, packet['delivery_id'], packet['digest'])
        return packet

    deliver(old)
    update = store.append_user('demo', source='test-ui', message_id='2',
                                text='Keep the existing database. Do not delete any records.')
    store.record_summary('demo', 'Implement storage.', through_seq=update['seq'])
    blocked_revision = False
    try:
        store.begin_action(old, 'write-old-plan', 'write_file', {}, mutating=True)
    except StaleRevision:
        blocked_revision = True
    new = store.start_attempt('implementation', 'fallback', write_access=True, replace=True)
    delivered = deliver(new)
    blocked_attempt = False
    try:
        store.begin_action(old, 'late-tool', 'write_file', {}, mutating=True)
    except StaleAttempt:
        blocked_attempt = True
    store.finish_attempt(new, 'Proposal ready for review; no files changed in this smoke.')
    correction_present = any(event['payload']['text'] == update['payload']['text']
                             for event in delivered['packet']['user_events'])
    assert blocked_revision and blocked_attempt and correction_present
    return {'status': 'PASS', 'synthetic_only': True, 'live_agents_tested': False,
            'correction_preserved_despite_incomplete_summary': correction_present,
            'stale_revision_blocked': blocked_revision, 'stale_attempt_blocked': blocked_attempt,
            'final_status': store.task_status('implementation')['status']}


if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='laas-coordination-smoke-') as directory:
        print(json.dumps(run(Path(directory)), indent=2))
