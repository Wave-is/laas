# Pi Coding Agent и OpenClaw

Оба агента необязательны. Qwen Code Desktop остаётся предпочтительным интерфейсом
по умолчанию. GPU, модель, агент и интерфейс выбираются независимо.

## Обнаружение и установка

Station обнаруживает CLI в PATH и обычных глобальных npm-каталогах Windows.
Для npm-установок запускается Node.js с entrypoint из package.json: содержимое
pi.cmd/openclaw.cmd не передаётся командной оболочке. Пути с пробелами поддерживаются.
Можно задать package_root, executable и node в agent_runtimes.yaml; пример находится
в [examples/agent_runtimes.yaml](examples/agent_runtimes.yaml).

Проверенные версии: Pi 0.85.1, OpenClaw 2026.9.4, Node.js 24.18.0.
Pi теперь выпускается как @earendil-works/pi-coding-agent; обнаружение также знает
прежнее имя @mariozechner/pi-coding-agent. С прежним пакетом выполнены контрактные
тесты обнаружения, но реальный запрос проверен только с 0.85.1.

Если runtime ещё не установлен, его можно установить самостоятельно:
нажмите «Установить» на странице «Агенты» для перехода к официальным релизам
или используйте приведённые ниже команды npm.

```powershell
npm install -g @earendil-works/pi-coding-agent@0.85.1
npm install -g openclaw@2026.9.4
```

Требования Node проверяются самим runtime; для указанных версий подходит Node 24.18.
После установки нажмите «Повторить обнаружение» на странице «Агенты». Station не запускает
установку, onboarding или установку системной службы OpenClaw автоматически.
WSL-установка не считается установленным Windows CLI; запуск через WSL в этот адаптер
не входит. Неподдерживаемые параметры старых CLI показываются как недоступные.

## Модели и настройки

Сначала запустите модель в Station, затем выберите интерфейс агента и нажмите
«Открыть». При необходимости приложение покажет изменения provider/binding.
После применения выполняется реальный запрос к модели; ошибка вызывает откат.
Кнопка «Остановить» завершает только процесс, ранее запущенный Station.

| Агент | Файлы | Способ запуска |
|---|---|---|
| Pi | ~/.pi/agent/models.json и settings.json | Интерактивный Pi с выбранными provider/model |
| OpenClaw | ~/.openclaw/openclaw.json | tui --local; дополнительный gateway run |

Для Pi параметр home означает каталог agent, содержащий оба JSON-файла, и
передаётся через PI_CODING_AGENT_DIR. Оба файла применяются одной транзакцией:
проверяются конфликты, создаются копии, выполняется smoke и при его ошибке
откатываются собственные изменения. Новые чужие правки при откате сохраняются.

Для OpenClaw home задаёт OPENCLAW_STATE_DIR. config_path позволяет отдельно указать
OPENCLAW_CONFIG_PATH. Явный home в Station имеет приоритет над ambient config path.
Обычный JSON5 с комментариями и завершающими запятыми читается; после применения
сохраняется стандартный JSON. Исходный текст с комментариями остаётся в backup.
Повторяющиеся ключи отвергаются. Автоматическое редактирование конфигураций с
$include не поддерживается: используйте собственный редактор OpenClaw либо отдельный home.

Каждый профиль модели получает отдельный provider с префиксом local-agent-station-.
Это позволяет иметь одинаковые backend ID на разных endpoint. Профили none и
disabled не добавляются. Старые provider-записи удалённых профилей автоматически
не удаляются. Остальные провайдеры, credentials, каналы и настройки сохраняются.
Модель OpenClaw задаётся в defaults и в явном model override агента по умолчанию,
если такой override существует. Fallback-модели у выбранного binding очищаются
в видимом diff, чтобы запуск выбранной локальной модели не уходил на другой provider.
Другие агенты OpenClaw сохраняют собственные привязки.

OpenClaw проверяет полный конфиг собственной командой config validate. Затем
изолированный agent exec проверяет сохранённый provider/model без переопределения
модели командной строкой. Тест копирует только эту привязку, отключает инструменты
и плагины и не копирует каналы или сессии. Для Windows явно выделяется state-dir:
в 2026.9.4 встроенная очистка ephemeral state воспроизводимо завершалась ошибкой
EBUSY на SQLite. Выделенный каталог удаляется Station после выхода процесса.

Pi smoke также использует копию сохранённых provider/defaultModel, отключённые
инструменты и расширения. Оба теста проверяют имя provider, точный ID модели,
ответ STATION_OK и код завершения; один успешный HTTP-ответ недостаточен.

## Шлюз OpenClaw

Локальный терминал не требует шлюза. Для отдельного шлюза сначала настройте
gateway.mode=local и авторизацию средствами OpenClaw. Station запускает его в
foreground под собственным надзором, с bind=loopback и отключённым Tailscale;
системная служба не устанавливается. gateway_port задаётся при необходимости.
Station не завершает чужой listener через --force. Локальный терминал OpenClaw и
шлюз не могут одновременно владеть одним state-dir; это ограничение самого runtime.

## Первичные документы

- [Pi: каталог моделей](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md)
- [Pi: настройки](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/settings.md)
- [Pi: CLI и переменные окружения](https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/README.md)
- [OpenClaw: собственные провайдеры](https://docs.openclaw.ai/concepts/model-providers/custom-providers)
- [OpenClaw: agent exec](https://docs.openclaw.ai/cli/agent#agent-exec)
- [OpenClaw: TUI](https://docs.openclaw.ai/cli/tui)
- [OpenClaw: шлюз](https://docs.openclaw.ai/cli/gateway)
