# Карта проекта для следующего разработчика

Station — Windows GUI на Python/Tkinter, упакованный PyInstaller, с отдельным C#
Windows service для привилегированных GPU-операций. Модель и агент внешние.

| Область | Основные файлы | Что учитывать |
|---|---|---|
| Вход и единственный экземпляр | `main.pyw`, `src/instance.py`, `src/paths.py` | `--data-dir` фиксируется до импорта конфигурации; второй запуск показывает существующую панель |
| Автозапуск | `src/startup.py`, `src/windows_startup.py`, `src/ui/startup_controls.py` | Windows Startup shortcut отдельно от config.startup; opt-in последовательность служб/модели/агентов, задержка/отмена, один раз после обнаружения; `--no-startup` для восстановления |
| Карточки агентов | `src/ui/agent_controls.py`, `src/controller.py` | Выбор frontend, start/stop только с ownership; основной агент первым; состояния общие с dashboard/tray |
| GUI, фоновые операции, tray | `src/ui/control_center.py`, `src/ui/tray_controls.py`, `src/tray_renderer.py`, `src/tray_settings.py` | Dashboard, общие команды меню через очередь; независимые каналы текста/фона по UUID; закрытие обычно скрывает в tray |
| Бренд | `src/branding.py`, `tools/build_brand_assets.py`, `assets/brand/` | Единая геометрия SVG/PNG/ICO; постоянная иконка отдельно от динамической телеметрии |
| Сценарии и реестры | `src/controller.py`, `src/profiles_schema.py`, `src/profile_storage.py` | Hardware/model/runtime/frontend/preset/services независимы |
| Аппаратные сведения | `src/hardware.py`, `src/hardware_topology.py`, `src/windows_graphics.py` | NVIDIA через nvidia-smi, DXGI для перечисления; UUID/LUID вместо предположения о номере карты |
| Совместимость и смена профилей | `src/compatibility.py`, `src/gpu_modes.py` | VRAM, CPU, выбранные GPU, readiness, pending mode; отсутствие NVLink само по себе не запрет |
| Запуск моделей | `src/model_backend.py`, `src/engines/llama_swap.py`, `src/process_manager.py` | Компиляция частного backend-конфига, loopback, HTTP readiness, загрузка/выгрузка |
| Владение процессами | `src/supervisor.py` | PID + время создания + EXE; журнал `processes.json`; скрытый backend, отдельная консоль только для выбранного терминального frontend |
| Агенты и их настройки | `src/agents/`, `src/agent_sync.py` | Манифесты/адаптеры, независимое обнаружение CLI/Desktop, официальные релизы, diff/backup/smoke/rollback |
| Сохранение и миграция | `src/storage.py`, `src/config.py`, `src/migration.py` | Атомарная запись, digest для внешних правок, резервные копии |
| Общие службы и секреты | `src/shared_services.py`, `src/service_profiles.py`, `src/ui/service_controls.py`, `src/secrets_store.py` | Форма и карточки вместо YAML, local/remote, ручная/30-секундная HTTP проверка, opt-in monitor, Credential Manager, backup/digest |
| Квалификация модели | `src/qualification.py` | Короткие реальные запросы и отчёт; не заменяет длительную пользовательскую задачу |
| Привилегированный helper | `src/services/LocalAgentGpuModeHelper.cs`, `gpu_mode_client.py`, `install_helper.ps1` в той же папке | Четыре типизированные операции, named pipe ACL, фиксированный подписанный nvidia-smi; основной GUI не требует повышения прав |
| Сборка | `build.ps1`, `LocalAgentAIStation.spec`, `requirements*.txt` | Тесты → C# → protocol checks → PyInstaller; исходники и текущий EXE могут иметь разные commits |

## Путь обычного действия

Пользователь выбирает модель в GUI → controller/менеджер профилей проверяет
совместимость → собирается частная конфигурация backend → supervisor запускает
llama-swap → readiness и реальная модель проверяются через API → состояние
возвращается в GUI. Выбор frontend проходит через адаптер агента; настройки
показываются до записи, соединение проверяется до объявления успеха.

GPU-переключение — отдельный сценарий. Приложение проверяет активные задачи,
останавливает только собственный backend, передаёт типизированный план helper,
повторно обнаруживает аппаратуру и различает live mode и pending/reboot.
`src/ui/gpu_confirmation.py` показывает названия карт и режимы, предупреждение TCC,
сохраняет отключение подтверждения только после согласия. Пустой план не открывает
окно; скрытая панель отправляет уведомление трея. Настройка
`suppress_gpu_switch_warning` не отключает проверки дисплея/службы. Перед исполнением
менеджер сверяет новый план с показанным `expected_plan`, включая пустой список.

У сервиса `type=remote` нет локальных start/stop-команд; старый программный start
означает только ручную проверку, stop возвращает отказ без обращения к supervisor.
`kind=comfyui` проверяет структуру ответа `/system_stats`, `kind=http` — HTTP статус
заданного health URL. `monitor_enabled=false` запрещает автоматические запросы,
ручной check не меняет этот параметр. GUI-форма по умолчанию выключает мониторинг;
старые записи без параметра сохраняют прежний автоматический опрос. В GUI создание
записи не устанавливает сервис или инструмент генерации в выбранный агент.

Автозагрузка Windows — единственный пользовательский ярлык `Local Agent AI Station.lnk`
в Startup, такой же, как у setup_autostart.ps1. Его состояние читается из Windows,
не из устаревшего config.autostart. В ярлыке сохраняются EXE/pythonw, --data-dir и
--startup; окно/трей определяется config.startup.minimized. Администратор не нужен.
Команды COM выполняются фиксированным скрытым PowerShell со входным JSON через stdin.
Настройка не создаёт отдельных заданий для моделей и не трогает старые GPU-службы.

config.startup хранит enabled/minimized/delay_seconds/model_id/frontend_ids/service_ids/
stop_on_error. По умолчанию компоненты выключены. После первого обнаружения GUI
запускает выбранные локальные службы, ждёт готовности модели, затем открывает
интерфейсы. Повторное обнаружение или показ существующей Station не повторяют запуск.
Новая привязка агента автоматически не записывается: при diff нужен ручной запуск.
Запуск агента из этого сценария сохраняет основной выбор пользователя (remember=False).
Отмена прерывает задержку/следующие шаги, текущее действие заканчивается штатно.
Результат — в logs/startup-last.json и в настройках; ошибка разворачивает окно.

## Где данные

Исходники не являются пользовательским home. По умолчанию это
`%LOCALAPPDATA%/LocalAgentAIStation`; возможны `--data-dir` и
`LOCAL_AGENT_STATION_HOME`. Реестры лежат в `config`, backend-конфиг — в `backend`,
журналы — в `logs`, свидетельства проверок — в `qualification`.
Расположения моделей, runtime, сторонних homes и текущий ПК смотри в локальном handoff.

## Тесты и известные границы

`tests/conftest.py` изолирует тестовые данные. `test_orchestration.py`,
`test_regressions_v3.py`, `test_final_regressions.py` проверяют сценарии и ошибки;
`test_universal_topologies.py` — матрицу GPU; `test_pi_openclaw.py` и
`test_agent_installation.py` — новые адаптеры и установочные ссылки.
`tests/helper_protocol.ps1` проверяет собранный протокол от обычного пользователя,
не устанавливая SYSTEM-службу и не переключая GPU.

Локальные интеграционные сценарии и снимки находятся в игнорируемом `runtime`.
Они могут ссылаться на конкретный ПК: сначала прочитай сценарий, проверь пути и
условия остановки, затем решай, подходит ли повторный запуск.
