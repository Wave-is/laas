"""Explicit loopback HTTP adapters for Qwen daemon admission and Tool Guard v1.

No background startup, token discovery, proxies, redirects or external endpoints.
Only the two documented Guard routes are exposed; queue/acknowledgement/reconciliation
are local Python APIs, not model-callable HTTP commands.
"""
from __future__ import annotations

from contextlib import contextmanager
import hmac
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
import threading
from urllib.parse import quote, urlsplit

from .qwen import ProtocolError, QwenJournal, QwenToolGuard, identifier

MAX_REQUEST = 1024 * 1024
MAX_RESPONSE = 64 * 1024


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError('Duplicate JSON key')
        result[key] = value
    return result


def _decode(data: bytes):
    def invalid_constant(value):
        raise ProtocolError('Non-finite JSON constant')
    return json.loads(data.decode('utf-8'), object_pairs_hook=_unique_object,
                      parse_constant=invalid_constant)


def _secret(token: str) -> str:
    if (not isinstance(token, str) or not token.strip()
            or len(token.encode('utf-16-le')) // 2 > 8192
            or any(ord(c) < 32 or ord(c) == 127 for c in token)):
        raise ValueError('Invalid bearer token')
    # HTTP header values used here must be representable without implicit encoding.
    try:
        token.encode('ascii')
    except UnicodeEncodeError as exc:
        raise ValueError('Use an ASCII bearer token') from exc
    return token


def _origin(url: str) -> tuple[str, int]:
    parsed = urlsplit(url)
    if (parsed.scheme != 'http' or parsed.hostname not in ('127.0.0.1', 'localhost')
            or parsed.username is not None or parsed.password is not None
            or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('Expected origin-only loopback http URL')
    port = parsed.port if parsed.port is not None else 80
    if not 1 <= port <= 65535:
        raise ValueError('Invalid port')
    return '127.0.0.1', port  # do not resolve localhost through DNS


class QwenDaemonClient:
    """Narrow v1 admission client. Accepted != delivered != completed.

    Caller must attach to an existing *managed* session; session creation and SSE
    integration are not implemented here. runtime_id must change after a daemon
    instance changes; never rebind uncertain requests to a new process silently.
    """

    def __init__(self, origin: str, *, runtime_id: str, token: str,
                 client_id: str | None = None, timeout: float = 10,
                 image_transport_verified: bool = False):
        self.host, self.port = _origin(origin)
        self.runtime_id = identifier(runtime_id)
        self._token = _secret(token)
        self.client_id = identifier(client_id) if client_id is not None else None
        if not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise ValueError('Timeout must be between zero and 30 seconds')
        self.timeout = timeout
        self.image_transport_verified = image_transport_verified

    def _request(self, method: str, path: str, body: dict | None = None):
        wire = None if body is None else json.dumps(body, ensure_ascii=False, allow_nan=False).encode('utf-8')
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        headers = {'Authorization': 'Bearer ' + self._token, 'Accept': 'application/json'}
        if self.client_id:
            headers['X-Qwen-Client-Id'] = self.client_id
        if wire is not None:
            headers['Content-Type'] = 'application/json'
        try:
            conn.request(method, path, wire, headers)
            response = conn.getresponse()
            status = response.status
            data = response.read(MAX_RESPONSE + 1)
            if len(data) > MAX_RESPONSE:
                raise ProtocolError('Oversized daemon response')
            if response.getheader('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                raise ProtocolError('Expected JSON daemon response')
            value = _decode(data)
            if not isinstance(value, dict):
                raise ProtocolError('Expected daemon response object')
            return status, value
        finally:
            conn.close()

    def capabilities(self) -> dict:
        status, value = self._request('GET', '/capabilities')
        if (status != 200 or not isinstance(value.get('features'), list)
                or not all(isinstance(x, str) for x in value['features'])):
            raise ProtocolError('Daemon capabilities not confirmed')
        required = ('session_prompt', 'non_blocking_prompt', 'session_events', 'external_tool_guard')
        if not set(required).issubset(value['features']):
            raise ProtocolError('Required nonblocking prompt/events/external Tool Guard not advertised')
        return value

    def dispatch(self, store: QwenJournal, event_id: str) -> dict:
        """One POST at most; uncertainty blocks the queue until human reconciliation."""
        item = store.input_status(event_id)
        if item['runtime_id'] != self.runtime_id:
            raise ProtocolError('Input belongs to a different daemon instance')
        store.check_observation(self.runtime_id, item["session_id"])
        body = store.prepare_request(event_id)
        if item['payload']['attachments'] and not self.image_transport_verified:
            raise ProtocolError('Image transport has not been qualified for this runtime')
        self.capabilities()  # no POST before read-only preflight succeeds
        store.claim_input(event_id)  # durable intent BEFORE touching the network
        try:
            status, value = self._request('POST', '/session/' + quote(item['session_id'], safe='') + '/prompt', body)
            if status == 202:
                identifier(value.get('promptId'))
                cursor = value.get('lastEventId')
                if type(cursor) is not int or cursor < 0:
                    raise ProtocolError('Malformed queued prompt receipt')
                return store.record_admission(event_id, state='accepted',
                                               prompt_id=value['promptId'], last_event_id=cursor)
            # These rejection classes cannot authorize execution. No auto-retry even
            # here; 409/429/5xx/timeouts and malformed responses remain uncertain.
            state = 'rejected' if status in (400, 401, 403, 404, 413, 415) else 'uncertain'
            return store.record_admission(event_id, state=state)
        except Exception:
            # If this write also fails, 'sending' remains durably blocking.
            return store.record_admission(event_id, state='uncertain')


class _GuardServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = True
    allow_reuse_address = False

    def handle_error(self, request, client_address):
        # No payload, token, exception or user path on stderr.
        pass


@contextmanager
def running_tool_guard(guard: QwenToolGuard, token: str, *, port: int = 0):
    """Explicit start/stop for a local v1 provider; caller owns lifetime and secret."""
    token = _secret(token)
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError('Invalid port')

    class Handler(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.0'

        def setup(self):
            super().setup()
            self.connection.settimeout(3)

        def log_message(self, format, *args):
            pass

        def _reply(self, status, value):
            body = json.dumps(value, ensure_ascii=True, allow_nan=False).encode('ascii')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._reply(405, {'error': 'Method not allowed'})

        def do_POST(self):
            # Reject browser origins and DNS-rebinding Host values. No management,
            # arbitrary reconciliation or authority-binding route is exposed.
            expected_hosts = ('127.0.0.1:' + str(self.server.server_port),
                              'localhost:' + str(self.server.server_port))
            if self.headers.get('Origin') is not None or self.headers.get_all('Host') not in ([expected_hosts[0]], [expected_hosts[1]]):
                return self._reply(403, {'error': 'Request refused'})
            auth = self.headers.get_all('Authorization', [])
            if len(auth) != 1 or not hmac.compare_digest(auth[0].encode('utf-8'), ('Bearer ' + token).encode('utf-8')):
                return self._reply(401, {'error': 'Unauthorized'})
            if self.path not in ('/v1/handshake', '/v1/prepare'):
                return self._reply(404, {'error': 'Unknown route'})
            if self.headers.get('Transfer-Encoding') is not None:
                return self._reply(400, {'error': 'Unsupported framing'})
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit():
                return self._reply(411, {'error': 'Content length required'})
            length = int(lengths[0])
            if not 0 < length <= MAX_REQUEST:
                return self._reply(413, {'error': 'Invalid request size'})
            if self.headers.get('Content-Type', '').split(';')[0].strip().lower() != 'application/json':
                return self._reply(415, {'error': 'JSON required'})
            try:
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ProtocolError('Incomplete request')
                value = _decode(raw)
                if not isinstance(value, dict):
                    raise ProtocolError('Expected object')
                if self.path == '/v1/handshake':
                    if (set(value) != {'protocolVersion', 'nonce', 'client'}
                            or type(value['protocolVersion']) is not int
                            or value['protocolVersion'] != 1 or value['client'] != 'qwen-code'):
                        raise ProtocolError('Incompatible handshake')
                    identifier(value['nonce'])
                    response = {'protocolVersion': 1, 'nonce': value['nonce'], 'capabilities': {'prepare': True}}
                else:
                    response = guard.prepare(value)
                return self._reply(200, response)
            except Exception:
                return self._reply(400, {'error': 'Request refused'})

    server = _GuardServer(('127.0.0.1', port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    try:
        yield 'http://127.0.0.1:' + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
