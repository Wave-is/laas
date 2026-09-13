# Work log

## 2026-09-13 — first installer release preparation

Added a shared version source, Windows file metadata and an application lifetime mutex
for Setup. Added per-user Inno Setup packaging, standard shortcuts/uninstall, ownership-
checked startup cleanup, three-language release presentation and reproducible release inputs.
Private pre-installer progress is retained in ignored local handoff notes.

Validation baseline: 186 Python tests and 10 compiled helper checks before this change.
Installer installation/publishing work is still active; results must be recorded only
once completed. Detailed machine actions are in handoff-local/INSTALLER_RELEASE_PROGRESS.md.

## 2026-09-13 — installer lifecycle validated

186 Python tests and 10 compiled helper checks passed with the installer build.
Actual install, EN/RU/UK repair, installed diagnostics, active-GUI update refusal,
normal exit, uninstall, owned Startup cleanup and data preservation passed locally.
The first CI revealed Windows npm shims being treated as native POSIX executables;
fixed suffix handling, then all four Python matrix jobs passed. Packaging CI and
final publication are still in progress. Private raw reports remain in runtime.
