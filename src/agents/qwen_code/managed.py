"""Opt-in durable ingress for callers that control the Qwen input boundary.

This facade is not installed into Qwen Desktop/Telegram automatically. Merely
opening it neither sends a prompt nor starts a daemon, model or HTTP listener.
"""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from ...coordination.qwen import QwenJournal, identifier
from ...coordination.qwen_http import QwenDaemonClient


def coordination_coverage() -> dict:
    """Honest coverage, separate from upstream daemon feature discovery."""
    return {
        'managed_input_outbox': True,
        'managed_append_only_edits': True,
        'managed_followup_queue': True,
        'steering_capture': True,
        'steering_dispatch': False,
        'native_desktop_capture': False,
        'native_telegram_capture': False,
        'runtime_result_observer': True,
        'durable_sse_epoch_cursor': True,
        'full_packet_delivery_verified': False,
        'owned_process_drain': False,
        'external_tool_guard_v1_provider': True,
        'live_runtime_verified': False,
        'automatic_failover': False,
    }


class ManagedQwenInput:
    """Explicit project/session binding used by the Station runtime adapter.

    A frontend must call capture at authenticated human ingress and retain its
    message ID across retries. It must never report a successful send if capture
    fails. Dispatcher admission and model delivery remain different states.
    """

    def __init__(self, *, journal_path: str | Path, project_id: str,
                 workspace: str | Path, runtime_id: str, session_id: str,
                 daemon_origin: str, daemon_token: str,
                 client_id: str | None = None, image_transport_verified: bool = False):
        self.project_id = identifier(project_id)
        self.session_id = identifier(session_id)
        journal_path = Path(journal_path).expanduser().resolve()
        workspace = Path(workspace).expanduser().resolve()
        if journal_path.is_relative_to(workspace):
            raise ValueError('Private journal must be outside the agent workspace')
        # Validate transport configuration before creating private state. No I/O.
        self.client = QwenDaemonClient(daemon_origin, runtime_id=runtime_id,
            token=daemon_token, client_id=client_id,
            image_transport_verified=image_transport_verified)
        self.store = QwenJournal(journal_path)
        self.store.open_project(self.project_id, workspace)
        self.observer = None

    def capture(self, *, message_id: str, text: str, intent: str = 'queue',
                edit_of: str | None = None, attachments=()) -> dict:
        return self.store.capture(self.project_id, runtime_id=self.client.runtime_id,
            session_id=self.session_id, message_id=message_id, text=text, intent=intent,
            edit_of=edit_of, attachments=attachments)

    def dispatch_next(self) -> dict | None:
        """Try ONE item; active/ambiguous earlier items block, not skip or replay."""
        for entry in self.store.pending_inputs(self.client.runtime_id, self.session_id):
            if entry['state'] in ('queued', 'sending', 'accepted', 'uncertain'):
                receipt = self.client.dispatch(self.store, entry['event_id'])
                if self.observer is not None:
                    self.observer.reconcile_admissions()
                return receipt
        return None

    def event_receiver(self):
        """Opt-in: register a deny-until-caught-up observer; starts no thread/server.

        Caller runs the receiver before dispatch. This does not wire native UI
        input or establish full-packet delivery. It keeps the legacy API explicit.
        """
        from ...coordination.qwen_events import QwenEventObserver
        from ...coordination.qwen_stream import QwenEventClient
        if self.observer is None:
            self.observer = QwenEventObserver(self.store, project_id=self.project_id,
                runtime_id=self.client.runtime_id, session_id=self.session_id)
        return QwenEventClient(self.client, self.observer)

    def status(self) -> dict:
        """No user content or credentials; suitable for future Station controls."""
        entries = self.store.pending_inputs(self.client.runtime_id, self.session_id)
        return {'project_id': self.project_id, 'runtime_id': self.client.runtime_id,
                'session_id': self.session_id,
                'counts': dict(Counter(item['state'] for item in entries)),
                'coverage': coordination_coverage(),
                'observation': self.observer.status() if self.observer else None}
