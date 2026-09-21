# Development handoff

Updated: 2026-09-21. **Release v0.2.0-beta.11 in progress: direct agent launch, compact icon controls (▶/⏹/🔧), default OTA auto-download and GitHub publication.**

## Текущая работа
- **Цель**: 1) упростить запуск агента (прямой запуск без блокирующих окон review/smoke, вынос настройки конфигурации в отдельную кнопку 🔧); 2) заменить массивные кнопки агентов на компактные значки (▶ запуск, ⏹ остановка, 🔧 настройки); 3) включить автоматическое скачивание OTA по умолчанию и 1-click перезапуск; 4) выпустить релиз `v0.2.0-beta.11` на GitHub.
- **Этап**: Внесение правок в логику запуска агента, замена кнопок на значки, обновление конфигурации OTA, подготовка версии 0.2.0-beta.11.
- **Следующий конкретный шаг**: обновить `src/ui/agent_controls.py` и `src/ui/control_center.py` (значки ▶, ⏹, 🔧; прямой `_launch_frontend` и метод `_configure_agent`), включить `app_update_auto_download: True`, поднять версию до `0.2.0-beta.11`, прогнать тесты, собрать инсталлятор и опубликовать релиз на GitHub.
- **Критерий завершения**: все тесты проходят (285+), инсталлятор собран с движком, релиз v0.2.0-beta.11 опубликован на GitHub со всеми ассетами, запуск агента происходит напрямую в 1 клик.

Current progress:
- **Telegram-style OTA Auto-Updates**:
  - `src/app_updates.py`: stateful `UpdateManager` (IDLE, CHECKING, AVAILABLE, DOWNLOADING, READY, INSTALLING, ERROR) with chunked background streaming, SHA256 integrity verification against `SHA256SUMS.txt`, cancellation, and detached PowerShell runner (`apply_update.ps1`) for seamless 1-click update & restart.
  - `src/ui/control_center.py`: unobtrusive sidebar badge packed directly above version label reacting in real time to update events (`[ 📥 Обновить до v... ]`, `[ ⏳ Загрузка 45% ]`, `[ 🚀 Перезапустить: v... ]`), delayed 15s check on boot, hourly periodic check.
  - `src/ui/update_dialog.py`: dedicated modal dialog with version info, changelog markdown viewer, download progress bar, action buttons, and Git source mode detection.
  - `src/ui/pages/maintenance.py`: updated Station update section with progress bar, action buttons, auto-check and auto-download toggles.
  - `locales/en/app_updates.json`, `locales/uk/app_updates.json`: 100% complete localization, strict i18n validated.
  - `tests/test_app_updates.py`: 7 automated tests covering SemVer comparisons, check flows, download chunking, hash verification & mismatch handling, detached script generation, and sidebar state transitions.
- **Latest Release**: `v0.2.0-beta.10` published at [Wave-is/laas/releases/tag/v0.2.0-beta.10](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.10)
  with all 4 assets (Setup installer x64 with bundled engine, source archive, BUILD.json, SHA256SUMS.txt).
- **Seamless Updater & Setup Guard**: auto-close of running processes and zero file locks during update.
- **Smarter GPU Helper Lifecycle**: `gpuhelper` unchecked by default; preserves and restarts existing service without deletion.
- **Cluster Refresh Overhaul for Slow PCs**: in-place node card updates, relaxed 30s defaults with interval dropdown (30s/60s/2min/Manual), zero widget destruction on telemetry ticks, non-blocking sampling.
- **Secondary PC Polish & Cluster Auto-Discovery**: 11 improvements implemented and covered by automated tests.
- **Cluster Models Discovery & Sync**: module `src/node_models.py` queries `/v1/models` from cluster LLM nodes, adds remote models to Qwen Code Desktop (`modelProviders.local-agent-station`), auto-syncs on cluster node updates and XML import.
- **Dashboard Layout Reorder & Compaction**: swapped «Оборудование сейчас» and «Модель и агент», compacted vertical paddings and label heights so the page fits without triggering the scrollbar.
- **Repository Visibility**: switched to `public` per owner request.
- **Installer Language Selection**: added `ShowLanguageDialog=yes` and `UsePreviousLanguage=no` to `installer/Station.iss`
  so the three-language dialog (EN/RU/UK) always appears on every installation and upgrade.

## Current state

The official release is published at
[Wave-is/laas](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.8).
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

