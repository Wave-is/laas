"""Opt-in Qwen integration primitives; no automatic interception of existing UIs.

The outbox commits human input before any network send. HTTP 202 is *admission*,
not delivery to the model. The external Tool Guard uses a separately confirmed
JournalStore delivery; it never grants authority from a queued prompt receipt.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import json
from typing import Callable, Mapping

from .store import (ActionConflict, Busy, IdempotencyConflict, JournalError,
                    JournalStore, StaleRevision, _id, _json, _text)


class ProtocolError(JournalError):
    """Runtime contract missing or invalid. No permissive fallback."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS qwen_extension_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL);
INSERT OR IGNORE INTO qwen_extension_version VALUES(1, 1);
CREATE TABLE IF NOT EXISTS qwen_inputs (
    event_id TEXT PRIMARY KEY REFERENCES events(event_id),
    runtime_id TEXT NOT NULL, session_id TEXT NOT NULL,
    intent TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued',
    prompt_id TEXT, last_event_id INTEGER, note TEXT
);
CREATE TABLE IF NOT EXISTS qwen_prompt_bindings (
    runtime_id TEXT NOT NULL, session_id TEXT NOT NULL, prompt_id TEXT NOT NULL,
    attempt_id TEXT NOT NULL REFERENCES attempts(id),
    delivery_id TEXT NOT NULL REFERENCES deliveries(id),
    PRIMARY KEY(runtime_id, session_id, prompt_id)
);
CREATE TABLE IF NOT EXISTS qwen_guard_requests (
    runtime_id TEXT NOT NULL, request_id TEXT NOT NULL,
    session_id TEXT NOT NULL, prompt_id TEXT NOT NULL, tool_call_id TEXT NOT NULL,
    action_id TEXT REFERENCES actions(id),
    PRIMARY KEY(runtime_id, request_id),
    UNIQUE(runtime_id, session_id, prompt_id, tool_call_id)
);
"""


def identifier(value: str) -> str:
    _id(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Identifier contains control characters')
    return value


class QwenJournal(JournalStore):
    """M1 store plus additive Qwen tables; opens explicitly, never on import."""

    def __init__(self, path, **kwargs):
        super().__init__(path, **kwargs)
        with self._connection() as db:
            db.executescript('BEGIN IMMEDIATE;\n' + _SCHEMA + '\nCOMMIT;')
            if db.execute('SELECT version FROM qwen_extension_version WHERE id=1').fetchone()[0] != 1:
                raise JournalError('Unsupported Qwen integration schema')

    def capture(self, project_id: str, *, runtime_id: str, session_id: str,
                message_id: str, text: str, intent: str = 'queue',
                edit_of: str | None = None, attachments=()) -> dict:
        """Trusted human ingress only. Commit event AND queue row atomically.

        attachment digests must already refer to immutable bytes in this store.
        'steer' is saved immediately, but not transmitted as a normal prompt.
        Edits are append-only corrections, not upstream transcript rewrites.
        """
        for value in (runtime_id, session_id, message_id):
            identifier(value)
        _text(text)
        if intent not in ('prompt', 'queue', 'steer'):
            raise ValueError('Unsupported input intent')
        attachments = list(attachments)
        if len(attachments) > 16 or not all(isinstance(x, str) for x in attachments):
            raise ValueError('Expected up to 16 stored attachment digests')
        source = 'qwen-managed:' + hashlib.sha256(_json([runtime_id, session_id]).encode()).hexdigest()
        with self._transaction() as db:
            if edit_of is not None:
                old = self._one(db, 'SELECT * FROM events WHERE event_id=?', (edit_of,))
                if (old['project_id'] != project_id or old['source'] != source
                        or old['kind'] not in ('user.message', 'user.edit')):
                    raise JournalError('Edit belongs to another input source or project')
            for digest in attachments:
                self._one(db, 'SELECT digest FROM attachments WHERE digest=?', (digest,))
            event = self._append(db, project_id, 'user.edit' if edit_of else 'user.message',
                                 {'text': text, 'edit_of': edit_of, 'attachments': attachments,
                                  'intent': intent}, source=source, source_id=message_id,
                                 changes_revision=True)
            db.execute('INSERT OR IGNORE INTO qwen_inputs(event_id,runtime_id,session_id,intent) '
                       'VALUES(?,?,?,?)', (event['event_id'], runtime_id, session_id, intent))
            return self._input(db, event['event_id'])

    def _input(self, db, event_id):
        row = self._one(db, 'SELECT q.*,e.project_id,e.revision,e.seq,e.payload FROM qwen_inputs q '
                           'JOIN events e ON e.event_id=q.event_id WHERE q.event_id=?', (event_id,))
        value = dict(row)
        value['payload'] = json.loads(value['payload'])
        return value

    def input_status(self, event_id: str) -> dict:
        with self._connection() as db:
            return self._input(db, event_id)

    def pending_inputs(self, runtime_id: str, session_id: str) -> list[dict]:
        """Ordered session outbox, including terminal history for audit/status."""
        with self._connection() as db:
            return [self._input(db, row[0]) for row in db.execute(
                'SELECT q.event_id FROM qwen_inputs q JOIN events e ON e.event_id=q.event_id '
                'WHERE runtime_id=? AND session_id=? ORDER BY e.seq', (runtime_id, session_id))]

    def prepare_request(self, event_id: str) -> dict:
        """Rebuild wire data from persisted bytes, never a disappearing file path.

        This is a single input, NOT M1's full handoff/delivery packet. The caller
        must preserve the current agent history; no context-fit claim is made here.
        """
        item = self.input_status(event_id)
        if item['intent'] == 'steer':
            raise ProtocolError('Steering is stored but its runtime route is not qualified')
        payload = item['payload']
        if payload['text'].lstrip().startswith('/'):
            raise ProtocolError('Slash commands bypass tool admission; managed dispatch refuses them')
        parts = []
        if payload['edit_of']:
            parts.append({'type': 'text', 'text': 'User correction to journal event '
                          + payload['edit_of'] + '; original input remains in the journal.'})
        # Preserve whitespace and empty text for image-only submissions.
        parts.append({'type': 'text', 'text': payload['text']})
        for digest in payload['attachments']:
            media_type, data = self.read_attachment(digest)
            if media_type not in ('image/png', 'image/jpeg', 'image/webp'):
                raise ProtocolError('Attachment transport not qualified; original bytes remain stored')
            parts.append({'type': 'image', 'mimeType': media_type,
                          'data': base64.b64encode(data).decode('ascii')})
        body = {'prompt': parts}
        if payload['text'].strip():
            body['_meta'] = {'qwen.submittedPrompt': payload['text']}
        if len(_json(body).encode('utf-8')) > 24 * 1024 * 1024:
            raise ProtocolError('Wire payload too large; no input was truncated')
        return body

    def claim_input(self, event_id: str) -> dict:
        """One in-flight input per runtime/session; crash leaves a blocking intent."""
        with self._transaction() as db:
            item = self._input(db, event_id)
            if item['state'] != 'queued':
                raise Busy('Input already dispatched or cancelled; inspect instead of replaying')
            first = self._one(db,
                'SELECT q.event_id FROM qwen_inputs q JOIN events e ON e.event_id=q.event_id '
                "WHERE q.runtime_id=? AND q.session_id=? AND q.state IN ('queued','sending','accepted','uncertain') "
                'ORDER BY e.seq LIMIT 1', (item['runtime_id'], item['session_id']))
            if first[0] != event_id:
                raise Busy('Earlier input is queued, active or uncertain')
            db.execute("UPDATE qwen_inputs SET state='sending' WHERE event_id=?", (event_id,))
            return self._input(db, event_id)

    def record_admission(self, event_id: str, *, state: str,
                         prompt_id: str | None = None, last_event_id: int | None = None) -> dict:
        if state not in ('accepted', 'uncertain', 'rejected'):
            raise ValueError('Invalid admission state')
        if state == 'accepted':
            identifier(prompt_id)
            if type(last_event_id) is not int or last_event_id < 0:
                raise ProtocolError('Admission must include a nonnegative event cursor')
        elif prompt_id is not None or last_event_id is not None:
            raise ValueError('Unconfirmed admission cannot carry a prompt receipt')
        with self._transaction() as db:
            item = self._input(db, event_id)
            if item['state'] != 'sending':
                raise Busy('Admission no longer owns this pending dispatch')
            db.execute('UPDATE qwen_inputs SET state=?,prompt_id=?,last_event_id=? WHERE event_id=?',
                       (state, prompt_id, last_event_id, event_id))
            # Do NOT acknowledge_delivery: 202 means queued, not consumed by the model.
            self._append(db, item['project_id'], 'runtime.notice',
                         {'task_id': None, 'attempt_id': None, 'data': {
                             'input_event_id': event_id, 'admission': state, 'prompt_id': prompt_id}})
            return self._input(db, event_id)

    def record_terminal(self, event_id: str, *, prompt_id: str, evidence_id: str,
                        outcome: str) -> None:
        """Trusted runtime observer only. Not an LLM 'done' flag or a tool receipt."""
        identifier(prompt_id)
        identifier(evidence_id)
        if outcome not in ('end_turn', 'cancelled', 'max_tokens', 'error', 'length'):
            raise ValueError('Unsupported turn outcome')
        with self._transaction() as db:
            item = self._input(db, event_id)
            if item['state'] not in ('accepted', 'completed') or item['prompt_id'] != prompt_id:
                raise JournalError('Terminal event does not match an admitted input')
            self._append(db, item['project_id'], 'runtime.notice',
                         {'task_id': None, 'attempt_id': None, 'data': {
                             'input_event_id': event_id, 'prompt_id': prompt_id, 'outcome': outcome}},
                         source='qwen-terminal:' + event_id, source_id=evidence_id)
            if item['state'] == 'completed' and item['note'] != outcome:
                raise IdempotencyConflict('Conflicting terminal outcomes')
            db.execute("UPDATE qwen_inputs SET state='completed',note=? WHERE event_id=?", (outcome, event_id))

    def cancel_queued(self, event_id: str) -> None:
        """Cancel only undispatched work; never claims to stop a running daemon."""
        with self._transaction() as db:
            item = self._input(db, event_id)
            if item['state'] == 'cancelled':
                return
            if item['state'] != 'queued':
                raise Busy('Already dispatched; cancellation requires runtime reconciliation')
            db.execute("UPDATE qwen_inputs SET state='cancelled' WHERE event_id=?", (event_id,))
            self._append(db, item['project_id'], 'runtime.notice',
                         {'task_id': None, 'attempt_id': None, 'data': {
                             'input_event_id': event_id, 'queue_cancelled': True}})

    def reconcile_input(self, event_id: str, *, note: str, expected_revision: int,
                        definitely_not_admitted: bool = False) -> None:
        """Operator proof required; ambiguity is never automatically retried."""
        if not _text(note).strip() or definitely_not_admitted is not True:
            raise ValueError('Retry requires explicit proof that no prompt was admitted')
        with self._transaction() as db:
            item = self._input(db, event_id)
            revision = self._one(db, 'SELECT revision FROM projects WHERE id=?', (item['project_id'],))[0]
            if revision != expected_revision:
                raise StaleRevision('Input changed before reconciliation')
            if item['state'] not in ('sending', 'uncertain'):
                raise JournalError('Input does not need dispatch reconciliation')
            state = 'queued'
            db.execute('UPDATE qwen_inputs SET state=?,note=? WHERE event_id=?', (state, note, event_id))
            self._append(db, item['project_id'], 'operator.input_reconciled',
                         {'input_event_id': event_id, 'note': note,
                          'definitely_not_admitted': definitely_not_admitted})

    def bind_delivered_prompt(self, *, runtime_id: str, session_id: str, prompt_id: str,
                              attempt_id: str, delivery_id: str, digest: str) -> None:
        """Trusted delivery observer only; never call merely after HTTP 202.

        The complete M1 packet must have reached the selected attempt through a
        qualified transport. This method is not exposed to model tools or HTTP.
        """
        for v in (runtime_id, session_id, prompt_id):
            identifier(v)
        with self._transaction() as db:
            attempt, revision = self._current(db, attempt_id)
            delivery = self._one(db, 'SELECT * FROM deliveries WHERE id=?', (delivery_id,))
            if delivery['attempt_id'] != attempt_id or delivery['digest'] != digest:
                raise JournalError('Delivery does not belong to this attempt')
            if delivery['revision'] != revision:
                raise StaleRevision('New input arrived during delivery')
            old = db.execute('SELECT * FROM qwen_prompt_bindings WHERE runtime_id=? AND session_id=? '
                             'AND prompt_id=?', (runtime_id, session_id, prompt_id)).fetchone()
            if old and (old['attempt_id'] != attempt_id or old['delivery_id'] != delivery_id):
                raise IdempotencyConflict('Runtime prompt is already bound to a different delivery')
            db.execute('INSERT OR IGNORE INTO qwen_prompt_bindings VALUES(?,?,?,?,?)',
                       (runtime_id, session_id, prompt_id, attempt_id, delivery_id))
            db.execute('UPDATE deliveries SET acknowledged=1 WHERE id=?', (delivery_id,))
            db.execute('UPDATE attempts SET confirmed_revision=? WHERE id=?', (revision, attempt_id))


@dataclass(frozen=True)
class ToolRule:
    """Trusted application policy, NOT a value supplied by the model."""
    mutating: bool
    validate: Callable[[dict], bool]


class QwenToolGuard:
    """External Tool Guard v1 decision provider, deny-by-default.

    This is admission, not a shell sandbox. No tool is executed here. Successful
    permits remain unresolved until a trusted observer records the actual result.
    """
    _NESTED = {'agent', 'workflow', 'create_sub_session', 'send_message', 'monitor'}

    def __init__(self, store: QwenJournal, runtime_id: str, rules: Mapping[str, ToolRule]):
        self.store = store
        self.runtime_id = identifier(runtime_id)
        self.rules = dict(rules)
        for name, rule in self.rules.items():
            identifier(name)
            if not isinstance(rule, ToolRule) or type(rule.mutating) is not bool or not callable(rule.validate):
                raise ValueError('Invalid trusted tool policy')

    def prepare(self, request: dict) -> dict:
        required = {'protocolVersion', 'requestId', 'sessionId', 'promptId', 'toolCallId', 'toolName', 'arguments'}
        if (not isinstance(request, dict) or set(request) != required
                or type(request['protocolVersion']) is not int or request['protocolVersion'] != 1):
            raise ProtocolError('Invalid Tool Guard v1 request')
        for name in ('requestId', 'sessionId', 'promptId', 'toolCallId', 'toolName'):
            identifier(request[name])
        if not isinstance(request['arguments'], dict):
            raise ProtocolError('Tool arguments must be an object')
        answer = {'protocolVersion': 1, 'requestId': request['requestId'], 'allowed': False}
        tool = request['toolName']
        args = request['arguments']
        rule = self.rules.get(tool)
        try:
            if (not rule or tool in self._NESTED or args.get('is_background', False) is not False
                    or rule.validate(json.loads(_json(args))) is not True):
                raise JournalError('Policy refuses this tool or its arguments')
            # Reserve request and tuple BEFORE admission. A crash in between is a
            # conservative refusal on replay, never a second permit.
            with self.store._transaction() as db:
                binding = self.store._one(db, 'SELECT * FROM qwen_prompt_bindings WHERE runtime_id=? '
                    'AND session_id=? AND prompt_id=?',
                    (self.runtime_id, request['sessionId'], request['promptId']))
                self.store._current(db, binding['attempt_id'], require_revision=True)
                duplicate = db.execute('SELECT request_id FROM qwen_guard_requests WHERE runtime_id=? '
                    'AND (request_id=? OR (session_id=? AND prompt_id=? AND tool_call_id=?))',
                    (self.runtime_id, request['requestId'], request['sessionId'],
                     request['promptId'], request['toolCallId'])).fetchone()
                if duplicate:
                    raise ActionConflict('Tool permit cannot be replayed')
                db.execute('INSERT INTO qwen_guard_requests VALUES(?,?,?,?,?,NULL)',
                           (self.runtime_id, request['requestId'], request['sessionId'],
                            request['promptId'], request['toolCallId']))
                attempt_id = binding['attempt_id']
            correlation = hashlib.sha256(_json([self.runtime_id, request['sessionId'],
                request['promptId'], request['toolCallId']]).encode()).hexdigest()
            action_id = self.store.begin_action(attempt_id, 'qwen:' + correlation, tool, args,
                                                mutating=rule.mutating)
            with self.store._transaction() as db:
                db.execute('UPDATE qwen_guard_requests SET action_id=? WHERE runtime_id=? AND request_id=?',
                           (action_id, self.runtime_id, request['requestId']))
                # Last re-check before permit response. Cannot undo an executor
                # already admitted just before a later correction arrives.
                self.store._current(db, attempt_id, require_revision=True)
            answer['allowed'] = True
        except Exception:
            # No exception/raw tool content in the public protocol or logs.
            answer['reason'] = 'Unconfirmed revision, stale attempt, duplicate request or denied tool policy.'
        return answer

    def observe_result(self, *, session_id: str, prompt_id: str, tool_call_id: str,
                       success: bool, result: dict) -> str:
        """Trusted lifecycle observer only; not part of the HTTP/LLM API."""
        if type(success) is not bool:
            raise ValueError('Result status must be a boolean')
        with self.store._connection() as db:
            row = self.store._one(db, 'SELECT action_id FROM qwen_guard_requests WHERE runtime_id=? '
                'AND session_id=? AND prompt_id=? AND tool_call_id=?',
                (self.runtime_id, session_id, prompt_id, tool_call_id))
        if not row['action_id']:
            raise JournalError('No admitted action matches this observation')
        return self.store.finish_action(row['action_id'], success=success, result=result)
