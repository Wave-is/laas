"""Durable Qwen SSE observation; output evidence never becomes human input.

Opt-in and fail-closed. An epoch/cursor pair belongs to one daemon lifetime and
session. Replay gaps cannot be healed by a model summary or a reconnect alone.
No tool, process, prompt, permission response or GPU operation is issued here.
"""
from __future__ import annotations

import json
import re
from uuid import uuid4

from .qwen import ProtocolError, QwenJournal, identifier
from .store import Busy, IdempotencyConflict, JournalError, _json

MAX_FRAME_BYTES = 1024 * 1024
MAX_EVENT_ID = 2**53 - 1  # Qwen/JS safe integer


class ObservationLost(ProtocolError):
    """Evidence continuity is lost; do not auto-replay prompts or permit tools."""


class StreamInterrupted(ConnectionError):
    """Subscriber disconnected; reconnect may replay from the durable cursor."""


class StaleObserver(ProtocolError):
    """An older connection may not alter a newer observer's state."""


def validate_epoch(epoch: str) -> str:
    if not isinstance(epoch, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', epoch):
        raise ProtocolError('Missing or invalid Qwen event epoch')
    return epoch


def validate_event(value: dict) -> dict:
    if (not isinstance(value, dict) or type(value.get('v')) is not int or value['v'] != 1
            or not isinstance(value.get('data'), dict)):
        raise ProtocolError('Unsupported event envelope')
    identifier(value.get('type'))
    if 'id' in value and (type(value['id']) is not int or not 1 <= value['id'] <= MAX_EVENT_ID):
        raise ProtocolError('Invalid event cursor')
    for name in ('promptId', 'originatorClientId'):
        if name in value:
            identifier(value[name])
    for name in ('promptId', 'sessionId'):
        if name in value['data']:
            identifier(value['data'][name])
    if ('promptId' in value and 'promptId' in value['data']
            and value['promptId'] != value['data']['promptId']):
        raise ProtocolError('Conflicting prompt correlation')
    encoded = _json(value)
    if len(encoded.encode('utf-8')) > MAX_FRAME_BYTES:
        raise ProtocolError('Oversized event')
    return json.loads(encoded)  # caller cannot mutate stored evidence after validation


class QwenEventObserver:
    """Explicit session binding. Constructing it touches SQLite, never the network.

    Starting/restarting an observer denies new tool permits until replay_complete.
    A superseding connection fences late callbacks. This does not assert complete
    input capture or packet delivery; separate trusted delivery evidence is required.
    """
    _HARD_FAILURES = {
        'state_resync_required', 'history_truncated', 'session_recording_degraded',
        'session_died', 'session_closed', 'model_switched', 'model_switch_failed',
        'session_rewound',
    }
    _SOFT_FAILURES = {'client_evicted', 'stream_error'}
    _IDLESS = _HARD_FAILURES | _SOFT_FAILURES | {'replay_complete', 'slow_client_warning', 'session_snapshot'}

    def __init__(self, store: QwenJournal, *, project_id: str, runtime_id: str, session_id: str):
        self.store = store
        self.project_id = identifier(project_id)
        self.runtime_id = identifier(runtime_id)
        self.session_id = identifier(session_id)
        self.connection_id: str | None = None
        with store._transaction() as db:
            store._one(db, 'SELECT id FROM projects WHERE id=?', (project_id,))
            db.execute('INSERT OR IGNORE INTO qwen_stream_sessions(runtime_id,session_id,project_id) '
                       'VALUES(?,?,?)', (runtime_id, session_id, project_id))
            if self._row(db)['project_id'] != project_id:
                raise IdempotencyConflict('Observation session belongs to another project')

    def _row(self, db):
        return self.store._one(db, 'SELECT * FROM qwen_stream_sessions WHERE runtime_id=? AND session_id=?',
                               (self.runtime_id, self.session_id))

    def status(self) -> dict:
        with self.store._connection() as db:
            row = dict(self._row(db))
        return {k: row[k] for k in ('project_id', 'runtime_id', 'session_id', 'epoch',
                                    'cursor', 'state', 'last_seen', 'note')}

    def _owned(self, db):
        row = self._row(db)
        if not self.connection_id or row['connection_id'] != self.connection_id:
            raise StaleObserver('Observer connection was superseded')
        return row

    def _state(self, db, state, note=None):
        db.execute('UPDATE qwen_stream_sessions SET state=?,note=?,last_seen=? '
                   'WHERE runtime_id=? AND session_id=?',
                   (state, note, self.store._now(), self.runtime_id, self.session_id))

    def connect(self, epoch: str) -> dict:
        """Bind the HTTP response epoch BEFORE consuming any replay frame."""
        epoch = validate_epoch(epoch)
        conflict = False
        connection = str(uuid4())
        with self.store._transaction() as db:
            row = self._row(db)
            if row['state'] == 'resync_required':
                raise ObservationLost('Explicit reconciliation required; reconnect cannot clear a gap')
            if row['epoch'] is not None and row['epoch'] != epoch:
                self._state(db, 'resync_required', 'epoch_changed')
                conflict = True
            else:
                db.execute('UPDATE qwen_stream_sessions SET epoch=?,connection_id=?,state=?,note=NULL,last_seen=? '
                           'WHERE runtime_id=? AND session_id=?',
                           (epoch, connection, 'catching_up', self.store._now(), self.runtime_id, self.session_id))
        if conflict:
            raise ObservationLost('Daemon event epoch changed')
        self.connection_id = connection
        return self.status()

    def disconnect(self, *, fault=False) -> None:
        """Invalidate liveness without replacing a sticky loss-of-evidence marker."""
        with self.store._transaction() as db:
            row = self._row(db)
            if row['connection_id'] != self.connection_id and self.connection_id is not None:
                return
            if row['state'] != 'resync_required':
                self._state(db, 'resync_required' if fault else 'disconnected',
                            'protocol_or_storage_error' if fault else 'stream_disconnected')

    def heartbeat(self) -> None:
        with self.store._transaction() as db:
            row = self._owned(db)
            if row['state'] in ('live', 'catching_up'):
                db.execute('UPDATE qwen_stream_sessions SET last_seen=? WHERE runtime_id=? AND session_id=?',
                           (self.store._now(), self.runtime_id, self.session_id))

    def _evidence(self, db, envelope, source_id):
        source = 'qwen-sse:' + self.runtime_id + ':' + self.session_id
        # The source and ID are generated by trusted transport, not content roles.
        self.store._append(db, self.project_id, 'runtime.notice',
                           {'task_id': None, 'attempt_id': None, 'data': envelope},
                           source=source, source_id=source_id)

    def ingest(self, envelope: dict) -> str:
        """Commit the raw frame, cursor AND correlated projection atomically.

        Returns applied/evidence/duplicate/pending_admission; raises on discontinuity.
        No user revision is advanced by SSE, including user_message_chunk echoes.
        """
        try:
            event = validate_event(envelope)
            if event['data'].get('sessionId', self.session_id) != self.session_id:
                raise ProtocolError('Event belongs to another session')
            return self._ingest(event)
        except (StaleObserver, ObservationLost, StreamInterrupted):
            raise
        except Exception:
            self.disconnect(fault=True)
            raise

    def _ingest(self, event):
        failure = None
        result = 'evidence'
        with self.store._transaction() as db:
            row = self._owned(db)
            if row['state'] not in ('live', 'catching_up'):
                raise ObservationLost('Observer is not receiving a continuous stream')
            kind, data = event['type'], event['data']
            event_id = event.get('id')
            encoded = _json(event)
            if event_id is None:
                if kind not in self._IDLESS:
                    raise ProtocolError('Durable event has no cursor')
                if kind == 'replay_complete':
                    if type(data.get('replayedCount')) is not int or data['replayedCount'] < 0:
                        raise ProtocolError('Invalid replay completion')
                    self._state(db, 'live')
                elif kind in self._HARD_FAILURES:
                    self._state(db, 'resync_required', kind)
                    failure = kind
                elif kind in self._SOFT_FAILURES:
                    self._state(db, 'disconnected', kind)
                    failure = kind
                elif kind == 'session_snapshot' and data.get('recordingDegraded') is True:
                    self._state(db, 'resync_required', 'recording_degraded')
                    failure = 'recording_degraded'
                else:
                    db.execute('UPDATE qwen_stream_sessions SET last_seen=? WHERE runtime_id=? AND session_id=?',
                               (self.store._now(), self.runtime_id, self.session_id))
                self._evidence(db, event, str(uuid4()))
            else:
                old = db.execute('SELECT envelope,disposition FROM qwen_stream_frames WHERE runtime_id=? '
                    'AND session_id=? AND epoch=? AND event_id=?',
                    (self.runtime_id, self.session_id, row['epoch'], event_id)).fetchone()
                if old:
                    if old['envelope'] != encoded:
                        self._state(db, 'resync_required', 'conflicting_replay')
                        failure = 'conflicting_replay'
                    else:
                        result = 'duplicate'
                elif event_id != row['cursor'] + 1:
                    self._state(db, 'resync_required', 'cursor_gap')
                    failure = 'cursor_gap'
                    # Keep the rejected frame as evidence but never advance a cursor over it.
                    self._evidence(db, event, str(uuid4()))
                else:
                    self._evidence(db, event, f"{row['epoch']}:{event_id}")
                    db.execute('INSERT INTO qwen_stream_frames VALUES(?,?,?,?,?,?)',
                               (self.runtime_id, self.session_id, row['epoch'], event_id, encoded, 'evidence'))
                    result = self._project(db, event, row['epoch'])
                    db.execute('UPDATE qwen_stream_frames SET disposition=? WHERE runtime_id=? AND session_id=? '
                               'AND epoch=? AND event_id=?',
                               (result, self.runtime_id, self.session_id, row['epoch'], event_id))
                    db.execute('UPDATE qwen_stream_sessions SET cursor=?,last_seen=? WHERE runtime_id=? AND session_id=?',
                               (event_id, self.store._now(), self.runtime_id, self.session_id))
                    after = self._row(db)
                    if after['state'] == 'resync_required':
                        failure = after['note']
                    if kind in self._HARD_FAILURES or (kind == 'session_snapshot' and data.get('recordingDegraded') is True):
                        self._state(db, 'resync_required', kind)
                        failure = kind
        if failure:
            if failure in self._SOFT_FAILURES:
                raise StreamInterrupted(failure)
            raise ObservationLost(failure)
        return result

    def _project(self, db, event, epoch):
        kind, data = event['type'], event['data']
        prompt = event.get('promptId', data.get('promptId'))
        # Missing prompt attribution is not guessed from 'the current task'.
        if not prompt:
            return 'evidence'
        if kind in ('turn_complete', 'turn_error'):
            outcome = 'error' if kind == 'turn_error' else data.get('stopReason')
            if outcome not in ('end_turn', 'cancelled', 'max_tokens', 'error', 'length'):
                raise ProtocolError('Unknown turn terminal outcome')
            rows = db.execute('SELECT event_id,project_id,state FROM qwen_inputs q JOIN events e USING(event_id) '
                'WHERE runtime_id=? AND session_id=? AND prompt_id=?',
                (self.runtime_id, self.session_id, prompt)).fetchall()
            if not rows:
                return 'pending_admission'  # SSE can win the race against the 202 response
            if len(rows) != 1 or rows[0]['project_id'] != self.project_id:
                raise ProtocolError('Ambiguous prompt admission')
            self.store._record_terminal(db, rows[0]['event_id'], prompt_id=prompt,
                                        evidence_id=f'{epoch}:{event["id"]}', outcome=outcome)
            # End-of-turn is NOT an implicit tool result or proof that a process exited.
            unresolved = db.execute('SELECT x.id FROM qwen_guard_requests g JOIN actions x ON x.id=g.action_id '
                "WHERE g.runtime_id=? AND g.session_id=? AND g.prompt_id=? AND x.state IN ('running','needs_review')",
                (self.runtime_id, self.session_id, prompt)).fetchone()
            if unresolved:
                self._state(db, 'resync_required', 'terminal_with_unresolved_tool')
            return 'turn_terminal'
        if kind == 'session_update' and data.get('sessionUpdate') == 'tool_call_update':
            status = data.get('status')
            if status not in ('completed', 'failed'):
                return 'evidence'
            call_id = identifier(data.get('toolCallId'))
            meta = data.get('_meta', {})
            if not isinstance(meta, dict):
                raise ProtocolError('Malformed tool metadata')
            if (meta.get('provenance') == 'subagent' or meta.get('phase') == 'preparing'
                    or meta.get('preparationDiscarded') is True):
                return 'unqualified_tool'  # a preparation discard is not an executed tool
            row = db.execute('SELECT g.action_id,t.project_id,x.tool FROM qwen_guard_requests g '
                'LEFT JOIN actions x ON x.id=g.action_id LEFT JOIN attempts a ON a.id=x.attempt_id '
                'LEFT JOIN tasks t ON t.id=a.task_id '
                'WHERE g.runtime_id=? AND g.session_id=? AND g.prompt_id=? AND g.tool_call_id=?',
                (self.runtime_id, self.session_id, prompt, call_id)).fetchone()
            if not row or not row['action_id']:
                return 'unqualified_tool'
            if row['project_id'] != self.project_id:
                raise ProtocolError('Tool result belongs to another project')
            if meta.get('toolName', row['tool']) != row['tool']:
                raise ProtocolError('Tool result name does not match its admitted intent')
            # Status is runtime completion, NOT pytest correctness or process-tree drain.
            state = self.store._finish_action(db, row['action_id'], success=status == 'completed',
                                              result={'qwen_tool_update': data, 'prompt_id': prompt})
            return 'tool_' + state
        return 'evidence'

    def reconcile_admissions(self) -> int:
        """Join already-durable terminal frames to late 202 receipts, never resend.

        No authority is created; ambiguous sends without a prompt ID stay blocked.
        Caller must observe a fresh continuous stream before applying projections.
        """
        with self.store._transaction() as db:
            row = self._row(db)
            if row['state'] not in ('live', 'catching_up') or not 0 <= self.store._now() - row['last_seen'] <= 60:
                raise ObservationLost('Cannot project results across a lost/stale stream')
            frames = db.execute("SELECT event_id,envelope FROM qwen_stream_frames WHERE runtime_id=? "
                "AND session_id=? AND epoch=? AND disposition='pending_admission' ORDER BY event_id LIMIT 1000",
                (self.runtime_id, self.session_id, row['epoch'])).fetchall()
            applied = 0
            for frame in frames:
                result = self._project(db, json.loads(frame['envelope']), row['epoch'])
                db.execute('UPDATE qwen_stream_frames SET disposition=? WHERE runtime_id=? AND session_id=? '
                           'AND epoch=? AND event_id=?',
                           (result, self.runtime_id, self.session_id, row['epoch'], frame['event_id']))
                applied += result != 'pending_admission'
                if self._row(db)['state'] == 'resync_required':
                    break
            return applied
