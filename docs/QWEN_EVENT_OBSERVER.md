# Qwen durable event observation — M2b1

Status: implemented, opt-in; offline SQLite and real loopback HTTP/SSE fixture
qualification only. **Not an installed Qwen/Windows Desktop acceptance.** No GUI
hook, agent startup, model/GPU setting, remote request, installer or release change.
`task_control=False`, `automatic_failover=False` remain unchanged.

## Why this block exists

HTTP 202 admits a prompt; text saying "done" is not a terminal; cancelling a prompt
is not proof its tools/processes have stopped. A disconnect can lose the actual
terminal event. The coordinator now records output and its exact replay position
before advancing a queue or completing a known tool intent.

## Implemented

- `QwenEventClient`: explicit authenticated loopback GET-only subscription. Probe
  capabilities, send `Last-Event-ID` and `X-Qwen-Event-Epoch`, preserve attribution,
  follow no redirects/ambient proxies, bound framing/reconnects/deadlines and allow
  cancellation while a socket is idle. No prompt POST retry or `/load` mutation.
- `parse_sse`: bounded UTF-8 LF/CRLF parser, multiline JSON data, comments, optional
  initial BOM. Refuses duplicate JSON keys, nonfinite numbers, bad versions, unsafe
  numeric cursors and inconsistent envelope/SSE identity. Partial disconnects do
  not advance the cursor; reconnect replays the incomplete frame.
- `QwenEventObserver`: additive SQLite stream-session/frame tables. Raw JSON event
  envelope, cursor and correlated state projection commit in **one transaction**.
  Assistant text, thoughts, usage, plans, permissions and unknown events remain
  evidence. They never become authenticated user edits or delivery acknowledgements.
- New connections are `catching_up`; only a valid id-less `replay_complete` makes
  them `live`. A superseded connection cannot append or disconnect the newer one.
  Registered observer state and a 60-second freshness check gate prompt dispatch,
  trusted delivery binding and Tool Guard admission. No observer row preserves the
  M2a developer API, NOT a protected-session claim.
- Exact replay is idempotent. An ID gap, conflicting replay, changed epoch,
  truncated/degraded recording, session death/close, rewind or model change blocks
  admission until explicit reconciliation. A mere snapshot/reconnect cannot clear
  this latch. Subscriber eviction/disconnect can retry the same GET/epoch/cursor.
- `turn_complete` / `turn_error` must match a persisted runtime/session/prompt.
  Stop reason is retained. A terminal arriving before its 202 receipt stays pending;
  `reconcile_admissions()` joins it after the receipt without resending the prompt.
  Missing prompt IDs are not guessed. Ambiguous sends remain ambiguous.
- A foreground `tool_call_update` with final `completed`/`failed` status can settle
  only its exact previously recorded Guard intent. Preparation-discard and nested
  subagent events cannot settle it. Late output after a user edit, cancellation or
  stale attempt remains `needs_review`. Runtime completion is NOT proof pytest
  passed or the OS process tree drained. Turn completion with unresolved tools
  blocks further admission instead of manufacturing missing results.

## Upstream contract

Reviewed at `QwenLM/qwen-code` commit
`f024b37689f3abab4bbfb249f44af55d34effc77` (same pin as M2a):

- `docs/developers/daemon/09-event-schema.md`
- `packages/acp-bridge/src/eventBus.ts`
- `packages/sdk-typescript/src/daemon/DaemonTransport.ts`
- `packages/cli/src/acp-integration/session/emitters/tool-call-emitter.ts`

The envelope is `{id?, v:1, type, data, promptId?, originatorClientId?, _meta?}`.
Tool updates are ACP data under `session_update`; the terminal turn's promptId can
be in data. If both envelope and data have a promptId they must agree. Only the
transport's registered session can update that session. Control frames such as
`state_resync_required`, `client_evicted`, `history_truncated`, `replay_complete`
may have no ID; they must never inherit the last event's ID.

Strict scope is deliberate: old daemons without an event epoch are refused rather
than guessing a restart from a number. Bare-CR/compressed/oversized SSE is not
qualified. A corrupt/incompatible stream fails closed. This is an observer, not a
new daemon or a proxy intended to replace Qwen's native frontends.

## Explicit use

```python
# managed = QwenCodeAdapter().open_managed_input(...existing managed session...)
receiver = managed.event_receiver()  # SQLite registration only; starts nothing
# In a caller-owned thread, before dispatch:
# receiver.run(stop=caller_stop_event, max_reconnects=3, max_seconds=300)
# Wait for managed.status()['observation']['state'] == 'live'.
# Capture exact human input, then call managed.dispatch_next().
```

The receiver has an explicit bounded lifetime. Production supervision is not
installed by this code. A 60-second last-seen check only bounds stale observer
liveness; it is not instantaneous revocation of a permit already returned. A
failed SQLite write prevents cursor/state advancement; errors must propagate,
not be hidden by a frontend. Output stored here can contain private user data;
keep the database outside the agent worktree and out of source control. Tokens
are held in the client, never journaled by the receiver.

## Boundaries that are still open

1. Complete native Desktop/Telegram/new/edit/steer ingress is NOT wired. SSE echoes
   cannot replace persistence before forwarding a human message.
2. `replay_complete`, assistant output and a terminal event do NOT prove that the
   full requirement packet reached the model without truncation. This observer
   never invokes `bind_delivered_prompt` or `acknowledge_delivery`.
3. Guard v1 is foreground top-level admission. It is not a sandbox; hooks, direct
   mutations, nested agents, shell children and user-owned processes require
   separate coverage. No process is started/killed/drained here.
4. SQLite continuity does not make a failed node safe to replace. Gap recovery,
   queue cancellation, actual process ownership/draining and complete packet
   delivery remain required before automatic failover is enabled.
5. Existing installed clients are unchanged. Registering a managed session is a
   developer action, not an implicit claim about every existing chat.

## Validation

```text
python -m pytest -q tests/test_coordination_journal.py tests/test_coordination_qwen.py tests/test_coordination_qwen_events.py
python tools/coordination_smoke.py
python tools/coordination_qwen_smoke.py
python tools/coordination_qwen_events_smoke.py
```

New: 89 tests. Combined coordination: 220 passed. Three synthetic smokes PASS.
Broader local run: 368 passed, 1 skipped, 1 deselected with two GUI-dependent
modules excluded. Full local collection fails because customtkinter is unavailable;
no stubs were used. Full unchanged Windows/Linux matrix and installer checks run
in GitHub CI. No live agent, model endpoint or user machine was contacted.

Next M2b2: native managed ingress + full-packet delivery evidence and owned-process
completion/draining in a disposable real Qwen session. Keep M3 scheduling gated.
