# Development handoff

Updated: 2026-09-13. Release being prepared: **3.0.0-alpha.1**.
Read `AGENTS.md` first. Machine-specific progress and test evidence belong in ignored
`handoff-local/`; historical development notes have been archived there locally.

## Current work

Prepare the first Windows installer and publish an early release to `Wave-is/laas`,
keeping repository visibility private. RU/UK/EN README, setup and quick-start documents
are required. Migrate the development installation to standard per-user Windows paths,
remove old Station launchers/startup entries, and confirm Hermes does not start at login.
Do not remove models, agent installations or development source/history.

Implementation: version source, Windows metadata, running-app setup guard, per-user
Inno installer, build/package scripts, translated presentation and release workflow.
Next: complete build + install/reinstall/uninstall validation, local data transfer and
legacy cleanup; audit source snapshot; push clean main/tag; upload and verify release
assets and CI. Completion must be based on actual installation and GitHub evidence.

## Working rules

- Follow the checkpoint protocol in AGENTS.md before long operations.
- Compare executable hashes/source commit with BUILD.json; HEAD may include newer docs.
- Preserve local development history; publish only the audited main history.
- Use the normal installed EXE for user launches, AppData for persistent settings.
- Windows startup and component startup are separate and opt-in.
- Stop only owned processes. Do not contact a paused remote service automatically.
- The installer must not silently install the optional privileged GPU helper.

## Validation baseline and remaining work

Before installer changes: 186 Python tests and 10 compiled helper protocol checks
passed. Previous physical evidence includes CPU/one/two NVIDIA inference, Qwen Desktop,
Pi and OpenClaw. See VALIDATION.md for limits; this is not a fresh test claim.

Installed GPU-helper acceptance, other Windows machines, prolonged maximum contexts,
full non-Russian GUI translation and agent image-tool integration remain pending.
User acceptance of a preview is distinct from completion of those product features.

## Commands and map

- `python -m pytest -q`
- `build_installer.ps1 -Python <venv-python> -ISCC <compiler>`
- `python tools/handoff_snapshot.py`
- `docs/ARCHITECTURE.md`: source map.
- `docs/RELEASING.md`: build, publication and versioning.
- `handoff-local/INSTALLER_RELEASE_PROGRESS.md`: current machine operation checkpoint.
