# Development handoff

Updated: 2026-09-19. **All 11 secondary PC feedback items implemented & verified.**
All 272 tests pass with zero failures.

## Текущая работа
- **Цель**: устранение 11 замечаний после тестирования релиза на втором ПК (1 GPU, CLI-агент, удалённый ComfyUI, LAN-кластер).
- **Статус**: Выполнено. Реализован весь пакет из 11 пунктов:
  1. Win32 Named Mutex в `src/instance.py` для надёжного single-instance без гонок при двойном клике.
  2. Адаптация под 1 GPU: дефолтный `tray_style` изменён на `dual_tile`, ограничение числа иконок трея до 1 при < 2 GPU.
  3. Устранение наложения в шапке кластера + автоматический автопоиск узлов по UDP (порт 47150) с диалогом добавления в 1 клик.
  4. Порог перегрева по умолчанию установлен на 90°C (`DEFAULT_THRESHOLD = 90`).
  5. Дефолтная папка моделей `D:\LLM` (с фолбэком на `C:\LLM` при отсутствии диска D:) и автосозданием при скачивании.
  6. Автовыбор установленного фронтенда агента (CLI/терминал активен со старта даже при отсутствии Desktop).
  7. Компактизация левой навигационной панели (уменьшена высота кнопок до 33px и паддинги, помещается на экранах 768p).
  8. Сбалансированные отступы дашборда (`pady=(5, 6)`), устраняющие чрезмерную тесноту и предотвращающие скроллбар.
  9. Синхронизация ComfyUI при импорте кластера: автоматическое обновление URL в `SharedServices` и внедрение навыка.
  10. Разделение UX локального vs сетевого ComfyUI.
  11. Настраиваемая папка логов в `src/paths.py`, `src/config.py` и Настройках с поддержкой сетевых путей UNC (`\\server\share`).
- **Проверка**: Все 272 теста пройдены без ошибок (pytest за 11.5с).

Current progress:
- **Secondary PC Polish & Cluster Auto-Discovery**: 11 improvements implemented and covered by 9 new tests.
- **Latest Release**: `v0.2.0-beta.7` published at [Wave-is/laas/releases/tag/v0.2.0-beta.7](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.7)
  with all 5 assets (Setup installer x64 with bundled engine, source archive, BUILD.json, SHA256SUMS.txt, cluster_topology.xml).
- **Cluster Models Discovery & Sync**: module `src/node_models.py` queries `/v1/models` from cluster LLM nodes, adds remote models to Qwen Code Desktop (`modelProviders.local-agent-station`), auto-syncs on cluster node updates and XML import.
- **Dashboard Layout Reorder & Compaction**: swapped «Оборудование сейчас» and «Модель и агент», compacted vertical paddings and label heights so the page fits without triggering the scrollbar.
- **Repository Visibility**: switched to `public` per owner request.
- **Latest Release**: `v0.2.0-beta.2` published at [Wave-is/laas/releases/tag/v0.2.0-beta.2](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.2)
  with all 4 assets (Setup installer x64 with bundled engine, source archive, BUILD.json, SHA256SUMS.txt).
- **Installer Language Selection**: added `ShowLanguageDialog=yes` and `UsePreviousLanguage=no` to `installer/Station.iss`
  so the three-language dialog (EN/RU/UK) always appears on every installation and upgrade.
- **UI Polish**: dynamic auto-hiding scrollbars across all pages (`src/ui/control_center.py`), eliminating
  unnecessary scrollbar tracks on pages where content fits the window.
- **Schedules & Idle Unload**: time and day-of-week task automation engine (`src/schedules.py`),
  `SchedulesPage` UI with `AddTaskDialog` (`src/ui/pages/schedules.py`), configurable idle unload timer,
  full EN/UK localization catalogs (`locales/{en,uk}/schedules.json`), and 4 automated tests (`tests/test_schedules.py`).
- **Model Dialog Fixes**: corrected parameter binding across `ScanDialog`, `HfDialog`, and `MoveDialog`
  (`on_add`/`on_saved` signature compatibility, `on_done` default), verified via `tests/test_model_features.py::test_dialog_signatures`.
- **Telemetry & Validation**: verified live telemetry (2x RTX A5000 TCC + Intel UHD 770), model server health (PID 29208),
  and 10 compiled C# helper protocol checks. All 245 tests pass in 5.3s.

## Current state

The official release is published at
[Wave-is/laas](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.2).
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

