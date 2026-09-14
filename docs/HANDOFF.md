# Development handoff

Updated: 2026-09-13. **3.0.0-alpha.1 released**.
Read AGENTS.md, then this file and ignored handoff-local/README.md when available.

## Current work — managed coordination, 2026-09-14

User requirement: tasks evolve through conversation; no model is responsible for
remembering to write changes to the database or a summary. Start the deterministic
coordinator inside this project, not a new coding-agent framework.

M1 implemented in `src/coordination/`: transactional exact message/edit/attachment
journal, replayable delivery receipts, requirement revisions, expiring/fenced task
attempts, one writer per project and fail-closed tracking of uncertain tool effects.
Summaries are derived; a missing summary item cannot replace an original message.
Runtime stream evidence is separate from authoritative human input.

Current validation: 53 new offline tests passed in a Linux Python environment;
`python tools/coordination_smoke.py` passed. Existing 186 tests, Windows packaging
and live Qwen/Hermes behavior were not re-run locally in that environment. Full
repository CI results belong to the feature PR, separately from historical results.
No new installer or deployed binary; main/production settings are unchanged.

Next concrete step: M2 in [COORDINATION.md](COORDINATION.md). Inspect the installed
Qwen protocol and implement a verified input/steering/queue capture adapter plus
local tool-admission boundary. `QwenCodeAdapter` currently has `task_control=False`;
this M1 library is NOT connected to Desktop/daemon and must not be advertised as
protecting an existing live conversation. Do not enable autonomous failover until
every relevant ingress and tool launch is captured/fenced. Add disposable integration
tests for mid-turn user edits, disconnect/replay, stale output and process recovery.

Acceptance of M1: original corrections survive model-summary omission and restart;
stale/revoked attempts cannot admit new tools through the library; ambiguous actions
block retry. M2 acceptance additionally requires real runtime ingress/tool evidence.

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
