# Development handoff

Updated: 2026-09-19. **Release 0.2.0-beta.9 built, published and verified on GitHub.**
All 276 tests pass with zero failures.

## Текущая работа
- **Цель**: переработка механики обновления вкладки «LLM-кластер» для медленных ПК (устранение постоянных перерисовок и дерганий, внедрение спокойного дефолтного интервала 30с и настраиваемых интервалов, in-place обновление виджетов без destroy, сборка и публикация нового инсталлятора).
- **Статус**: Завершена. Релиз `v0.2.0-beta.9` собран и опубликован на GitHub.
- **Подтверждённый результат**:
  - Полностью отвязано обновление UI вкладки кластера от ежесекундного тика локальной телеметрии GPU.
  - Реализован in-place рендеринг карточек узлов (`_update_node_card_inplace`): виджеты больше не уничтожаются (`destroy()`) при обновлении метрик, устранены мерцания, прыжки скролла и потеря кликов.
  - В шапку вкладки кластера добавлены контролы: спокойные интервалы (`30 сек`, `60 сек`, `2 мин`, `Вручную`) с дефолтом 30 секунд, кнопка ручного опроса `[⟳ Обновить]` с визуальным индикатором `[⏳ Опрос...]` и метка времени последнего успешного обновления.
  - В `ClusterManager` добавлен неблокирующий опрос `sample_async(callback)`, поллер переведён в режим сна при `manual` и использует адаптивный таймаут.
  - Все 276 тестов проходят успешно (включая i18n, `test_cluster.py` и все регрессионные тесты).
  - Скомпилирован полный инсталлятор Inno Setup с бандлом llama.cpp/llama-swap (`LocalAgentAIStation-0.2.0-beta.9-Setup-x64.exe`, 182.7 MB).
  - Релиз `v0.2.0-beta.9` успешно опубликован на GitHub со всеми 4 ассетами и проверенными контрольными суммами SHA256.
- **Следующий конкретный шаг**: установка и тестирование нового инсталлятора на втором ПК.
- **Критерий завершения**: инсталлятор скачан и успешно развёрнут на втором ПК, опрос кластера работает плавно без нагрузки на CPU.

Current progress:
- **Latest Release**: `v0.2.0-beta.9` published at [Wave-is/laas/releases/tag/v0.2.0-beta.9](https://github.com/Wave-is/laas/releases/tag/v0.2.0-beta.9)
  with all 4 assets (Setup installer x64 with bundled engine, source archive, BUILD.json, SHA256SUMS.txt).
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

