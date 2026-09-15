"""Bounded, explicit REST/SSE receiver for the qualified Qwen v1 contract.

Only GET /capabilities and GET /session/:id/events. Never retry a prompt POST,
load/rewind a session, answer permissions or infer delivery from output text.
"""
from __future__ import annotations

import http.client
import math
import threading
import time
from typing import BinaryIO, Iterator
from urllib.parse import quote

from .http_lifetime import RequestInterrupted, bounded_response, interrupted
from .qwen import ProtocolError
from .qwen_events import MAX_FRAME_BYTES, ObservationLost, QwenEventObserver, StaleObserver, StreamInterrupted, validate_event
from .qwen_http import QwenDaemonClient, _decode


def parse_sse(stream: BinaryIO) -> Iterator[dict | None]:
    """Parse UTF-8 LF/CRLF SSE, bounded before JSON allocation.

    None is a heartbeat/comment-only frame. Partial EOF is not committed; the
    durable cursor causes replay on reconnect. Id-less controls never inherit
    the last durable id. JSON duplicate keys/NaN and conflicting SSE ids refuse.
    """
    data = []
    fields = {}
    size = 0
    first = True
    while True:
        raw = stream.readline(MAX_FRAME_BYTES + 1)
        if not raw:
            if data or fields:
                raise StreamInterrupted('SSE ended in the middle of a frame')
            return
        size += len(raw)
        if size > MAX_FRAME_BYTES:
            raise ProtocolError('SSE frame exceeds the byte limit')
        if first:
            raw = raw.removeprefix(b'\xef\xbb\xbf')
            first = False
        if not raw.endswith(b'\n'):
            raise StreamInterrupted('Incomplete SSE line')
        line = raw[:-1].removesuffix(b'\r').decode('utf-8', errors='strict')
        if '\r' in line or '\x00' in line:
            raise ProtocolError('Invalid SSE framing')
        if line == '':
            if data:
                value = validate_event(_decode('\n'.join(data).encode('utf-8')))
                if 'event' in fields and fields['event'] != value['type']:
                    raise ProtocolError('SSE event type disagrees with envelope')
                if 'id' in fields:
                    if (not fields['id'].isascii() or not fields['id'].isdigit()
                            or str(value.get('id')) != fields['id']):
                        raise ProtocolError('SSE id disagrees with envelope')
                yield value
            elif fields:
                raise ProtocolError('SSE identity without an event')
            else:
                yield None
            data, fields, size = [], {}, 0
            continue
        if line.startswith(':'):
            continue
        key, sep, value = line.partition(':')
        value = value[1:] if sep and value.startswith(' ') else value
        if key == 'data':
            data.append(value)
        elif key in ('event', 'id'):
            if key in fields:
                raise ProtocolError('Duplicate SSE identity')
            fields[key] = value
        # Unknown/retry fields are permitted by SSE but cannot cause a mutation.


class QwenEventClient:
    """Caller-owned receiver. Fresh connection per reconnect; secrets are never logged."""
    def __init__(self, daemon: QwenDaemonClient, observer: QwenEventObserver):
        if daemon.runtime_id != observer.runtime_id:
            raise ValueError('Observer belongs to another daemon lifetime')
        self.daemon, self.observer = daemon, observer

    def receive_once(self, *, stop: threading.Event | None = None,
                     max_events: int = 10000, max_seconds: float = 300) -> int:
        if type(max_events) is not int or not 1 <= max_events <= 100000:
            raise ValueError('Invalid event limit')
        if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600:
            raise ValueError('Invalid observation deadline')
        stop = stop or threading.Event()
        if stop.is_set():
            return 0
        deadline = time.monotonic() + max_seconds
        try:
            self.daemon.capabilities(stop=stop, deadline=deadline)
        except RequestInterrupted:
            self.observer.disconnect()
            return 0
        except Exception as exc:
            self.observer.disconnect(fault=not isinstance(exc, (OSError, http.client.HTTPException)))
            raise
        saved = self.observer.status()
        if saved['state'] == 'resync_required':
            raise ObservationLost('Reconciliation required before reconnect')
        headers = {'Authorization': 'Bearer ' + self.daemon._token,
                   'Accept': 'text/event-stream', 'Accept-Encoding': 'identity',
                   'Cache-Control': 'no-cache', 'Last-Event-ID': str(saved['cursor'])}
        if saved['epoch'] is not None:
            headers['X-Qwen-Event-Epoch'] = saved['epoch']
        if self.daemon.client_id:
            headers['X-Qwen-Client-Id'] = self.daemon.client_id
        count = 0
        application_failure = None
        try:
            with bounded_response(
                self.daemon.host, self.daemon.port, 'GET',
                '/session/' + quote(self.observer.session_id, safe='') + '/events',
                headers=headers, timeout=self.daemon.timeout, deadline=deadline, stop=stop,
            ) as response:
                if response.status != 200:
                    raise ProtocolError('Event subscription rejected; no redirect or mutation attempted')
                if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'text/event-stream':
                    raise ProtocolError('Expected SSE response')
                if response.getheader('Content-Encoding', 'identity').strip().lower() != 'identity':
                    raise ProtocolError('Encoded SSE transport is not qualified')
                epochs = response.headers.get_all('X-Qwen-Event-Epoch', [])
                if len(epochs) != 1:
                    raise ProtocolError('Exactly one event epoch is required')
                self.observer.connect(epochs[0])
                for event in parse_sse(response):
                    if interrupted(stop, deadline):
                        break
                    try:
                        if event is None:
                            self.observer.heartbeat()
                        else:
                            self.observer.ingest(event)
                            count += 1
                            if count >= max_events:
                                break
                    except (StaleObserver, ObservationLost, StreamInterrupted):
                        raise
                    except Exception as exc:
                        # Keep application I/O failures distinct from a cancelled
                        # socket read, including OSError from private storage.
                        application_failure = exc
                        raise
            return count
        except (StaleObserver, ObservationLost):
            raise
        except (OSError, http.client.HTTPException):
            if application_failure is not None:
                self.observer.disconnect(fault=True)
                raise application_failure
            if interrupted(stop, deadline):
                return count
            raise
        except Exception:
            # A concurrent stop must never turn a storage/protocol error into PASS.
            self.observer.disconnect(fault=True)
            raise
        finally:
            self.observer.disconnect()

    def run(self, *, stop: threading.Event, max_reconnects: int = 3,
            max_seconds: float = 300) -> dict:
        """Bounded read-only reconnect loop. No automatic background registration."""
        if type(max_reconnects) is not int or not 0 <= max_reconnects <= 10:
            raise ValueError('Invalid reconnect budget')
        if type(max_seconds) not in (int, float) or not math.isfinite(max_seconds) or not 0 < max_seconds <= 3600:
            raise ValueError('Invalid observation deadline')
        deadline = time.monotonic() + max_seconds
        connections, events = 0, 0
        for index in range(max_reconnects + 1):
            remaining = deadline - time.monotonic()
            if stop.is_set() or remaining <= 0:
                break
            connections += 1
            try:
                events += self.receive_once(stop=stop, max_seconds=remaining)
            except (OSError, http.client.HTTPException):
                pass  # reconnect only GET; never claim the agent stopped or retry POST
            if self.observer.status()['state'] == 'resync_required':
                break
            if index < max_reconnects:
                stop.wait(min(0.25 * (2**index), 2.0, max(0, deadline - time.monotonic())))
        return {'connections': connections, 'events': events, 'status': self.observer.status()}
