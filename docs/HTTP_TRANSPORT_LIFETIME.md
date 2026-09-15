# Bounded Qwen HTTP lifetime — M2b1 hardening

Status: implemented and tested with local loopback HTTP/SQLite fixtures on Linux.
Not an installed Qwen Desktop, Windows driver or remote-model qualification.
`task_control`, `automatic_failover` and `live_runtime_verified` remain false.

## Reproducible defects addressed

1. The SSE deadline previously started after `/capabilities`. A slow capabilities
   response ignored the caller's stop event and the stated observation budget.
2. Stopping at an event limit closed `HTTPConnection`, not the retained partial
   `HTTPResponse`. A response owns a socket file separately, including when the
   connection detaches its socket for `Connection: close`.
3. A stop/deadline racing a SQLite or protocol exception could convert that error
   into a normal return. Private storage I/O failures must remain failures as well.

The new private `http_lifetime.bounded_response` owns one connection and response.
One absolute deadline includes connect, headers and the body consumed by its caller.
A caller-owned stop event is checked before connect and after socket attachment.
A watcher only shuts down the owned socket; the request thread closes the response
and connection and joins the watcher. It never closes a buffered reader from a
second thread, executes a tool, kills another process or logs a token/payload.

The SSE receiver shares this budget with the capabilities preflight and limits
reconnect backoff to the remaining time. Only transport interruption is a normal
stop. Event projection/storage errors latch `resync_required` and propagate, even
when cancellation arrives concurrently. No cursor is committed for an incomplete
frame. Prompt POSTs still have at most one attempt; ambiguous admission blocks the
outbox rather than replaying side effects.

A socket read timeout and a wall-clock deadline are different: regular trickle bytes
can prevent a read timeout but do not extend the exchange deadline. Connect remains
bounded by the socket timeout; a stop before a socket exists is rechecked immediately
once connect returns. This network budget is not a guarantee that arbitrary storage
or application code can be preempted. SQLite/application failure is not swallowed.

## Tests and scope

34 new tests cover cancellation/deadlines in capabilities headers/body/trickle,
SSE headers/idle/partial frames/heartbeats, response cleanup including detached
connections, pre-attachment cancellation, non-retried uncertain POSTs, bounded
backoff, invalid budgets, watcher cleanup and concurrent storage/protocol errors.

Commands:

```text
python -m pytest -q tests/test_coordination_http_lifetime.py
python -m pytest -q tests/test_coordination_journal.py tests/test_coordination_qwen.py tests/test_coordination_qwen_events.py tests/test_coordination_http_lifetime.py
```

Final local results: 254 coordination tests passed; the 34-test lifetime suite
passed five additional complete repetitions. All three existing synthetic smokes
passed. Broader non-GUI run: 402 passed, 1 skipped, 1 deselected, with the two
customtkinter-dependent modules excluded. Full GUI collection cannot run in this
container because that dependency is absent; no stub or fake PASS was substituted.
A selected regression subset failed against the exact previous transport files,
then passed with the fix. No actual agent/server/user environment was contacted.

## Windows CI is still a separate acceptance gate

The previous ab238e9 run 34897467428 passed Linux. Both Windows jobs exceeded the
six-hour Actions limit; their logs contain progress dots but no exact stuck test or
stack. These transport fixes must not be advertised as a proven root-cause fix for
that particular Windows hang without a new bounded Windows run.

Diagnostics commit d3ebf9c adds `-vv`, Python faulthandler stack dumps, test-step
and job deadlines, and retained JUnit results. Follow-up runs 34927010021 and
34927013300 failed before runner assignment (empty steps, runner_id=0). The
available API does not establish the infrastructure/account cause. Do not change
billing/security or repeatedly retry jobs to hide this limitation.

Next: one bounded Windows/Linux run when runners are available, then M2b2 native
human ingress, actual full-packet delivery and owned-process draining. Automatic
handoff is not enabled by merely hardening this transport.
