# Validation record

## Baseline before the first installer

The internal 3.0 preview passed **186 Python tests and 10 compiled C# helper protocol
checks** on Windows. Tests cover configuration transactions, process ownership,
agent adapters, startup sequencing/cancellation, service health handling, GPU mode
confirmation and simulated 0/1/2/3/4/8-GPU topologies.

Physical development checks used one Windows PC with two NVIDIA RTX A5000 cards
and an Intel display adapter. CPU inference, one selected GPU and two GPU inference,
model load/unload and backend shutdown were exercised. Qwen Desktop opened and
completed a local dialog; Pi and OpenClaw performed a local file-tool task; OpenClaw
Gateway health and owned start/stop were checked. These are historical integration
results, not a promise for every upstream agent version.

Agent-card start/stop, startup save/reload, one-time execution and cancellation were
checked with a real local fixture process. Windows shortcut creation/read/removal was
checked through COM in an isolated folder. Real Qwen Desktop was launched and stopped
from the packaged application's card. Services were exercised against local HTTP fixtures.

## Installer release

Source **ba850335d4b4bf34effccf641a05293edd51c083** passed 186 local Python tests
and 10 compiled helper checks. The actual installer passed installation, same-version
repair in EN/RU/UK, installed EXE diagnostics, active-GUI update refusal, uninstall,
owned Startup cleanup and user-data preservation. Standard per-user installation,
Start/Desktop links, AppData configuration preservation and the installed dashboard /
startup settings were checked on the development PC.

All five [CI jobs](https://github.com/Wave-is/laas/actions/runs/34756285739) passed:
Windows/Linux logic tests on Python 3.11/3.13 and Windows installer build/lifecycle.
The installer, source archive, build manifest, test record and checksums were uploaded
to the private prerelease and downloaded again with matching SHA256 hashes.

Same-version repair does not establish all future cross-version upgrades. Automated
language execution passed; interactive Setup screenshot access timed out, so visual
inspection of the Setup text is not claimed. Raw local evidence is excluded from Git
because it contains machine details. BUILD.json and TESTING.json identify release scope.

## Not yet established

- Installed new privileged GPU-helper end-to-end acceptance and live mode switching.
- A separate clean Windows machine and physical systems with other GPU counts.
- Windows logout/login with a full model + services + agents startup sequence.
- Prolonged agent sessions, largest model contexts, every native tray interaction.
- Agent-side image-generation tool binding to a remote worker.
- Full English/Ukrainian application UI (Setup and user-facing release docs are translated).

The release is an early preview. Do not equate simulated topology coverage with
physical qualification, or compiled protocol checks with installed-service validation.

## Coordination M1 — 2026-09-14

New validation, separate from the installed release: 53 tests in
`tests/test_coordination_journal.py` passed locally on Linux, plus
`python tools/coordination_smoke.py`. Tests use temporary databases and fake clocks;
no GPU, remote host, live agent, Windows service or installed user settings touched.
They cover transactional/idempotent user input, edits, attachment persistence,
concurrent writers, source-role separation, stale requirement/attempt fencing,
context-budget refusal, replay after restart and reconciliation of unknown effects.

The local test directory was a reconstructed subset, not a complete repository
checkout. Existing regression and Windows results for this commit must be read from
CI, not inferred from the earlier 186-test baseline. No Windows installer was built
locally. Real power-loss/storage hardware testing was not performed.

Not yet covered: Qwen Desktop/daemon ingress capture, queued/steering messages and
edits through the live UI, real tokenizer/image budgeting, tool-executor interception,
remote-worker scheduling, cancellation of owned OS processes, live failover and
Station task UI. Library receipts prove persistence/delivery, not model comprehension
or semantic correctness. See COORDINATION.md for remaining milestones and boundaries.


## Coordination M2a — 2026-09-14

78 new tests in `tests/test_coordination_qwen.py` passed locally. Together with
M1: **131 passed**. Both `coordination_smoke.py` and `coordination_qwen_smoke.py`
passed. Tests exercise actual SQLite transactions and loopback HTTP with synthetic
Qwen peers, not an installed daemon/model or native Desktop UI.

Coverage: persist-before-network, exact edits/attachments, atomic queue rollback,
FIFO claims, uncertain admission/crash recovery, no automatic replay, schema and
capability refusal, HTTP 202 not delivery, authenticated external Guard v1,
permit-before-execution intent, current-revision/attempt checks, duplicate request
and tuple refusal, stale results, deny-by-default tools, facade privacy and no
implicit network/background startup. Original M1 tests are unchanged.

Broader local run: **279 passed, 1 skipped, 1 deselected**, excluding
`test_gpu_confirmation.py`, `test_startup.py` and one UI callback test because
customtkinter is unavailable. A full run initially failed on that missing dependency;
pip installation was blocked by container networking. No dependency was stubbed.
Full Windows/Linux matrix and installer verification are delegated to the existing
PR CI; report its actual outcome separately. Source archive was recovered from the
successful e178ee7 CI artifact, with the M1 store blob identity checked.

NOT established: native composer/Telegram/steering interception, end-to-end full
packet delivery proof, persistent live SSE/result observer, tool process cancellation,
nested-agent fencing, live model or GUI testing, automatic failover. The adapter
retains task_control=False; no running user installation or remote service changed.


## Coordination M2b1 — 2026-09-14

89 new synthetic tests in test_coordination_qwen_events.py passed. M1+M2a+M2b1:
220 passed; all three coordination smoke scripts PASS. Includes real loopback HTTP
GET/SSE framing, reconnect cursor/epoch headers, cancellation during idle reads,
read-only transport, late admission joins, transactional rollback, replay/gap/epoch
faults, stale observer callbacks, permission liveness and late tool-result handling.
Runtime events never create user instructions or prove delivery. Final runtime tool
status is not an assertion about pytest/file correctness or process-tree exit.

Full local suite was attempted; collection needs unavailable customtkinter.
Broader non-GUI run: 368 passed, 1 skipped, 1 deselected (two GUI modules excluded).
Full Windows/Linux Python 3.11/3.13 and Windows installer execution belong to CI;
results are recorded on the feature PR. No actual Qwen daemon/GUI, GPU/model server,
Windows service or user configuration was exercised. No new dependencies.

## Coordination HTTP lifetime hardening — 2026-09-15

34 new real-loopback/SQLite regression tests passed; total coordination suite:
254 passed in 6.50s. The lifetime suite also passed five additional full repeats
(34 each). All three existing synthetic smoke scripts PASS. A regression subset
was run against the exact prior ab238e9 transport files and failed, then passed
with the fix; retained partial responses, unbounded preflight cancellation and
concurrent stop masking persistence/protocol failures are independently reproduced.
No new dependency or live agent/model/GPU access.

Broader final local command excluded tests/test_gpu_confirmation.py and
 tests/test_startup.py and deselected test_ui_callback_error_does_not_stop_telemetry_queue:
402 passed, 1 skipped, 1 deselected in 10.19s. Earlier broader attempt with the
callback included failed on missing customtkinter (1 failed, 399 passed, 1 skipped,
before three final regression cases were added). No UI dependency was stubbed.
This is not a full GUI or Windows-suite success claim.

CI evidence: ab238e9 run 34897467428 passed Linux, but both Windows jobs timed out
at six hours; the exact stalled test remains unknown. d3ebf9c adds named tests,
faulthandler dumps, bounded step/job deadlines and JUnit retention. Its push/PR
runs 34927010021 / 34927013300 failed before any runner or test step started.
No Windows validation is claimed; see HTTP_TRANSPORT_LIFETIME.md. Runtime task
control, live packet-delivery qualification and automatic failover stay disabled.
