# Development handoff

Updated: 2026-09-13. **3.0.0-alpha.1 released**.
Read AGENTS.md, then this file and ignored handoff-local/README.md when available.

## Current work — managed coordination, 2026-09-14

M1 remains the durable exact conversation/attachment journal and stale-attempt
fencing core. M2a now adds the Qwen adapter's explicit managed-input facade,
transactional follow-up outbox, capability-gated loopback prompt client and
required external Tool Guard v1 HTTP provider. See QWEN_MANAGED_COORDINATION.md.

The adapter still advertises `task_control=False`; native Desktop/Telegram ingress,
steering dispatch and live result observation are NOT wired. Existing chats do not
magically gain capture protection. HTTP 202 is queue admission, never full packet
delivery. Guard permits require a separate trusted delivery binding and explicit
application tool policy; until that evidence exists, tools are denied. No autonomous
failover, process cancellation, remote request, model/GPU change or installer release.

Validation in this block: 78 new M2a tests + 53 M1 tests = 131 passed; both synthetic
smokes passed. Broader local run: 279 passed, 1 skipped, 1 deselected, excluding two
GUI-dependent modules because customtkinter is unavailable and pip network access
failed. Full unchanged test matrix and Windows packaging belong to CI, not this
local claim. Source came from the verified prior CI source archive for e178ee7.

Next concrete step M2b: qualify the installed Qwen contract in a disposable project,
wire all authenticated new/edited/queued input paths before dispatch, add persistent
SSE epoch/cursor observation and prove current packet delivery before tool admission.
Then correlate real final tool results, track/drain owned processes and test restart
with ambiguous admission and late output. Do not activate nested AgentCore delegation
under required external Guard v1; its top-level-only boundary must remain visible.
Complete this end-to-end before M3 scheduling or exposing protected status in the GUI.

Main/installed alpha.1 and user settings remain unchanged. The feature PR is the
review boundary. See WORKLOG.md and VALIDATION.md for exact validation scope.

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
