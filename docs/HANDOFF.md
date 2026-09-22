# Development handoff

Updated: 2026-09-22. **Release v0.2.0-beta.16: Tested Non-disruptive OTA updater, Inno Setup deadlock elimination, PyInstaller MEI lock fixes, unified dashboard icon buttons, and lazy tray guards.**

## Текущая работа
- **Цель**: Релиз v0.2.0-beta.16: устранение ошибки tray_sources, единый стиль символьных кнопок на дашборде, устранение взаимной блокировки и PyInstaller `_MEI...` ошибки при OTA обновлении, сохранение работающего llama-swap.
- **Этап**: Сборка инсталлятора, сквозное тестирование OTA на локальном ПК, публикация на GitHub.
- **Следующий конкретный шаг**: Готово к проверке пользователем.
- **Критерий завершения**: все 293 теста проходят, инсталлятор v0.2.0-beta.16 собран и протестирован сквозным OTA скриптом (код 0), обновление накатывается без сбоев и не прерывает сервер моделей.

Current progress:
- **Non-Disruptive Tested OTA In-Place Updates**: Resolved Inno Setup installer mutual deadlock on `SetupMutex` by removing synchronous uninstaller call inside `PrepareToInstall`; removed `taskkill` calls so `llama-swap.exe` / `llama-server.exe` keep serving requests uninterrupted during UI updates.
- **PyInstaller `_MEI...` Cleanup Error Fixed**: Eliminated `Failed to remove temporary directory: %TEMP%\_MEI...` by setting `cwd=child_cwd` and `close_fds=True` in `supervisor.py` and `tempfile.gettempdir()` in `control_center.py` restart calls.
- **Unified Dashboard Controls**: Converted Model and llama-swap dashboard rows to 44px icon buttons (`▶`, `⏹`, `🔧`) matching the Agent row.
- **Tray Controls Lazy Initialization Guard**: Added safety checks in `src/ui/tray_controls.py` preventing `AttributeError: '_tkinter.tkapp' object has no attribute 'tray_sources'` when telemetry polls before the settings page is rendered.
- **Fixed StartupRunner Import**: Resolved `NameError: name 'StartupRunner' is not defined` during background services initialization on startup in `ControlCenter`.
- **Elevated Updater for Program Files**: `src/app_updates.py` automatically passes `-Verb RunAs` and decouples working directory from temporary folders when updating installations in `C:\Program Files`.
- **VM Performance & UI Responsiveness (Lazy Loading)**: Implemented on-demand lazy page creation in `ControlCenter`. Only the initial `station` page is built at startup, deferring all other 11 heavy pages (~400+ canvas widgets) until requested by the user.
- **Non-GPU Hardware Probe Bypass**: Instant return in `GpuDetailsCache.refresh()` when `nvidia-smi` is absent.
- **Single-Instance Mutex Canonicalization**: Normalized directory paths and Win32 named mutex hashing to prevent duplicate executions.
- **Vision Model Backend CLI Parameters**: Added `--no-mmproj-offload`, `--image-min-tokens <N>`, and `--image-max-tokens <N>`.
- **CUDA 13 Runtime DLLs Bundled**: `cublas64_13.dll` (50MB), `cublasLt64_13.dll` (477MB), and `nvcudart_hybrid64.dll` (1.1MB) placed into `D:\AI\QWEN_LOCAL_STACK_2026\llama.cpp` and packaged into `{app}\engine\llama.cpp` by `build_installer.ps1`. Full GPU compute out of the box on remote PCs.
- **Model Qualification Import Fixed**: corrected relative import in `src/ui/pages/models.py` (`from ...qualification import qualify_model`), eliminating `No module named 'src.ui.qualification'` error.
- **Purged Legacy 3.0.0-alpha.1 & Hardened OTA**: deleted obsolete release and tag `v3.0.0-alpha.1` from GitHub; added major version guard in SemVer comparisons; enhanced `apply_update.ps1` with `%TEMP%\laas_update.log`, codes 0/6, and fallback launcher.
- **Agent Settings Sync & Test Sandboxing**: `tests/conftest.py` now sandboxes `QWEN_HOME`, `HERMES_HOME`, `PI_CODING_AGENT_DIR`, `OPENCLAW_HOME`, and `LOCALAPPDATA` so tests never overwrite user agent settings. Station silently keeps agent configurations in sync before launch and on boot.
- **Compact Icon Agent Controls**: replaced massive text buttons with clean 44px symbol buttons: Play (`▶`), Stop (`⏹`), and Settings (`🔧`) on Dashboard and Agents page.
- **Direct Agent Launch**: `▶` launches agent directly without blocking diff or review modals. Dedicated `🔧` button opens configuration review dialog to preview and synchronize model endpoints with agent configs.
- **OTA Auto-Download by Default**: `app_update_auto_download: True` in config; 1-click restart immediately applies update and relaunches Station.
- **Legacy Release Tag Filter**: `src/app_updates.py` ignores legacy `3.0.0-alpha.1` release so SemVer comparison targets the active `0.2.0-beta.*` release line.

## Current state

The official release is published at
[Wave-is/laas](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.13).
Repository visibility is public. Setup, release notes, README, quick-start,
and application UI are fully localized in English, Russian and Ukrainian.

Release source: `e7e55584da25164f9b8c005f7e7c805eb38cbddb`.
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

