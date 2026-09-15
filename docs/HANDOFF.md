# Development handoff

Updated: 2026-09-13. **3.0.0-alpha.1 released**.
Read AGENTS.md, then this file and ignored handoff-local/README.md when available.

## Current work — M2b1 transport hardening, 2026-09-15

The durable journal, Qwen outbox/Guard and SSE observer exist, but live native
input capture, full-packet delivery and owned-process drain are still NOT wired.
`task_control=False`, `automatic_failover=False`, `live_runtime_verified=False`.
Existing Desktop/Telegram conversations are not automatically protected.

This checkpoint fixes reproducible HTTP lifetime problems before M2b2: one owned
exchange deadline now covers capabilities preflight, response headers and streaming
body; explicit response/socket cleanup includes partial and Connection: close bodies.
Cancellation cannot hide concurrent journal/protocol failures or turn uncertain
prompt POSTs into retries. See HTTP_TRANSPORT_LIFETIME.md for precise boundaries.
No new dependency, daemon startup, remote-model access or production change.

Validation: 34 new loopback/SQLite tests; full coordination 254 passed locally.
All three synthetic coordination smokes PASS. Broader non-GUI regression is run
with customtkinter-dependent tests excluded; see VALIDATION.md for exact totals.
The archived fda25d3 source was overlaid with connected-repository ab238e9 files;
Git blob hashes for all affected predecessor modules/tests were checked.

CI blocker: ab238e9 run 34897467428 passed Linux but both Windows jobs exceeded
six hours and were cancelled. Logs have only progress dots, no exact hung test.
The exact Windows root cause remains unconfirmed; do not claim these fixes prove it.
Diagnostics commit d3ebf9c adds verbose test names, stack dumps and strict deadlines.
Runs 34927013300 and 34927010021 failed before runner assignment (empty steps,
runner_id=0); the available API does not establish why. No tests ran in those jobs.
Do not repeatedly rerun or alter repository billing/security to bypass this.

Next: obtain one bounded Windows/Linux run of this transport fix when runners are
available; inspect named test/stack on any hang. Then qualify an installed Qwen in
a disposable project for M2b2: native new/edit/queue/steer capture BEFORE forwarding,
full packet receipt/token budget, foreground tool ownership and queue/process drain.
Do not infer delivery from HTTP 202, replay_complete, model text or turn_complete.
No M3 automatic scheduling until the complete boundary is accepted.

PR #1 remains the review boundary. No main, release, installed EXE, GPU/model,
startup, user setting or remote service was changed.

## Current state

The first early release is published at
[Wave-is/laas](https://github.com/Wave-is/laas/releases/tag/v3.0.0-alpha.1).
Repository visibility remains private. Setup, release notes, README and quick-start
are available in English, Russian and Ukrainian; application UI is currently Russian.

Release source: `ba850335d4b4bf34effccf641a05293edd51c083`.
Later documentation-only commits do not change the built installer or executable.
BUILD.json, TESTING.json and SHA256SUMS.txt accompany the installer and source archive.
All five uploaded assets were downloaded and verified by SHA256.

Completed on the development machine: standard per-user installation, data transfer
with verified backup, old Station launcher/build cleanup and old GPU-service removal.
Hermes remains installed, without Windows startup entries. Machine-specific details
and exact paths belong in handoff-local. Application and component startup are off.
The normal application is now installed under LOCALAPPDATA/Programs; its data is in
LOCALAPPDATA/LocalAgentAIStation. Models and external agents remain separate.

## Verified

186 Python tests + 10 compiled C# helper protocol checks passed. Installer installation,
EN/RU/UK repair, installed diagnostics, running-app update refusal, normal exit,
uninstall, owned Startup cleanup and data preservation passed. Standard Start/Desktop
shortcuts, installed dashboard and startup settings were checked on the development PC.
All five [GitHub CI jobs](https://github.com/Wave-is/laas/actions/runs/34756285739)
passed, including installer lifecycle checks on a hosted Windows runner.
See VALIDATION.md and the release TESTING.json for boundaries.

## Next product work

- Complete acceptance of the separately installed privileged GPU helper. Setup does
  not install it; GPU monitoring does not require it. Do not claim live switching verified.
- Other physical Windows machines/GPU counts; prolonged agent/max-context sessions.
- Full English/Ukrainian GUI and agent-side image-generation tool integration.
- Cross-version upgrade acceptance for each later release; this release tested repair.

This installer/publication task is complete; the broader roadmap is not.

## Continuing safely

Follow the checkpoint protocol in AGENTS.md before long operations. Stop only owned
processes; keep GPU modes, model selection and agent selection independent. Do not
contact paused remote services automatically. Preserve user data and private evidence.

The shared main history starts with an audited root snapshot. Old local historical
branches must not be pushed. Use src/version.py for both semantic and Windows versions;
follow RELEASING.md for each new release. Do not change repository visibility implicitly.

Commands: `python -m pytest -q`, `build_installer.ps1`, `python tools/handoff_snapshot.py`.
The installer test refuses an existing installation: do not force it over a user's
working copy. Source map: ARCHITECTURE.md. Public validation: VALIDATION.md.
