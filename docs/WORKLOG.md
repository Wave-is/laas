# Work log

## 2026-09-13 — first installer release preparation

Added a shared version source, Windows file metadata and an application lifetime mutex
for Setup. Added per-user Inno Setup packaging, standard shortcuts/uninstall, ownership-
checked startup cleanup, three-language release presentation and reproducible release inputs.
Private pre-installer progress is retained in ignored local handoff notes.

Validation baseline: 186 Python tests and 10 compiled helper protocol checks before this change.
Installer installation/publishing work is still active; results must be recorded only
once completed. Detailed machine actions are in handoff-local/INSTALLER_RELEASE_PROGRESS.md.

## 2026-09-13 — installer lifecycle validated

186 Python tests and 10 compiled helper checks passed with the installer build.
Actual install, EN/RU/UK repair, installed diagnostics, active-GUI update refusal,
normal exit, uninstall, owned Startup cleanup and data preservation passed locally.
The first CI revealed Windows npm shims being treated as native POSIX executables;
fixed suffix handling, then all four Python matrix jobs passed. Packaging CI and
final publication are still in progress. Private raw reports remain in runtime.

## 2026-09-13 — alpha.1 published and installed

Source ba85033: local 186+10 checks and full installer lifecycle passed. All five CI
jobs passed, including hosted Windows installer tests. A same-version repair was
tested; future cross-version upgrades remain release-specific validation. Standard
per-user installation, existing data preservation, shortcuts and installed GUI were
verified. Old launcher/build/startup cleanup completed on the development machine.
Private prerelease v3.0.0-alpha.1 published with five assets, downloaded hash checks
passed. No repository visibility change. Application UI remains Russian; presentation
and Setup are translated. Broader GPU-helper/physical-machine/agent qualification
remains on the roadmap. Local raw evidence and machine details are ignored by Git.

## 2026-09-14 — M1 durable conversation and fenced coordination core

Added opt-in `src/coordination/JournalStore` using local SQLite, no new dependency,
network request, model download or GUI/startup change. Exact human messages/edits and
immutable attachment bytes persist before a successful receipt; every user update
advances a revision. Model summaries cannot replace original input. Runtime outputs
are evidence only. Replayable delivery receipts and attempt epochs gate new tool
intents; a single writer is admitted per project. Uncertain effects block failover
until explicit operator reconciliation. Late output is kept without granting authority.

Validation: 53 new offline Python tests passed; synthetic coordination smoke passed.
A locally reconstructed source subset was used because container Git networking was
unavailable. Full-repository regression tests/Windows packaging are delegated to the
feature PR's existing CI; no local claim of running the historical 186 tests.
Live Qwen/Hermes message capture and tool interception are NOT implemented yet.
Next: verified runtime adapter (M2), then resource-aware dispatcher/UI (M3/M4).
See COORDINATION.md and HANDOFF.md. No production settings or released binary changed.


## 2026-09-14 — M2a Qwen durable outbox and required external Tool Guard

Implemented an opt-in QwenCodeAdapter facade without changing task_control=False,
production configuration or GUI startup. Exact human messages/edits and queue rows
commit atomically; dispatch records intent before one HTTP POST. Ambiguous results
block replay. HTTP 202 is kept separate from model delivery and tool completion.

Reviewed the upstream external Tool Guard v1 at f024b37689f3abab4bbfb249f44af55d34effc77.
Implemented its two authenticated loopback routes, strict contract parsing, explicit
tool allowlist, current delivery/revision/attempt fencing and persistent request/tuple
replay refusal. Unknown/nested/background tools deny. No OS tool is launched by this
module; actual results require a trusted observer. Fixed the capabilities fixture
against upstream: features is an array, not an invented boolean map.

New tests: 78 passed. M1+M2a together: 131 passed. Both synthetic smokes PASS.
Broader local regression: 279 passed, 1 skipped, 1 deselected, with two GUI-dependent
modules excluded because customtkinter is missing; attempting dependency installation
failed on unavailable network. No UI stubs or fake full-suite claim. CI runs the full
unchanged matrix and Windows packaging separately. No new dependencies.

Next M2b: native input/steering and live SSE cursor/epoch + result/delivery evidence,
then actual owned-process draining. Existing Desktop/Telegram conversations remain
unprotected and automatic failover disabled until those integration tests pass.


## 2026-09-14 — M2b1 durable Qwen SSE lifecycle observation

Added bounded authenticated loopback SSE receiver and additive SQLite event tables.
Each raw event, epoch/cursor and correlated terminal/tool projection commit together.
Replay is idempotent; stale callbacks are fenced; gaps, changed epochs, degraded
recording, session death/rewind/model changes block new permits. Subscriber loss
reconnects only GET with the durable cursor, never a prompt POST. Missing final tool
results remain unresolved. A user correction during execution leaves late results
requiring review. HTTP 202, replay completion and model text still confer no delivery
proof. Adapter exposes explicit event_receiver(); existing UIs are not intercepted.

Reviewed upstream event schema, bus, SDK transport and tool emitter at f024b37689f3.
89 new tests pass; combined coordination 220 passed. Three synthetic smokes PASS.
Broader local regression 368 passed, 1 skipped, 1 deselected, two GUI modules excluded;
full collection attempted and blocked by missing customtkinter. No faked dependency,
live Qwen, GPU request or production change. Full matrix/installer is delegated to
unchanged CI. Main and installed release remain unchanged.

Next: M2b2 native ingress, full packet delivery and actual owned-process draining;
then scheduling. Automatic failover and task_control remain false.
