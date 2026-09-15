"""Durable conversation ingress and fencing, independent of any model or GPU.

Only adapters which route *every* user change and tool launch through this API can
claim managed coordination. This store does not intercept external agent GUIs or
revoke an already-running OS process. Uncertain actions block reassignment until
an operator reconciles them. See docs/COORDINATION.md for integration boundaries.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import time
from typing import Callable, Iterator
from uuid import uuid4


class JournalError(RuntimeError):
    """A managed operation was refused; do not acknowledge/execute it anyway."""


class IdempotencyConflict(JournalError):
    pass


class Busy(JournalError):
    pass


class StaleAttempt(JournalError):
    pass


class StaleRevision(JournalError):
    pass


class ReconciliationRequired(JournalError):
    pass


class ActionConflict(JournalError):
    pass


class ContextOverflow(JournalError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _id(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError('Expected a nonempty identifier of at most 256 characters')
    return value


def _text(value: str) -> str:
    if not isinstance(value, str) or len(value.encode('utf-8')) > 1024 * 1024:
        raise ValueError('Expected UTF-8 text of at most 1 MiB; use attachments for larger input')
    return value


_SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY, workspace TEXT NOT NULL UNIQUE, revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    project_id TEXT NOT NULL REFERENCES projects(id),
    revision INTEGER NOT NULL, kind TEXT NOT NULL,
    source TEXT NOT NULL, source_id TEXT NOT NULL,
    payload TEXT NOT NULL, created_at REAL NOT NULL,
    UNIQUE(project_id, source, source_id)
);
CREATE INDEX IF NOT EXISTS event_project_seq ON events(project_id, seq);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
BEGIN SELECT RAISE(ABORT, 'events are append-only'); END;
CREATE TABLE IF NOT EXISTS attachments (
    digest TEXT PRIMARY KEY, media_type TEXT NOT NULL, content BLOB NOT NULL
);
CREATE TRIGGER IF NOT EXISTS attachments_no_update BEFORE UPDATE ON attachments
BEGIN SELECT RAISE(ABORT, 'attachments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS attachments_no_delete BEFORE DELETE ON attachments
BEGIN SELECT RAISE(ABORT, 'attachments are immutable'); END;
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
    description TEXT NOT NULL, epoch INTEGER NOT NULL DEFAULT 0,
    active_attempt TEXT, status TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS attempts (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    epoch INTEGER NOT NULL, worker_id TEXT NOT NULL,
    write_access INTEGER NOT NULL, state TEXT NOT NULL,
    lease_until REAL NOT NULL, confirmed_revision INTEGER NOT NULL DEFAULT -1
);
CREATE TABLE IF NOT EXISTS deliveries (
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
    revision INTEGER NOT NULL, through_seq INTEGER NOT NULL,
    payload TEXT NOT NULL, digest TEXT NOT NULL, acknowledged INTEGER NOT NULL DEFAULT 0,
    UNIQUE(attempt_id, revision, through_seq)
);
CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(id),
    request_id TEXT NOT NULL, tool TEXT NOT NULL, arguments TEXT NOT NULL,
    mutating INTEGER NOT NULL, revision INTEGER NOT NULL,
    state TEXT NOT NULL, result TEXT, success INTEGER,
    UNIQUE(attempt_id, request_id)
);
"""


class JournalStore:
    """Explicitly opened local SQLite database. No singleton, network or LLM calls.

    Return from append_user is the persistence receipt. Storage errors propagate:
    a frontend must not show a successful send or call a model before that return.
    Caller-supplied source IDs must survive UI retries and daemon reconnects.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], float] = time.time):
        if str(path) == ':memory:':
            raise ValueError('Managed ingress requires a persistent local database')
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, 1):
                raise JournalError(f'Unsupported coordinator schema: {version}')
            db.execute('PRAGMA journal_mode=WAL')
            # executescript is one transaction, including the version marker.
            db.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA + '\nPRAGMA user_version=1;\nCOMMIT;')

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA synchronous=FULL')
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute('BEGIN IMMEDIATE')
            try:
                yield db
                db.execute('COMMIT')
            except BaseException:
                db.execute('ROLLBACK')
                raise

    def _now(self) -> float:
        value = float(self.clock())
        if not math.isfinite(value):
            raise ValueError('Clock must return a finite UTC timestamp')
        return value

    def _deadline(self, ttl: float) -> float:
        if not math.isfinite(ttl) or not 1 <= ttl <= 3600:
            raise ValueError('Lease TTL must be between 1 and 3600 seconds')
        return self._now() + ttl

    @staticmethod
    def _one(db, sql, args=()):
        row = db.execute(sql, args).fetchone()
        if row is None:
            raise JournalError('Unknown project, task, attempt, event or action')
        return row

    @staticmethod
    def _event(row) -> dict:
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        return value

    def _append(self, db, project, kind, payload, *, source='coordinator', source_id=None,
                changes_revision=False) -> dict:
        source_id = source_id or str(uuid4())
        encoded = _json(payload)
        if len(encoded.encode('utf-8')) > 1024 * 1024:
            raise ValueError('Event payload exceeds 1 MiB; store large content as an attachment')
        old = db.execute('SELECT * FROM events WHERE project_id=? AND source=? AND source_id=?',
                         (project, source, source_id)).fetchone()
        if old is not None:
            if old['kind'] != kind or old['payload'] != encoded:
                raise IdempotencyConflict('Source message ID was reused with different content')
            return self._event(old)
        revision = self._one(db, 'SELECT revision FROM projects WHERE id=?', (project,))[0]
        if changes_revision:
            revision += 1
            db.execute('UPDATE projects SET revision=? WHERE id=?', (revision, project))
        event_id = str(uuid4())
        db.execute('INSERT INTO events(event_id,project_id,revision,kind,source,source_id,payload,created_at) '
                   'VALUES(?,?,?,?,?,?,?,?)',
                   (event_id, project, revision, kind, source, source_id, encoded, self._now()))
        return self._event(self._one(db, 'SELECT * FROM events WHERE event_id=?', (event_id,)))

    def open_project(self, project_id: str, workspace: str | Path) -> None:
        project_id = _id(project_id)
        workspace = os.path.normcase(str(Path(workspace).expanduser().resolve()))
        with self._transaction() as db:
            old = db.execute('SELECT workspace FROM projects WHERE id=?', (project_id,)).fetchone()
            if old and old[0] != workspace:
                raise IdempotencyConflict('Project ID is already bound to a different workspace')
            owner = db.execute('SELECT id FROM projects WHERE workspace=?', (workspace,)).fetchone()
            if owner and owner[0] != project_id:
                raise IdempotencyConflict('Workspace already belongs to another project ID')
            db.execute('INSERT OR IGNORE INTO projects(id,workspace) VALUES(?,?)', (project_id, workspace))

    def put_attachment(self, content: bytes, media_type: str) -> str:
        if not isinstance(content, bytes) or len(content) > 20 * 1024 * 1024:
            raise ValueError('Attachment must be bytes, at most 20 MiB')
        _id(media_type)
        digest = hashlib.sha256(content).hexdigest()
        with self._transaction() as db:
            old = db.execute('SELECT media_type FROM attachments WHERE digest=?', (digest,)).fetchone()
            if old and old[0] != media_type:
                raise IdempotencyConflict('Same attachment has conflicting media types')
            db.execute('INSERT OR IGNORE INTO attachments VALUES(?,?,?)', (digest, media_type, content))
        return digest

    def read_attachment(self, digest: str) -> tuple[str, bytes]:
        with self._connection() as db:
            row = self._one(db, 'SELECT media_type,content FROM attachments WHERE digest=?', (digest,))
            if hashlib.sha256(row['content']).hexdigest() != digest:
                raise JournalError('Attachment integrity check failed')
            return row['media_type'], row['content']

    def append_user(self, project_id: str, *, source: str, message_id: str,
                    text: str, edit_of: str | None = None, attachments=()) -> dict:
        """Persist exact text; every user message conservatively revises the project.

        An edit is a new event pointing to the old event ID. It never overwrites it.
        No semantic classifier/model can decide to discard a user correction.
        """
        _id(source)
        _id(message_id)
        _text(text)
        attachments = list(attachments)
        if len(attachments) > 16 or not all(isinstance(item, str) for item in attachments):
            raise ValueError('Expected at most 16 attachment digests')
        with self._transaction() as db:
            if edit_of is not None:
                original = self._one(db, 'SELECT * FROM events WHERE event_id=?', (edit_of,))
                if (original['project_id'] != project_id or original['source'] != source
                        or original['kind'] not in ('user.message', 'user.edit')):
                    raise JournalError('Edit must reference a user event from the same project and source')
            for digest in attachments:
                self._one(db, 'SELECT digest FROM attachments WHERE digest=?', (digest,))
            return self._append(db, project_id, 'user.edit' if edit_of else 'user.message',
                                {'text': text, 'edit_of': edit_of, 'attachments': attachments},
                                source=source, source_id=message_id, changes_revision=True)

    def events(self, project_id: str, *, after_seq: int = 0) -> list[dict]:
        with self._connection() as db:
            self._one(db, 'SELECT id FROM projects WHERE id=?', (project_id,))
            return [self._event(row) for row in db.execute(
                'SELECT * FROM events WHERE project_id=? AND seq>? ORDER BY seq', (project_id, after_seq))]

    def record_runtime_event(self, project_id: str, *, source: str, message_id: str,
                             kind: str, payload: dict, task_id: str | None = None,
                             attempt_id: str | None = None) -> dict:
        """Persist stream chunks/messages/tool output even from superseded attempts.

        This is evidence, not a dispatch command or a new user instruction. An adapter
        must classify human input from its authenticated ingress, never from content
        such as an assistant's quoted 'user:' string.
        """
        if kind not in ('assistant.chunk', 'assistant.message', 'tool.output', 'runtime.notice'):
            raise ValueError('Unsupported runtime evidence kind')
        _id(source)
        _id(message_id)
        if not isinstance(payload, dict):
            raise ValueError('Runtime payload must be an object')
        with self._transaction() as db:
            if task_id is not None:
                task = self._one(db, 'SELECT project_id FROM tasks WHERE id=?', (task_id,))
                if task['project_id'] != project_id:
                    raise JournalError('Runtime event belongs to another project')
            if attempt_id is not None:
                attempt = self._one(db, 'SELECT a.task_id,t.project_id FROM attempts a '
                                    'JOIN tasks t ON t.id=a.task_id WHERE a.id=?', (attempt_id,))
                if attempt['project_id'] != project_id or attempt['task_id'] != task_id:
                    raise JournalError('Runtime event belongs to another task or attempt')
            return self._append(db, project_id, kind,
                                {'task_id': task_id, 'attempt_id': attempt_id, 'data': payload},
                                source=source, source_id=message_id)

    def task_status(self, task_id: str) -> dict:
        """Content-free status for a future Station UI; no inference or network calls."""
        with self._connection() as db:
            row = self._one(db, 'SELECT t.*,p.revision FROM tasks t JOIN projects p '
                            'ON p.id=t.project_id WHERE t.id=?', (task_id,))
            value = dict(row)
            attempt = db.execute('SELECT * FROM attempts WHERE id=?', (row['active_attempt'],)).fetchone()
            value['attempt'] = dict(attempt) if attempt else None
            value['needs_delivery'] = bool(attempt and attempt['confirmed_revision'] != row['revision'])
            value['unresolved_actions'] = [r[0] for r in db.execute(
                'SELECT x.id FROM actions x JOIN attempts a ON a.id=x.attempt_id '
                "WHERE a.task_id=? AND x.state IN ('running','needs_review') ORDER BY x.rowid", (task_id,))]
            return value

    def create_task(self, project_id: str, task_id: str, description: str) -> None:
        _id(task_id)
        _text(description)
        with self._transaction() as db:
            self._one(db, 'SELECT id FROM projects WHERE id=?', (project_id,))
            old = db.execute('SELECT * FROM tasks WHERE id=?', (task_id,)).fetchone()
            if old:
                if old['project_id'] != project_id or old['description'] != description:
                    raise IdempotencyConflict('Task ID was reused with different content')
                return
            db.execute('INSERT INTO tasks(id,project_id,description) VALUES(?,?,?)',
                       (task_id, project_id, description))
            self._append(db, project_id, 'task.created', {'task_id': task_id, 'description': description})

    def _current(self, db, attempt_id: str, *, require_revision=False):
        attempt = self._one(db, 'SELECT a.*,t.project_id,t.active_attempt,t.epoch AS task_epoch '
                            'FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE a.id=?', (attempt_id,))
        if (attempt['state'] != 'active' or attempt['active_attempt'] != attempt_id
                or attempt['epoch'] != attempt['task_epoch'] or attempt['lease_until'] <= self._now()):
            raise StaleAttempt('Attempt is revoked, expired or no longer owns this task')
        revision = self._one(db, 'SELECT revision FROM projects WHERE id=?', (attempt['project_id'],))[0]
        if require_revision and attempt['confirmed_revision'] != revision:
            raise StaleRevision('New user input must be delivered before further tools or completion')
        return attempt, revision

    def start_attempt(self, task_id: str, worker_id: str, *, write_access=False,
                      ttl: float = 120, replace=False) -> str:
        """Claim/replace an attempt. Unknown tool outcomes prevent automatic replay."""
        _id(worker_id)
        deadline = self._deadline(ttl)
        with self._transaction() as db:
            task = self._one(db, 'SELECT * FROM tasks WHERE id=?', (task_id,))
            unresolved = db.execute('SELECT x.id FROM actions x JOIN attempts a ON a.id=x.attempt_id '
                                    "WHERE a.task_id=? AND x.state IN ('running','needs_review') LIMIT 1",
                                    (task_id,)).fetchone()
            if unresolved:
                raise ReconciliationRequired('Prior tool outcome is unresolved; do not replay the task')
            old = db.execute('SELECT * FROM attempts WHERE id=?', (task['active_attempt'],)).fetchone()
            if old and old['state'] == 'active' and old['lease_until'] > self._now() and not replace:
                raise Busy('Task already has a live attempt')
            if write_access:
                writer = db.execute('SELECT a.id FROM attempts a JOIN tasks t ON t.id=a.task_id '
                                    "WHERE t.project_id=? AND a.write_access=1 AND a.state='active' "
                                    'AND a.task_id<>? LIMIT 1', (task['project_id'], task_id)).fetchone()
                uncertain = db.execute('SELECT x.id FROM actions x JOIN attempts a ON a.id=x.attempt_id '
                                       'JOIN tasks t ON t.id=a.task_id WHERE t.project_id=? AND x.mutating=1 '
                                       "AND x.state IN ('running','needs_review') LIMIT 1",
                                       (task['project_id'],)).fetchone()
                if writer or uncertain:
                    raise Busy('Project has another writer or an unresolved mutating action')
            if old and old['state'] == 'active':
                db.execute("UPDATE attempts SET state='revoked' WHERE id=?", (old['id'],))
            attempt_id = str(uuid4())
            epoch = task['epoch'] + 1
            db.execute('INSERT INTO attempts(id,task_id,epoch,worker_id,write_access,state,lease_until) '
                       "VALUES(?,?,?,?,?,'active',?)",
                       (attempt_id, task_id, epoch, worker_id, int(bool(write_access)), deadline))
            db.execute("UPDATE tasks SET epoch=?,active_attempt=?,status='running' WHERE id=?",
                       (epoch, attempt_id, task_id))
            self._append(db, task['project_id'], 'attempt.started',
                         {'task_id': task_id, 'attempt_id': attempt_id, 'epoch': epoch,
                          'worker_id': worker_id, 'replaces': old['id'] if old else None})
            return attempt_id

    def heartbeat(self, attempt_id: str, *, ttl: float = 120) -> None:
        deadline = self._deadline(ttl)
        with self._transaction() as db:
            self._current(db, attempt_id)
            db.execute('UPDATE attempts SET lease_until=? WHERE id=?', (deadline, attempt_id))

    def prepare_delivery(self, attempt_id: str, *, token_count: Callable[[dict], int],
                         context_limit: int, reserve_tokens: int) -> dict:
        """Create a replayable packet containing ALL original user messages.

        token_count must include the real template, tools and attachment/image cost.
        If the packet does not fit, refuse: never silently summarize away messages.
        The returned receipt/digest must be acknowledged by the transport, not LLM.
        """
        if (not isinstance(context_limit, int) or not isinstance(reserve_tokens, int)
                or not 0 <= reserve_tokens < context_limit):
            raise ValueError('Invalid context budget')
        with self._transaction() as db:
            attempt, revision = self._current(db, attempt_id)
            rows = list(db.execute('SELECT * FROM events WHERE project_id=? ORDER BY seq',
                                   (attempt['project_id'],)))
            task = self._one(db, 'SELECT * FROM tasks WHERE id=?', (attempt['task_id'],))
            packet = {'project_id': attempt['project_id'], 'task_id': task['id'],
                      'attempt_id': attempt_id, 'epoch': attempt['epoch'], 'revision': revision,
                      'task_description': task['description'], 'through_seq': rows[-1]['seq'],
                      'user_events': [self._event(row) for row in rows if row['kind'].startswith('user.')],
                      'task_events': [self._event(row) for row in rows
                                      if not row['kind'].startswith('user.')
                                      and json.loads(row['payload']).get('task_id') == task['id']]}
            count = token_count(packet)
            if not isinstance(count, int) or count < 0:
                raise ValueError('Tokenizer must return a nonnegative integer')
            if count + reserve_tokens > context_limit:
                raise ContextOverflow('Original input does not fit; request scoped handoff or larger worker')
            encoded = _json(packet)
            digest = hashlib.sha256(encoded.encode('utf-8')).hexdigest()
            old = db.execute('SELECT * FROM deliveries WHERE attempt_id=? AND revision=? AND through_seq=?',
                             (attempt_id, revision, packet['through_seq'])).fetchone()
            delivery_id = old['id'] if old else str(uuid4())
            if not old:
                db.execute('INSERT INTO deliveries(id,attempt_id,revision,through_seq,payload,digest) '
                           'VALUES(?,?,?,?,?,?)',
                           (delivery_id, attempt_id, revision, packet['through_seq'], encoded, digest))
            return {'delivery_id': delivery_id, 'digest': digest, 'packet': packet,
                    'input_tokens': count, 'acknowledged': bool(old and old['acknowledged'])}

    def acknowledge_delivery(self, attempt_id: str, delivery_id: str, digest: str) -> None:
        with self._transaction() as db:
            attempt, revision = self._current(db, attempt_id)
            delivery = self._one(db, 'SELECT * FROM deliveries WHERE id=?', (delivery_id,))
            if delivery['attempt_id'] != attempt_id or delivery['digest'] != digest:
                raise JournalError('Delivery receipt does not match the active attempt')
            if delivery['revision'] != revision:
                raise StaleRevision('More user input arrived while this packet was in transit')
            db.execute('UPDATE deliveries SET acknowledged=1 WHERE id=?', (delivery_id,))
            db.execute('UPDATE attempts SET confirmed_revision=? WHERE id=?', (revision, attempt_id))

    def begin_action(self, attempt_id: str, request_id: str, tool: str,
                     arguments: dict, *, mutating: bool) -> str:
        """Persist intent BEFORE launching a tool. A repeated intent is never executed twice."""
        _id(request_id)
        _id(tool)
        if not isinstance(arguments, dict):
            raise ValueError('Tool arguments must be an object')
        encoded = _json(arguments)
        with self._transaction() as db:
            attempt, revision = self._current(db, attempt_id, require_revision=True)
            if mutating and not attempt['write_access']:
                raise JournalError('Read-only worker cannot request a mutating tool')
            old = db.execute('SELECT * FROM actions WHERE attempt_id=? AND request_id=?',
                             (attempt_id, request_id)).fetchone()
            if old:
                raise ActionConflict(f'Tool intent already recorded as {old["id"]}; inspect, do not rerun')
            if db.execute("SELECT id FROM actions WHERE attempt_id=? AND state IN ('running','needs_review')",
                          (attempt_id,)).fetchone():
                raise Busy('Finish or reconcile the previous tool before launching another')
            action_id = str(uuid4())
            db.execute('INSERT INTO actions(id,attempt_id,request_id,tool,arguments,mutating,revision,state) '
                       "VALUES(?,?,?,?,?,?,?,'running')",
                       (action_id, attempt_id, request_id, tool, encoded, int(bool(mutating)), revision))
            self._append(db, attempt['project_id'], 'tool.started',
                         {'task_id': attempt['task_id'], 'attempt_id': attempt_id,
                          'action_id': action_id, 'tool': tool, 'arguments': arguments,
                          'mutating': bool(mutating)})
            return action_id

    def finish_action(self, action_id: str, *, success: bool, result: dict) -> str:
        """Keep late results as evidence; never let them regain authority."""
        with self._transaction() as db:
            return self._finish_action(db, action_id, success=success, result=result)

    def _finish_action(self, db, action_id: str, *, success: bool, result: dict) -> str:
        """Transaction-sharing variant for an atomic event/cursor/result commit."""
        encoded = _json(result)
        action = self._one(db, 'SELECT * FROM actions WHERE id=?', (action_id,))
        if action['result'] is not None:
            if action['result'] != encoded or bool(action['success']) != bool(success):
                raise IdempotencyConflict('Conflicting results for one tool action')
            return action['state']
        attempt = self._one(db, 'SELECT a.*,t.project_id FROM attempts a JOIN tasks t '
                            'ON t.id=a.task_id WHERE a.id=?', (action['attempt_id'],))
        try:
            _, revision = self._current(db, attempt['id'], require_revision=True)
            current = revision == action['revision']
        except (StaleAttempt, StaleRevision):
            current = False
        state = ('succeeded' if success else 'failed') if current else 'needs_review'
        if action['state'] == 'reconciled':
            state = 'reconciled'  # late evidence must not undo an operator's reconciliation
        db.execute('UPDATE actions SET state=?,result=?,success=? WHERE id=?',
                   (state, encoded, int(bool(success)), action_id))
        self._append(db, attempt['project_id'], 'tool.finished',
                     {'task_id': attempt['task_id'], 'attempt_id': attempt['id'], 'action_id': action_id,
                      'state': state, 'success': bool(success), 'result': result})
        return state

    def reconcile_action(self, action_id: str, *, note: str, expected_revision: int) -> None:
        """Operator-only gate AFTER checking process/files. Not an LLM tool or automatic retry."""
        if not _text(note).strip():
            raise ValueError('Reconciliation requires an explanation')
        with self._transaction() as db:
            row = self._one(db, 'SELECT x.*,a.task_id,t.project_id,p.revision AS current_revision '
                            'FROM actions x JOIN attempts a ON a.id=x.attempt_id '
                            'JOIN tasks t ON t.id=a.task_id JOIN projects p ON p.id=t.project_id '
                            'WHERE x.id=?', (action_id,))
            if row['current_revision'] != expected_revision:
                raise StaleRevision('Project changed before reconciliation')
            if row['state'] not in ('running', 'needs_review'):
                raise JournalError('Action does not need reconciliation')
            db.execute("UPDATE actions SET state='reconciled' WHERE id=?", (action_id,))
            self._append(db, row['project_id'], 'operator.reconciled',
                         {'task_id': row['task_id'], 'action_id': action_id, 'note': note})

    def cancel_attempt(self, attempt_id: str, *, reason: str) -> None:
        _text(reason)
        with self._transaction() as db:
            row = self._one(db, 'SELECT a.*,t.project_id FROM attempts a JOIN tasks t '
                            'ON t.id=a.task_id WHERE a.id=?', (attempt_id,))
            if row['state'] != 'active':
                return
            db.execute("UPDATE attempts SET state='revoked' WHERE id=?", (attempt_id,))
            db.execute("UPDATE tasks SET status='paused' WHERE active_attempt=?", (attempt_id,))
            self._append(db, row['project_id'], 'attempt.cancelled',
                         {'task_id': row['task_id'], 'attempt_id': attempt_id, 'reason': reason})

    def finish_attempt(self, attempt_id: str, result: str) -> None:
        """Propose a result for review, NOT a proof of tests/requirements being satisfied."""
        _text(result)
        with self._transaction() as db:
            row, revision = self._current(db, attempt_id, require_revision=True)
            if db.execute("SELECT id FROM actions WHERE attempt_id=? AND state IN ('running','needs_review')",
                          (attempt_id,)).fetchone():
                raise ReconciliationRequired('Unresolved tool actions prevent completion')
            db.execute("UPDATE attempts SET state='completed' WHERE id=?", (attempt_id,))
            db.execute("UPDATE tasks SET status='awaiting_review' WHERE id=?", (row['task_id'],))
            self._append(db, row['project_id'], 'attempt.result',
                         {'task_id': row['task_id'], 'attempt_id': attempt_id,
                          'based_on_revision': revision, 'text': result, 'status': 'awaiting_review'})

    def record_summary(self, project_id: str, text: str, *, through_seq: int) -> dict:
        """Derived convenience view only: never advances delivery or replaces raw input."""
        _text(text)
        with self._transaction() as db:
            self._one(db, 'SELECT seq FROM events WHERE project_id=? AND seq=?', (project_id, through_seq))
            return self._append(db, project_id, 'summary.derived', {'text': text, 'through_seq': through_seq})
