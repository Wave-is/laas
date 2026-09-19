# Work log
 
## 2026-09-19 — secondary PC feedback fixes & cluster auto-discovery

1. Win32 Named Mutex Single Instance (`src/instance.py`, `main.pyw`):
   - Added kernel-level Win32 Named Mutex (`CreateMutexW`, `ERROR_ALREADY_EXISTS 183`) preventing race conditions on rapid double-clicks.
   - Secondary instance immediately signals running instance via loopback socket to bring window to front and exits `0` cleanly.
   - Started listener loop immediately on acquire with queued show events, eliminating the 2-second startup window race.
2. Dynamic GPU Adaptation & Tray Defaults (`src/config.py`, `src/ui/tray_controls.py`, `src/ui/pages/model_dialogs.py`):
   - Changed default `tray_style` in `DEFAULT_SETTINGS` from `two_icons` to `dual_tile` (two metrics in one tray icon).
   - Clamped tray icons to 1 whenever detected NVIDIA/discrete GPU count is < 2, eliminating unwanted second dummy tray icon on single-GPU machines.
   - Updated model dialog GPU selector to only present multi-GPU options when 2+ GPUs are detected.
3. Cluster Page UI Overlap Fix & UDP Auto-Discovery (`src/cluster_discovery.py`, `src/ui/pages/cluster.py`):
   - Restructured summary card into two clean rows: Title/Subtitle row on top (with Refresh button), and dedicated Action Toolbar underneath, completely preventing button overlap at any screen width.
   - Implemented `ClusterDiscovery` (`src/cluster_discovery.py`): UDP broadcast beacon on port 47150 announcing hostname, IP, port, endpoints, and GPU specs; background listener with automatic TTL expiry.
   - Added «🔍 Автопоиск в сети» button and `ClusterDiscoveryDialog` on Cluster page for 1-click addition of discovered LAN nodes.
4. Overheat Alert Threshold 90°C (`src/ui/pages/monitoring.py`, `src/metrics_history.py`, `src/config.py`):
   - Changed `DEFAULT_THRESHOLD = 90` (was 85) in `monitoring.py` and `DEFAULT_SETTINGS['monitoring_alert_threshold_c'] = 90`.
5. Default Models Directory `D:\LLM` (`src/model_server.py`, `src/hf_download.py`):
   - `model_server.models_dir()` now defaults to `D:\LLM` if drive D: exists, otherwise `C:\LLM`.
   - `safe_target` in `hf_download.py` automatically creates target directory on download.
6. Agent Frontend Auto-Selection (`src/ui/agent_controls.py`, `src/ui/control_center.py`):
   - When preferred frontend (`qwen-desktop`) is not installed, automatically selects the first *installed* frontend (`qwen-terminal`), enabling the «Запустить агента» button immediately.
7. Sidebar & Dashboard Layout Polish (`src/ui/control_center.py`):
   - Compacted left sidebar navigation: reduced button height from 42 to 33, padding to 1, brand and footer vertical margins tightened, fitting all 10 page buttons on 768p displays without cutoff.
   - Balanced dashboard padding (`pady=(5, 6)`) for cards, combos, and status boxes, providing breathing room while keeping the entire station page within window height.
8. ComfyUI Cluster Sync & Local vs Remote UX (`src/cluster_manager.py`):
   - `import_nodes_xml` automatically updates `SharedServices` active ComfyUI service profile with remote node's URL (e.g. `http://192.168.x.x:8188`) instead of keeping disconnected `127.0.0.1:8188`, and deploys the agent skill.
9. Configurable Logging Directory (`src/paths.py`, `src/config.py`, `main.pyw`, `src/ui/control_center.py`):
   - Added `'logs_dir'` setting in `config.py`.
   - Added `logs_dir()` in `paths.py` with fallback to default `%LOCALAPPDATA%/LocalAgentAIStation/logs` if custom/network path is unreachable.
   - Added «Папка журналов (логов)» field in Settings supporting UNC network shares (`\\server\share\laas-logs`), and «Открыть папку логов» button.
10. Testing & Verification:
    - Added unit tests: `tests/test_instance.py`, `tests/test_cluster_discovery.py`, `tests/test_secondary_pc_fixes.py`.
    - Added full EN/UK translations in `locales/{en,uk}/cluster.json` and `locales/{en,uk}/control_center.json`.
    - All 272 automated pytest tests pass with zero failures.
 
1. Remote Node Models Discovery:
   - Created `src/node_models.py` (`RemoteModel`, `query_node_models`, `discover_cluster_models`) to discover available models from cluster LLM nodes over OpenAI-compatible `/v1/models` endpoints.
   - Non-LLM nodes (ComfyUI) and disabled nodes are automatically filtered out.
   - Safe HTTP request handling with 3s timeout and robust JSON parsing.
2. Agent Adapters & Controller Integration:
   - Extended `QwenCodeAdapter.configure_model_provider` to accept `cluster_models` and register them into `modelProviders.local-agent-station` with remote `baseUrl`, deduplicating entries.
   - Updated `AgentRuntimeAdapter` (`src/agents/base.py`), Hermes, OpenClaw, and Pi adapters to accept `cluster_models=None`.
   - Updated `StationController.preview_sync` to query `discover_cluster_models(cluster_manager)` and forward remote models to the adapter.
3. Knowledge Distribution & Auto-Sync:
   - Expanded `src/skill_distributor.py` (`KnowledgeDistributor`) with `sync_models_to_agents()` method to automatically sync models to all discovered agents without requiring GUI interaction.
   - Wired auto-sync into `ClusterManager` (`add_node`, `update_node`, `import_nodes_xml`) for LLM node changes.
   - Overhauled the «📢 Рассказать агентам» button on `ClusterPage` (`src/ui/pages/cluster.py`) to deploy ComfyUI skills AND synchronize model catalogs with agents, reporting combined status to user.
   - Added full translations in `locales/en/cluster.json` and `locales/uk/cluster.json`.
4. Verification & Testing:
   - Created `tests/test_node_models.py` (5 tests covering endpoint queries, format variations, error resilience, node filtering, adapter registration).
   - Verified live synchronization against active cluster nodes: 7 models discovered on cluster nodes and successfully written into `~/.qwen/settings.json`.
   - All 263 pytest tests pass with zero failures.

## 2026-09-17 — dashboard layout polish: block reordering & vertical compaction

1. Dashboard Block Reordering:
   - In `src/ui/control_center.py` (`_build_station`), moved «Оборудование сейчас» (GPU tiles) directly under the top stats tiles, and placed «Модель и агент» below it per user request.
2. Vertical Compacting & Scrollbar Elimination:
   - Applied `height=0` to single-line labels (`heading`, `status_label`, `card` headers, `stats` tiles, GPU tile rows, server status label, and prompt label) to override CustomTkinter's default 28px/42px label height constraint.
   - Reduced button/combo row vertical padding from 14px to `(4, 5)`, eliminating the huge 28px gaps between consecutive rows.
   - Reduced GPU tile and card header margins (`pady`), saving ~168px vertically at 150% DPI scaling (content height reduced from 867px to 699px).
   - Dynamic auto-hiding scrollbar now remains completely unmapped/hidden at standard window resolutions without awkward vertical scrolling.
3. Verification:
   - Verified programmatically that `station_page._scrollbar.winfo_ismapped() == False` with simulated 3-device topology.
   - All 258 pytest tests pass with zero regressions.

1. Installer Language Prompt:
   - Configured `installer/Station.iss` with `ShowLanguageDialog=yes` and `UsePreviousLanguage=no` so that
     the three-language selection dialog (English, Russian, Ukrainian) is always presented to the user
     upon every installer launch, including upgrades.
2. UI Polish & Stubs Cleanup:
   - Dynamic auto-hiding scrollbar across all pages in `src/ui/control_center.py` with slim transparent styling.
   - Cleaned up obsolete "section in development" entries from locales.
3. Release Packaging, Public GitHub Repository & Latest Release:
   - Bumped version to `0.2.0-beta.2` (`WINDOWS_VERSION = (0, 2, 0, 2)`).
   - Rebuilt full release installer with bundled engine.
   - Switched GitHub repository visibility from private to public per user request.
   - Published release `v0.2.0-beta.2` as the public Latest Release on GitHub with all assets.

## 2026-09-17 — release 0.2.0-beta.1: schedules, model dialog fixes, Inno Setup 6.7.3

1. Schedules & Idle Unload:
   - Added `ScheduleManager` in `src/schedules.py` supporting time-of-day execution, day-of-week recurrence
     (every day, weekdays, weekends), actions (`gpu_profile`, `model_profile`, `unload_model`, `stop_backend`),
     and an idle auto-unload timer.
   - Built full `SchedulesPage` UI with task listing, delete/edit controls, and `AddTaskDialog`.
   - Added complete translations: `locales/en/schedules.json` and `locales/uk/schedules.json`.
   - Created automated tests in `tests/test_schedules.py` (4 tests covering engine and auto-unload).
2. Bugfixes & Model Dialogs:
   - Fixed `ScanDialog` and `HfDialog` constructor signature mismatch (`on_saved` vs `on_add`), and
     `MoveDialog` (`on_done` default value). Added signature regression tests in `tests/test_model_features.py`.
3. Installer & Packaging:
   - Installed Inno Setup 6.7.3 and updated `build_installer.ps1` with fallback discovery under `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.
   - Updated `src/version.py` to `VERSION = '0.2.0-beta.1'` (`WINDOWS_VERSION = (0, 2, 0, 1)`).
   - Documented comprehensive release notes in EN, RU, UK in `CHANGELOG.md`.
4. UI Polish & Quality:
   - Implemented dynamic auto-hiding scrollbars for all pages (`CTkScrollableFrame` in `control_center.py`):
     when page content fits the window, scrollbar is completely removed; when content overflows, a slim
     8px semi-transparent thumb appears. Eliminates persistent gray scrollbar ribbon on dashboard.
   - Removed obsolete "section in development" catalog entries from locales.
5. Verification:
   - All 245 pytest tests pass in 5.3s with zero failures.
   - 10 compiled C# helper protocol checks pass cleanly.
   - Live hardware telemetry and model server (PID 29208) verified intact.

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
