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
