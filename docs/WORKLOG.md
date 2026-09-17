# Work log

## 2026-09-17 — roadmap integration: watchdog, monitoring, logs, models, maintenance, GPU details

Completed integration of the modular architecture and new functionality:
1. Reliability: ModelServerWatchdog daemon (crash/hang detection, backoff, JSONL history,
   deliberate stop suppression in UI/tray), MetricsStore (SQLite) + MetricsSampler with overheat
   hysteresis alerting, pure-Tk canvas charts (temp, load, VRAM, power, tokens/sec), diagnostics
   log tail reader with credential redaction and ZIP bundle export, LogsPage with error filter/search.
2. Models: GGUF header reader with KV-cache VRAM estimator, HuggingFace downloader with resume
   and SHA-256 validation, model library backend (scan, mmproj auto-pairing, move folder),
   complete dialog suite (ModelDialog, ScanDialog, MoveDialog, ChatDialog, HfDialog) wired into ModelsPage.
3. Maintenance: engine_updates (llama.cpp/llama-swap version detection, install, rollback),
   app_updates (Station GitHub release checker with SemVer compare), backup/restore manager with
   secret redaction and pre-import safety backups, interactive MaintenancePage.
4. Hardware: GpuDetailsCache for background queries of power draw, fans, PCIe link, throttle reasons,
   and NVLink bandwidth, integrated into HardwarePage.
5. Localization: complete English and Ukrainian translation catalogs for all new modules (240 tests pass,
   including 100% i18n coverage without missing keys or placeholder mismatches).
Validation: 240 pytest tests passed in 5.5s with zero failures; clean UI import verification.

## 2026-09-17 — clarity, single model server, folders, Program Files installer

Audit found: Services/tray "start server" used the legacy user llama-swap JSON while model
load used the generated YAML; autostart of Qwen Code Desktop always failed because a hand-
edited ~/.qwen/settings.json made the whole managed block differ; an unknown field in
model_profiles.yaml crashed Station at import (2026-09-16); 0.0.0.0 bind was hard-coded;
no address/PID/owner was shown anywhere; ~100 English messages leaked into the UI.
Fixed all of the above (see CHANGELOG 3.0.0-alpha.2). New module src/model_server.py.
Leftovers (old Hermes copy, .station-audit, runtime/, logs) moved to ../_archive/2026-09-17.
Validation: 187 pytest passed; source UI rendered and screenshotted against a copy of the
real configuration; live backend_info against the running server reported 0.0.0.0:9292.


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
