# Changelog

## 0.2.0-beta.3

### English

- **Distributed LLM Cluster Tab**: Added dedicated "LLM Cluster" control center page for real-time monitoring and configuration of multi-host GPU inference clusters.
- **Telegraf Prometheus Standard**: Native integration with Telegraf Prometheus exporter on Linux nodes (`:9273/metrics`), providing zero-dependency GPU, CPU, and RAM telemetry over pure HTTP without SSH.
- **Cluster XML Import & Export**: One-click XML export and import (`cluster_topology.xml`) to easily distribute and synchronize cluster topologies across multiple workstations without manual entry.
- **Live Inference Telemetry**: Real-time tracking of Llama-server generation states, active slots, prompt lengths, token rates, and remaining context across all cluster nodes.

### Русский

- **Вкладка «LLM-кластер»**: добавлен новый полнофункциональный раздел центра управления для распределённого мониторинга и настройки пула видеокарт и инференса.
- **Стандартизация через Telegraf Prometheus**: нативная интеграция со стандартным Telegraf Prometheus на Linux-узлах (`:9273/metrics`), исключающая необходимость использования SSH или самописных агентов.
- **Импорт и экспорт кластера в XML**: возможность сохранения и загрузки полной топологии кластера через XML-файл (`cluster_topology.xml`) в один клик для мгновенного переноса настроек между ПК.
- **Телеметрия инференса в реальном времени**: отображение активных слотов Llama-server, размера промпта, количества сгенерированных токенов и доступного контекста.

### Українська

- **Вкладка «LLM-кластер»**: додано новий повнофункціональний розділ центру керування для розподіленого моніторингу та налаштування пулу відеокарт та інференсу.
- **Стандартизація через Telegraf Prometheus**: нативна інтеграція зі стандартним Telegraf Prometheus на Linux-вузлах (`:9273/metrics`), що повністю виключає необхідність використання SSH або сторонніх скриптів.
- **Імпорт та експорт кластера в XML**: можливість збереження та завантаження повної топології кластера через XML-файл (`cluster_topology.xml`) в один клік для миттєвого переносу налаштувань між ПК.
- **Телеметрія інференсу в реальному часі**: відображення активних слотів Llama-server, розміру промпту, кількості згенерованих токенів та доступного контексту.

## 0.2.0-beta.2

### English

- **Installer Language Selection**: Enabled mandatory language prompt (`ShowLanguageDialog=yes`, `UsePreviousLanguage=no`) on every installer run, allowing users to explicitly choose between English, Russian, and Ukrainian even when upgrading.
- **UI Polish**: Automatic hiding and slim translucent styling of page scrollbars when content fits within the window; eliminated persistent gray scrollbar ribbons on the dashboard.
- **Removed Development Stubs**: Fully completed all sections and dialogs; cleaned up legacy placeholders.

### Русский

- **Выбор языка в инсталляторе**: включён обязательный диалог выбора языка установки (`ShowLanguageDialog=yes`, `UsePreviousLanguage=no`) при каждом запуске инсталлятора (английский, русский, украинский), в том числе при обновлении поверх существующей версии.
- **Улучшения интерфейса**: автоматическое скрытие полос прокрутки (auto-hide) и тонкий прозрачный трек, убирающий статичные серые полосы при помещении страницы в окно.
- **Удаление заглушек**: все разделы и диалоги полностью реализованы и готовы к работе, удалены устаревшие заглушки «в разработке».

### Українська

- **Вибір мови в інсталяторі**: увімкнено обов'язковий діалог вибору мови встановлення (`ShowLanguageDialog=yes`, `UsePreviousLanguage=no`) при кожному запуску інсталятора (англійська, українська, російська), зокрема при оновленні поверх існуючої версії.
- **Покращення інтерфейсу**: автоматичне приховування смуг прокручування (auto-hide) та тонкий прозорий трек, що прибирає статичні сірі смуги, коли вміст вміщується у вікно.
- **Видалення заглушок**: усі розділи та діалоги повністю реалізовані та готові до роботи, видалено застарілі заглушки «у розробці».

## 0.2.0-beta.1

### English

- **Schedules & Idle Unload**: Automated time-based model loading and GPU profile switching, day-of-week recurrence, and configurable idle model auto-unload timer.
- **Model Management Suite**: Complete dialog suite wired into Models page:
  - Add Model dialog with GGUF header parser and KV-cache VRAM estimator.
  - Scan Folder dialog with unindexed model file detection and batch onboarding.
  - Hugging Face downloader dialog with resumable `.part` downloads and SHA-256 validation.
  - Quick Chat dialog with active model querying and token limiter.
  - Move Models Folder dialog with file-by-file verification and original retention.
- **Reliability & Watchdog**: ModelServerWatchdog daemon with crash/hang detection and deliberate stop suppression; MetricsStore SQLite history with pure-Tk canvas charts (temp, load, VRAM, power); diagnostics bundle collector.
- **Maintenance & Updates**: Engine updates (llama.cpp/llama-swap version detection and rollback), Station update checker, backup/restore manager with secret redaction.
- **Hardware Telemetry**: Live power draw, fan speed, PCIe link, throttle reasons, and NVLink bandwidth cards on Hardware page.
- **100% Localization**: Complete English, Russian, and Ukrainian translation catalogs across all pages and dialogs.
- **UI Polish**: Automatic hiding and slim translucent styling of page scrollbars when content fits within the window.

### Русский

- **Расписания и автовыгрузка**: автоматическая загрузка моделей и переключение GPU по времени, дням недели и таймер автовыгрузки неиспользуемой модели.
- **Управление моделями**: диалоги добавления модели (GGUF-парсер, оценка видеопамяти), сканирования папки, загрузки с Hugging Face, быстрого чата и переноса папки моделей.
- **Надёжность и мониторинг**: Watchdog-демон перезапуска сервера моделей, SQLite-хранилище метрик с графиками на чистом Tk, сборщик диагностического архива и просмотр логов.
- **Обслуживание и обновления**: обновление движков (llama.cpp/llama-swap), проверка релизов Station, менеджер резервных копий с очисткой секретов.
- **Телеметрия оборудования**: мощность, скорость вентиляторов, режим шины PCIe и причины троттлинга на странице оборудования.
- **Полная локализация**: 100% покрытие каталогов переводов для русского, английского и украинского языков.
- **Улучшения интерфейса**: автоматическое скрытие полос прокрутки (auto-hide) и тонкий прозрачный трек, убирающий статичные серые полосы при помещении страницы в окно.

### Українська

- **Розклади та авторозвантаження**: автоматичне завантаження моделей і перемикання GPU за часом, днями тижня та таймер авторозвантаження моделі при бездіяльності.
- **Керування моделями**: діалоги додавання моделі (GGUF-парсер, оцінка відеопам'яті), сканування папки, завантаження з Hugging Face, швидкого чату та перенесення папки моделей.
- **Надійність та моніторинг**: Watchdog-демон перезапуску сервера моделей, SQLite-сховище метрик із графіками на чистому Tk, збирач діагностичного архіву та перегляд журналів.
- **Обслуговування та оновлення**: оновлення рушіїв (llama.cpp/llama-swap), перевірка релізів Station, менеджер резервних копій із приховуванням секретів.
- **Телеметрія обладнання**: потужність, швидкість вентиляторів, режим шини PCIe та причини тротлінгу на сторінці обладнання.
- **Повна локалізація**: 100% покриття каталогів перекладів для української, англійської та російської мов.
- **Покращення інтерфейсу**: автоматичне приховування смуг прокручування (auto-hide) та тонкий прозорий трек, що прибирає статичні сірі смуги, коли вміст вміщується у вікно.

## 0.1.0-beta.1

First public beta. Versions 3.0.0-alpha.1/2 were internal previews; numbering restarts at 0.x until a stable 1.0.0.

- Interface in English, Russian and Ukrainian (Settings → Interface language).
- Separate Startup page; presets removed from the interface.
- Built-in model engine (llama.cpp + llama-swap) and GPU mode service installed by Setup; one-time administrator prompt when the service is absent.
- Tray: short name LAAS, readable tooltip values.

## 3.0.0-alpha.2 (internal)

### Русский

- Один сервер моделей: кнопки «Запустить сервер» (Службы, трей) и загрузка модели
  используют одну конфигурацию, созданную из профилей моделей. Старая ручная настройка
  «Конфигурация llama-swap» больше не используется.
- Везде видно, какой сервер работает: адрес и порт, PID, кто запустил (Station или
  внешний процесс), адреса в локальной сети, файл конфигурации, журнал, веб-панель.
- Доступ к серверу моделей из локальной сети — отдельная галочка в настройках
  (по умолчанию выключена, сервер слушает только 127.0.0.1).
- Настройки «Папка движка» (llama.cpp + llama-swap, exe ищутся автоматически) и
  «Папка моделей» (пути в профилях можно задавать относительно неё).
- Автозапуск агента больше не останавливается, если в настройках агента есть устаревшие
  записи о других моделях: достаточно, чтобы агент был настроен на выбранную модель.
- Неизвестные поля в профилях моделей больше не ломают запуск программы: они сохраняются
  и показываются как предупреждение. Ошибка настроек при старте показывает пути и
  предлагает открыть папку настроек.
- Установщик ставит программу в C:\Program Files (для всех пользователей), удаляет старую
  копию из %LOCALAPPDATA%\Programs и сохраняет автозагрузку. Данные остаются на месте.
- Сообщения интерфейса и агентов переведены и уточнены: что произошло, с каким
  компонентом и что делать. Единые названия кнопок; журналы процессов с понятными именами.
- Главная страница: строка управления сервером моделей (адрес, PID, запуск/остановка).
- Статус агента проверяет реальный процесс: «Не запущен · установлен», «Запущен из Station»
  или «Запущен вне Station» (Desktop, открытый с ярлыка, тоже виден).
- Меню трея: три режима GPU прямо в корне — «Все GPU в WDDM», «Все GPU в TCC»,
  «Первая GPU в WDDM, остальные в TCC»; галочка по фактическим режимам драйвера.


## 3.0.0-alpha.1

### English

First packaged preview: per-user Windows installer with English/Russian/Ukrainian
setup and documentation; standard shortcuts, uninstall and in-place installation.
Dashboard, agent start/stop, model profiles, configurable tray telemetry, service
management and opt-in startup. Clean initial repository without private machine history.
The GPU helper remains optional and experimental; application UI is currently Russian.

### Русский

Первый выпуск с установщиком: установка для текущего пользователя Windows,
русское/украинское/английское оформление, стандартные ярлыки, удаление и обновление
поверх установленной версии. Дашборд, запуск/остановка агентов, профили моделей,
настраиваемые показатели трея, службы и автозапуск по выбору пользователя.
Репозиторий начинается с чистого снимка без частной истории ПК. GPU-helper остаётся
отдельным экспериментальным компонентом; интерфейс приложения пока русский.

### Українська

Перший випуск з інсталятором: встановлення для поточного користувача Windows,
українське/російське/англійське оформлення, стандартні ярлики, видалення та оновлення
поверх встановленої версії. Головна панель, запуск/зупинка агентів, профілі моделей,
налаштовувані показники трея, служби й автозапуск за вибором користувача.
Репозиторій починається з чистого знімка без приватної історії ПК. GPU-helper
залишається окремим експериментальним компонентом; інтерфейс застосунку поки російською.

