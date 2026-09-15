# Qwen managed ingress and Tool Guard — M2a / M2b1

**Status: opt-in implementation + offline HTTP contract tests. Not live Desktop
capture. Automatic failover remains disabled. No installer release or production
configuration change.**

## M2b1 update

Explicit REST/SSE event observation and atomic result/cursor persistence now exist;
see [QWEN_EVENT_OBSERVER.md](QWEN_EVENT_OBSERVER.md) for current status and limits.
The original M2a sections below describe the admission boundary. No native frontend
is automatically wired and full-packet delivery/process draining remain open.

## Implemented in M2a

- `QwenCodeAdapter.open_managed_input(...)` returns an explicit
  `ManagedQwenInput` binding. Opening it starts nothing and contacts no server.
  Its private SQLite journal must be outside the agent's working directory.
- `capture(...)` commits the exact human message/edit, attachment digests and
  outbox row in one transaction before returning a receipt. A queued correction
  immediately advances M1's requirement revision, even while a model is busy.
  Keep message IDs across retries; attachments reference stored immutable bytes.
- `dispatch_next()` attempts one item in FIFO order. Before the POST, it commits
  `sending`. A crash, connection loss, malformed receipt or ambiguous HTTP result
  blocks replay. Canceling a queued send keeps the original human requirement in
  the journal; it is not a semantic retraction.
- `QwenDaemonClient` uses only an explicitly supplied, authenticated loopback
  daemon. It probes actual capability tags, ignores ambient HTTP proxies, follows
  no redirects, and sends the documented ACP prompt body to `/session/:id/prompt`.
  `202 {promptId,lastEventId}` means **accepted into the runtime queue**, never
  delivery to the model or completion. Original images can be serialized from the
  journal, but dispatch requires explicit qualification of image transport.
- `QwenToolGuard` and `running_tool_guard(...)` implement the official required
  external Tool Guard v1 handshake and prepare routes. A permit persists M1's
  tool intent first and requires a trusted prompt/delivery binding, current user
  revision, current attempt/lease and an explicit application-owned tool policy.
  Duplicate request or full runtime/session/prompt/tool-call tuples are refused.
  Unknown, nested-agent and background tools are denied. An empty rule map denies
  all tools; the LLM cannot label its own command read-only.
- A trusted lifecycle observer can correlate a terminal prompt or tool result.
  No observer is automatically started. M2b1 adds an explicit receiver. Late/stale
  tool results become evidence requiring review, not a new permit. HTTP exposes no delivery-ack, reconciliation,
  arbitrary-execution or user-input API.

`get_coordination_capabilities()` reports the real boundaries; the existing
`get_capabilities().task_control` remains `False`. Existing Desktop, CLI, Telegram
and other runtimes are unchanged. These methods are developer integration points,
not a second chat interface.

## Upstream contract reviewed

Reference snapshot: QwenLM/qwen-code commit
`f024b37689f3abab4bbfb249f44af55d34effc77`.

- [External Tool Guard design and implementation map](https://github.com/QwenLM/qwen-code/blob/f024b37689f3abab4bbfb249f44af55d34effc77/docs/design/2026-07-30-external-tool-guard-provider.md)
- [Runtime capability registry](https://github.com/QwenLM/qwen-code/blob/f024b37689f3abab4bbfb249f44af55d34effc77/packages/cli/src/serve/capabilities.ts)
- [Daemon REST reference](https://github.com/QwenLM/qwen-code/blob/f024b37689f3abab4bbfb249f44af55d34effc77/docs/developers/daemon-rest-api-reference.md)
- [Full protocol](https://github.com/QwenLM/qwen-code/blob/f024b37689f3abab4bbfb249f44af55d34effc77/docs/developers/qwen-serve-protocol.md)

`features` is an array of capability tag strings, not a boolean map. This client
requires `session_prompt`, `non_blocking_prompt`, `session_events` and
`external_tool_guard`. Unknown extra tags are harmless; absent required tags or
malformed shape prevent the prompt POST. A version string alone is not proof.

Guard v1 uses Bearer authentication, `POST /v1/handshake` and `POST /v1/prepare`.
A compatible Qwen daemon activates it at startup with required mode, a loopback
endpoint and `QWEN_CODE_EXTERNAL_TOOL_GUARD_TOKEN`. The provider checks the exact
version/fields/nonce/request identity. Only the local caller manages its lifetime
and credentials; Station does not modify or start an installed Qwen instance here.

## Important distinction: persistence, admission, delivery, execution

```text
human input -> durable event + queued
                -> sending (committed before POST)
                  -> accepted (202, runtime prompt ID)
                    -> actual delivery evidence (NOT implemented by this client)
                      -> trusted packet binding
                        -> prepare permit + persisted tool intent
                          -> executor outside Station
                            -> correlated lifecycle result / needs review
```

`bind_delivered_prompt` is an internal integration API. Do not call it merely on
HTTP 202 or a model saying "I read the instructions". A qualified adapter must
establish that the complete M1 packet reached the matching prompt, including the
current raw messages, edits and attachments and the actual context budget. Until
then a live Guard correctly **denies** an unbound prompt's tools.

The submitted input is one queued human message, not automatically the full M1
handoff. M2b1 adds explicit subscribe-before-dispatch and durable SSE cursor/epoch
tracking. Compaction/tokenization and full packet-delivery evidence remain M2b2 work.

## Queue and crash rules

- One outstanding send/accepted/uncertain item per runtime/session. Later inputs
  remain durable even while an earlier item is executing.
- Reopening the same journal does not turn `sending` back into `queued`.
- Only explicit operator reconciliation, with a note, expected current revision
  and proof that the runtime did NOT admit the prompt, permits retry.
- If the runtime may have admitted it, inspect its pending prompts/transcript;
  do not guess, auto-resend or bind a new attempt to the old prompt.
- `runtime_id` identifies one managed daemon lifetime and must change after
  restart. Never quietly retarget queued/uncertain rows to another daemon. A
  future recovery adapter must reconcile old receipts first.
- A failed/malformed receipt has no usable prompt ID. This stage deliberately
  cannot automatically recover such a runtime mutation.
- Canceling an admitted prompt is not canceling all queued daemon prompts. This
  stage only offers local **undispatched** cancellation; live queue/process
  cancellation is not claimed.
- Steering input is stored immediately but not silently sent as a normal prompt.
  Unqualified steering and slash commands are refused before POST. They remain in
  the journal/outbox for explicit handling; following items are not skipped.

## Minimal developer use

```python
from src.agents.qwen_code.adapter import QwenCodeAdapter

bridge = QwenCodeAdapter().open_managed_input(
    journal_path=private_data_dir / 'coordination' / 'journal.sqlite3',
    project_id=project_id, workspace=workspace,
    runtime_id=managed_daemon_instance_id, session_id=verified_session_id,
    daemon_origin=verified_loopback_origin,
    daemon_token=token_from_secret_store,
)
receipt = bridge.capture(message_id=stable_message_id, text=original_text)
# Only now can a frontend acknowledge that the message was saved.
# Explicit dispatch to a suitably configured live daemon is a separate action:
# admission = bridge.dispatch_next()
# admission is NOT a delivery acknowledgement or a task success result.
```

No script should copy a token into source/config/logs. The Guard and daemon use
separate tokens. Local guard HTTP rejects browser Origins, unexpected Hosts,
missing/wrong authentication, duplicate JSON keys and oversized requests. It has
no public network listener and does not log payloads. This is still not a security
boundary against hostile code running as the same Windows user; private data and
process permissions remain necessary.

## Coverage gaps — do not enable automatic authority transfer yet

1. Native Desktop, direct daemon/ACP, Telegram and extension ingress can bypass
   this facade. All relevant authenticated message/edit/queue paths must be wired
   before the session can be marked protected. `UserPromptSubmit` alone is not a
   universal raw-input boundary; do not trust model-authored metadata as human input.
2. Upstream external Guard v1 covers foreground top-level final tool invocation,
   not hooks, slash commands, management APIs or a command's child-process effects.
   Required mode rejects independent nested AgentCore executions. Do not silently
   enable nested subagents while claiming whole-session fencing.
3. The provider is not a sandbox and does not kill OS processes or retract a permit
   already delivered. A correction racing an admitted executor leaves its result
   requiring reconciliation. Owned-process cancellation/draining is still needed.
4. M2b1 implements an explicit SSE receiver/result recorder with synthetic tests;
   no installed live Qwen instance has been qualified yet.
   Prompt completion must not be guessed to mean all tool outcomes are resolved.
5. No current GUI, agent setting, GPU mode, startup entry or model endpoint changed.

## Verification and next step

```text
python -m pytest -q tests/test_coordination_journal.py tests/test_coordination_qwen.py
python tools/coordination_smoke.py
python tools/coordination_qwen_smoke.py
```

M2a adds 78 tests, including real loopback HTTP exchanges with synthetic peers.
It does not claim testing a live Qwen daemon, model, Windows Desktop or remote
server. The synthetic smoke binds a synthetic full packet explicitly and labels
that fact in its output. Do not copy that simulated acknowledgement into a live
frontend.

M2b2: pin/probe the installed daemon, connect authenticated native human ingress to
the M2b1 epoch/cursor-aware observer, prove full packet delivery, and fence/drain
actual tools/processes in a disposable project. Keep automatic failover disabled
until an edit during generation/tool execution and restart/replay are accepted
end-to-end. Only then implement M3 node/task scheduling.
