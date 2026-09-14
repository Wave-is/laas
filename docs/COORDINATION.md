# Managed coordination: durable conversation first

Status: **M1 implemented, opt-in library + offline smoke; not connected to live
Qwen/Hermes input or tools yet.** This does not change the installed alpha.1 EXE.

## Why a summary is not the source of truth

The user's task evolves during a conversation. A model may omit a new restriction
from `PROJECT_STATE.md`, misunderstand it, or disappear before writing any handoff.
Therefore an LLM is never responsible for persisting user input. The input boundary
must write each original message/edit and its attachments before returning a
successful receipt to the frontend, queue or agent. Failure to commit is a failed
send, not permission to continue without a journal.

`src/coordination/JournalStore` provides this boundary. SQLite is local to the
coordinator PC, with WAL, FULL synchronous writes, transactions and foreign keys.
This protects acknowledged data against process failure within SQLite/filesystem
semantics, not against storage hardware failure, malicious local access or loss of
the entire machine. Backups remain necessary. Do not place a live database on SMB.

A future integration opens `data_dir()/coordination/journal.sqlite3` explicitly.
Importing the package creates nothing. The library uses only Python's standard
library, starts no processes and contacts no model servers. Runtime databases,
attachments, conversations and local raw logs must never be committed to Git.

## Implemented contract

1. `open_project`: register an explicit workspace. A second project ID cannot bind
   the same normalized workspace and bypass the project-level single-writer check.
2. `append_user`: exact UTF-8 text, stable source/message ID and optional stored
   attachment digests. Return is a durable receipt, including event ID and revision.
   Repeated delivery of the same source ID/content returns the same event. Reusing
   an ID with different content is an error. Retries must keep the original ID.
3. Edits append a new `user.edit` pointing to the old event ID; no overwrite. Every
   human message conservatively advances the project revision, even a clarification
   or acknowledgement. No model classifies a message as 'unimportant' for storage.
4. `put_attachment`: content-addressed immutable bytes + media type, stored in the
   same database; a screenshot is not merely a path to a disappearing temporary file.
   Limits: 20 MiB per attachment, 16 digests per user event, 1 MiB per event payload.
   Unsupported size is an explicit error; never silently truncate an input.
5. `record_runtime_event`: exact assistant chunks/messages, tool output and notices
   from trusted adapters. These remain evidence, not user commands. Late output from
   revoked attempts can be retained without restoring the old attempt's authority.
6. `create_task` and `start_attempt`: each task has a monotonic attempt epoch and
   expiring lease. Explicit replacement or expiry revokes the old attempt. Writers
   are exclusive within a project; multiple read-only attempts are permitted.
7. `prepare_delivery`: a persisted/replayable packet contains **all original user
   events**, including edits, plus the current task's history. A model-authored
   summary cannot replace them. The adapter supplies a local tokenizer/budget
   counter which must account for the actual chat template, tools and images.
   Overflow raises `ContextOverflow` instead of truncating or silently compressing.
8. `acknowledge_delivery`: the trusted transport acknowledges a particular packet
   ID/digest/revision. An input arriving in transit invalidates the old receipt.
   This is evidence of delivery, **not proof of semantic comprehension**. It is not
   a tool which the LLM can call to claim it has read its own instructions.
9. `begin_action`: before a tool is launched, require the current attempt, unexpired
   lease, delivered current revision, appropriate write permission and no unresolved
   previous action. Persist the intent first. Duplicate tool IDs raise an explicit
   conflict rather than running again. The adapter, not the model, classifies tools.
10. `finish_action`: preserve output even if the model/lease/revision became stale.
    Such output is `needs_review`, never automatic success for the new attempt.
11. `cancel_attempt`: revoke future admissions. This does NOT terminate an OS process
    and does NOT remove uncertain running actions. Reassignment waits for an operator
    to verify process/files and call `reconcile_action` with a note/current revision.
12. `finish_attempt`: require the current revision and resolved tools, then propose
    `awaiting_review`. A model's 'done' is not a claim that tests or requirements pass.
13. `record_summary`: optional derived text with an explicit source high-water mark.
    It never advances a user's revision or an attempt's delivery acknowledgement.
14. `events` / `task_status`: ordered audit stream and task state for future UI.

Example: user says 'use SQLite', later 'keep the existing schema'. The second message
is committed as revision 2 even if the model writes a summary mentioning only SQLite.
An attempt which saw revision 1 cannot start another tool. After failover, the new
attempt's delivery packet still contains both exact messages. A delayed tool request
from the old attempt is refused. If an old tool was already running, automatic
replay stops for reconciliation rather than executing its side effect twice.

## Integration boundaries: do not overclaim

- This is not a new coding harness or a chat UI. External agents still perform
  reasoning and tools. Existing Station model/GPU/frontend profiles stay independent.
- The current `QwenCodeAdapter` reports `task_control=False`. No live ingress hook,
  steering hook, GUI edit hook, stream subscription or tool gate is wired in this PR.
  Opening the existing Qwen Desktop therefore does NOT enable these guarantees.
- A model-API proxy alone is insufficient: it sees model requests, not necessarily a
  user edit/queued message immediately when entered in the agent UI. The input adapter
  must observe the authenticated human-message boundary *before* dispatch/acknowledge.
- If that hook is unavailable in an installed runtime, mark capture as observational
  or unsupported and disable automatic authority transfer; do not invent an endpoint,
  edit its private session database or ask the model to remember to save messages.
- The library refuses stale **admission**. It does not sandbox arbitrary file access,
  prevent an external agent bypassing it, or undo a command already executing when
  a correction arrives. A future local tool executor must check current admission,
  enforce workspace/approval rules, track owned processes and stop/drain safely.
- Read-only attempts must not receive an unrestricted shell labelled 'read-only'.
  A source role ('human' vs assistant) comes from a trusted adapter, never from text.
- SQLite and the API are not an authorization boundary against malicious code running
  as the same user. Keep DB/private files in the user's data directory with suitable
  permissions; no network control port, secrets headers or tokens in audit metadata.
- End-to-end exactly-once model/tool execution is not claimed. Packets are replayable,
  duplicate ingress is idempotent; ambiguous external actions block automatic retry.
- SQLite leases use an injected UTC clock for persistence. Significant clock rollback
  needs explicit reconciliation; a process-restart scheduler must not trust old OS
  process ownership based only on a PID or assume a lease means a process was killed.
- Project-level revision invalidation is deliberately conservative. Later task-scoped
  routing must not silently omit project-wide changes. A new smaller-context leader
  currently receives all raw inputs or fails on overflow; selective source-linked
  handoff and user-approved scope reduction are later work, not a hidden lossy fallback.
- Backup via SQLite's backup API or a closed database; copying only a live .sqlite3
  without its WAL can omit committed records. Retention, encryption at rest and
  explicit user deletion/export UX are follow-up work.

## Verification

Run from source:

```text
python -m pytest -q tests/test_coordination_journal.py
python tools/coordination_smoke.py
```

The smoke creates only temporary synthetic data. It demonstrates an omitted
constraint in a model summary, a newer human message, failover and stale-attempt
rejection. It does not start a model or modify the installed Station.

Tests cover retries/conflicts, exact Unicode and edits, attachments, transaction
rollback, concurrent ingress, one writer, lease expiry, immutable-record triggers,
replay after restart, context overflow, new input during delivery and during a tool,
late results, cancellation, operator reconciliation and untrusted runtime evidence.

## Next milestones (not implemented by M1)

**M2: one real Qwen runtime adapter, fail-closed.** Pin/probe installed protocol;
intercept every new/edited/queued human input, store it before acknowledgement,
subscribe to output with persistent source IDs/cursors, and gate every tool through
managed admission. Preserve raw input independently of model summaries. Expose
capture coverage and delivered revision in Station. Unsupported channels stay
explicitly unprotected; no automatic failover there.

**M3: deterministic node/task dispatcher.** Reuse Station model/service registries;
resource-pool slots (not one worker per alias), enabled/paused/ready/loading/busy/offline,
allowed trust boundary per project, one leader, priority and backoff, task-scoped
handoff with provenance, no silent cloud fallback or context shrink, process-aware
cancellation and reconciliation. Do not modify GPU modes or remote services merely
because a model becomes unavailable.

**M4: optional Station controls.** One project/task view with raw conversation,
revisions, leader and worker sessions, queued user corrections, source links,
conflicts and explicit approvals. Use official agent sessions, not a second coding
agent. Requirements edits remain raw events regardless of UI or selected model.

First production acceptance: safe fault injection in a disposable repository,
user edits during generation/tool execution, delayed output after failover and
application restart. No repeat of the multi-model GPU benchmark is required.
