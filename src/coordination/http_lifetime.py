"""One owned HTTP exchange with a wall-clock deadline and explicit cancellation.

Private transport primitive: the caller still validates origin, auth and protocol.
A socket read timeout alone does not bound a peer that keeps trickling bytes. The
watcher only shuts down its own socket; the requesting thread closes all resources.
No callbacks, retries, background registration or exception/payload logging.
"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import math
import socket
import threading
import time
from typing import Iterator


class RequestInterrupted(TimeoutError):
    """The owned request was cancelled or exceeded its wall-clock budget."""


def interrupted(stop: threading.Event | None, deadline: float) -> bool:
    return (stop is not None and stop.is_set()) or time.monotonic() >= deadline


@contextmanager
def bounded_response(host: str, port: int, method: str, path: str, *,
                     headers: dict, body: bytes | None = None,
                     timeout: float, deadline: float,
                     stop: threading.Event | None = None) -> Iterator[http.client.HTTPResponse]:
    """Use one connection, never redirect/proxy/retry, close even partial responses.

    The deadline includes connect, headers AND the body consumed inside the context.
    Cancellation can arrive before the socket is attached; check again after connect.
    HTTPResponse owns a socket file even when HTTPConnection detaches on Connection:
    close, so closing only the connection would leak a partially consumed response.
    """
    if (type(timeout) not in (float, int) or not math.isfinite(timeout) or timeout <= 0
            or type(deadline) not in (float, int) or not math.isfinite(deadline)):
        raise ValueError('Invalid HTTP time budget')
    if interrupted(stop, deadline):
        raise RequestInterrupted('HTTP request cancelled or deadline reached')
    conn = http.client.HTTPConnection(host, port, timeout=min(timeout, max(0.001, deadline - time.monotonic())))
    done = threading.Event()
    lock = threading.Lock()
    owned_socket = None
    response = None

    def shutdown_owned():
        with lock:
            if owned_socket is not None:
                try:
                    owned_socket.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def watch():
        while not done.wait(min(0.05, max(0.001, deadline - time.monotonic()))):
            if interrupted(stop, deadline):
                shutdown_owned()
                return

    watcher = threading.Thread(target=watch, name='laas-qwen-http-watchdog', daemon=True)
    watcher.start()
    try:
        conn.connect()
        with lock:
            owned_socket = conn.sock
        if interrupted(stop, deadline):
            raise RequestInterrupted('HTTP request cancelled or deadline reached')
        conn.request(method, path, body=body, headers=headers)
        response = conn.getresponse()
        if interrupted(stop, deadline):
            raise RequestInterrupted('HTTP request cancelled or deadline reached')
        yield response
    except (OSError, http.client.HTTPException) as exc:
        if interrupted(stop, deadline):
            raise RequestInterrupted('HTTP request cancelled or deadline reached') from exc
        raise
    finally:
        done.set()
        # Do not call response.close() from the watcher: it can contend with a
        # buffered reader lock on another thread. SHUT_RDWR wakes the read first.
        shutdown_owned()
        try:
            if response is not None:
                response.close()
        finally:
            conn.close()
            watcher.join(timeout=1)
