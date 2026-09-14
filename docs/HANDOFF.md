# Development handoff

Updated: 2026-09-13. **3.0.0-alpha.1 released**.
Read AGENTS.md, then this file and ignored handoff-local/README.md when available.

## Current work — managed coordination M2b1, 2026-09-14

Completed: durable exact-input journal (M1), follow-up outbox and required Tool
Guard v1 (M2a), now a bounded authenticated REST/SSE observer (M2b1). See
QWEN_EVENT_OBSERVER.md. Event envelope + epoch/cursor + correlated turn/tool result
commit together. Replay deduplicates; gaps/epoch change/degraded history block
permissions. Late tool results after corrections remain needs_review. A matching
terminal may be joined after a late 202 receipt, without resending the prompt.

The adapter's explicit event_receiver() starts no daemon/thread on construction.
Registered observers gate dispatch/Guard until caught up and fresh. No observer
row preserves the legacy developer API, not a protected-session claim.
`task_control=False`, `automatic_failover=False`, `live_runtime_verified=False`.
Native Desktop/Telegram ingress, actual full-packet delivery and OS-process drain
are still NOT wired. Existing user chats do not automatically gain protection.
Never bind a delivery from HTTP 202, replay_complete, assistant text or turn_complete.

Validation: 89 new tests; M1+M2a+M2b1 = 220 passed locally; all three synthetic
smokes PASS. Broader local run: 368 passed, 1 skipped, 1 deselected; two GUI modules
excluded because customtkinter is unavailable. Full local collection was attempted
and failed on that missing dependency, not represented as success. The unchanged
GitHub CI matrix covers Windows/Linux 3.11/3.13 and installer lifecycle separately.
Source reconstructed from the prior CI source archive; its merge tree bd0ba24 is
identical to head fda25d3, verified by GitHub compare and archive SHA256.

Next concrete M2b2: qualify installed Qwen in a disposable project; route actual
new/edited/queued/steering inputs through authenticated persistence BEFORE forwarding.
Establish full packet receipt/token budget at the real model boundary, correlate
owned foreground tool processes and drain/cancel them including pending prompts.
Exercise an edit during tool execution and observer reconnect/restart end-to-end.
External Guard v1 remains top-level; do not enable nested AgentCore execution or
M3 automatic scheduling until the complete boundary is accepted.

No main, installed EXE, model, GPU, startup, user setting or remote service changed.
Feature PR #1 is the review boundary. WORKLOG.md/VALIDATION.md record test scope.

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
