# Development handoff

Updated: 2026-09-21. **Release v0.2.0-beta.12 in progress: bundle CUDA 13 DLLs (cublas, cublasLt, nvcudart_hybrid), fix models qualification import, purge legacy 3.0.0-alpha.1 release, harden detached update relaunch.**

## Текущая работа
- **Цель**: 1) встроить недостающие библиотеки CUDA 13 (`cublas64_13.dll`, `cublasLt64_13.dll`, `nvcudart_hybrid64.dll`) в бандл llama.cpp, чтобы на других ПК видеокарта сразу определялась; 2) исправить относительный импорт `qualify_model` в `src/ui/pages/models.py`; 3) удалить с GitHub релиз и тег `v3.0.0-alpha.1`, чтобы прекратить циклическое скачивание старой версии; 4) доработать скрипт `apply_update.ps1` (логирование, коды 0 и 6, fallback пути) для надёжного перезапуска; 5) выпустить `v0.2.0-beta.12`.
- **Этап**: Сборка инсталлятора 0.2.0-beta.12 с полным набором CUDA DLL.
- **Следующий конкретный шаг**: Дождаться завершения сборки, закоммитить правки, запушить тег `v0.2.0-beta.12` и опубликовать релиз со всеми ассетами через `tools/publish_release.py`.
- **Критерий завершения**: все 286 тестов проходят, инсталлятор собран с библиотеками CUDA 13, релиз v0.2.0-beta.12 опубликован на GitHub, проверка моделей работает без ошибки импорта.

Current progress:
- **CUDA 13 Runtime DLLs Bundled**: `cublas64_13.dll` (50MB), `cublasLt64_13.dll` (477MB), and `nvcudart_hybrid64.dll` (1.1MB) placed into `D:\AI\QWEN_LOCAL_STACK_2026\llama.cpp` and packaged into `{app}\engine\llama.cpp` by `build_installer.ps1`. Full GPU compute out of the box on remote PCs.
- **Model Qualification Import Fixed**: corrected relative import in `src/ui/pages/models.py` (`from ...qualification import qualify_model`), eliminating `No module named 'src.ui.qualification'` error.
- **Purged Legacy 3.0.0-alpha.1 & Hardened OTA**: deleted obsolete release and tag `v3.0.0-alpha.1` from GitHub; added major version guard in SemVer comparisons; enhanced `apply_update.ps1` with `%TEMP%\laas_update.log`, codes 0/6, and fallback launcher.
- **Agent Settings Sync & Test Sandboxing**: `tests/conftest.py` now sandboxes `QWEN_HOME`, `HERMES_HOME`, `PI_CODING_AGENT_DIR`, `OPENCLAW_HOME`, and `LOCALAPPDATA` so tests never overwrite user agent settings. Station silently keeps agent configurations in sync before launch and on boot.
- **Latest Release**: `v0.2.0-beta.11` published at [Wave-is/laas/releases/tag/v0.2.0-beta.11](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.11)
  with all 4 assets (Setup installer x64 with bundled engine, source archive, BUILD.json, SHA256SUMS.txt).
- **Compact Icon Agent Controls**: replaced massive text buttons with clean 44px symbol buttons: Play (`▶`), Stop (`⏹`), and Settings (`🔧`) on Dashboard and Agents page.
- **Direct Agent Launch**: `▶` launches agent directly without blocking diff or review modals. Dedicated `🔧` button opens configuration review dialog to preview and synchronize model endpoints with agent configs.
- **OTA Auto-Download by Default**: `app_update_auto_download: True` in config; 1-click restart immediately applies update and relaunches Station.
- **Legacy Release Tag Filter**: `src/app_updates.py` ignores legacy `3.0.0-alpha.1` release so SemVer comparison targets the active `0.2.0-beta.*` release line.
- **Telegram-style OTA Auto-Updates**:
  - `src/app_updates.py`: stateful `UpdateManager` (IDLE, CHECKING, AVAILABLE, DOWNLOADING, READY, INSTALLING, ERROR) with chunked background streaming, SHA256 integrity verification against `SHA256SUMS.txt`, cancellation, and detached PowerShell runner (`apply_update.ps1`) for seamless 1-click update & restart.
  - `src/ui/control_center.py`: unobtrusive sidebar badge packed directly above version label reacting in real time to update events (`[ 📥 Обновить до v... ]`, `[ ⏳ Загрузка 45% ]`, `[ 🚀 Перезапустить: v... ]`), delayed 15s check on boot, hourly periodic check.
  - `src/ui/update_dialog.py`: dedicated modal dialog with version info, changelog markdown viewer, download progress bar, action buttons, and Git source mode detection.
  - `src/ui/pages/maintenance.py`: updated Station update section with progress bar, action buttons, auto-check and auto-download toggles.
  - `locales/en/app_updates.json`, `locales/uk/app_updates.json`: 100% complete localization, strict i18n validated.
  - `tests/test_app_updates.py`: 8 automated tests covering SemVer comparisons, check flows, download chunking, hash verification & mismatch handling, detached script generation, UI state transitions, and legacy tag filtering.

## Current state

The official release is published at
[Wave-is/laas](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.11).
Repository visibility is public. Setup, release notes, README, quick-start,
and application UI are fully localized in English, Russian and Ukrainian.

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

