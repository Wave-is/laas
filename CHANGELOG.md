# Changelog

## 0.2.0-beta.10

### English

- **Review Dialog & Worker Label Shadowing Fix**: Resolved `TypeError: unsupported operand type(s) for +: 'CTkLabel' and 'str'` when confirming agent synchronization by preventing local header widget assignment from shadowing the `label` parameter and safely converting worker action labels to strings.
- **Cluster Model Discovery Deduplication**: Automatically deduplicate local models discovered via LAN cluster node addresses, preventing redundant entries with `@ AI Station` suffixes from being added to Qwen Code Desktop settings.
- **Qwen Code Adapter Smoke Test Hardening**: Ensured the qualification workspace directory is created before executing CLI commands, preventing `[WinError 267]` (`NotADirectoryError`). Explicitly included `--auth-type openai`, `--model`, and `--openai-base-url` during configuration smoke tests, avoiding unauthorized fallback to Aliyun/DashScope.
- **Hardware Isolation & Gaming Preset**: Added `first_wddm_rest_tcc` topology evaluation logic and «Игры + ИИ» preset, reserving GPU 0 for gaming in WDDM while pinning AI models to GPU 1 in TCC.

### Русский

- **Исправление ошибки диалога синхронизации и воркера**: Устранена ошибка `TypeError: unsupported operand type(s) for +: 'CTkLabel' and 'str'` при нажатии «Применить показанные изменения» в диалоге подключения модели: заголовок окна больше не затеняет параметр `label`, а воркер безопасно приводит метки действий к строке.
- **Дедупликация моделей кластера**: Исключено появление локальных дубликатов с суффиксом `@ AI Station` в настройках Qwen Code Desktop при сетевом опросе собственного узла по LAN IP.
- **Надёжность smoke-теста адаптера Qwen Code**: Добавлено автоматическое создание каталога workspace (исключает сбой `[WinError 267]`). В аргументы проверки явно передаются `--auth-type openai`, `--model` и `--openai-base-url`, исключая ошибочную переадресацию в облако Aliyun/DashScope с ошибкой 401.
- **Аппаратная изоляция и пресет «Игры + ИИ»**: Добавлена политика `first_wddm_rest_tcc` в оценщик совместимости и встроенный пресет «Игры + ИИ», сохраняющий GPU 0 для игр в WDDM и выделяющий GPU 1 в TCC для вычислений ИИ.

### Українська

- **Виправлення помилки діалогу синхронізації та воркера**: Усунено помилку `TypeError: unsupported operand type(s) for +: 'CTkLabel' and 'str'` під час натискання «Застосувати показані зміни» в діалозі підключення моделі: заголовок вікна більше не затінює параметр `label`, а воркер безпечно перетворює мітки дій на рядок.
- **Дедуплікація моделей кластера**: Виключено появу локальних дублікатів із суфіксом `@ AI Station` у налаштуваннях Qwen Code Desktop під час мережевого опитування власного вузла за LAN IP.
- **Надійність smoke-тесту адаптера Qwen Code**: Додано автоматичне створення каталогу workspace (виключає помилку `[WinError 267]`). В аргументи перевірки явно передаються `--auth-type openai`, `--model` та `--openai-base-url`, усуваючи помилкову переадресацію в хмару Aliyun/DashScope з кодом 401.
- **Апаратна ізоляція та пресет «Ігри + ШІ»**: Додано політику `first_wddm_rest_tcc` в оцінювач сумісності та вбудований пресет «Ігри + ШІ», що зберігає GPU 0 для ігор у WDDM та виділяє GPU 1 у TCC для обчислень ШІ.

## 0.2.0-beta.9

### English

- **Cluster UI Refresh Redesign & In-Place Updates**: Decoupled cluster page updating from local GPU telemetry ticks. Replaced destructive widget rebuilds (`child.destroy()`) with smooth in-place card updates (`_update_node_card_inplace`), eliminating GUI flickering, scroll position jumps, and missed clicks.
- **Adjustable Polling Intervals & Default 30s**: Set default cluster refresh interval to relaxed 30 seconds (`30s`), with selectable modes in header: `30 sec`, `60 sec`, `2 min`, and `Manual`. Polling automatically pauses when user is not viewing the cluster page.
- **Manual Trigger with Live Status**: Added `[⟳ Refresh]` button with temporary `[⏳ Polling...]` busy state and last updated timestamp indicator.
- **Non-Blocking Async Sampling**: `ClusterManager.sample_async(callback)` samples nodes on a background thread with safe join timeouts, avoiding any UI stalls.
- **Seamless Updater & Setup Guard**: Installer automatically detects and closes running Station and LLM engine processes before file copy, eliminating `DeleteFile code 5 (Access Denied)` during updates.
- **Smarter GPU Helper Lifecycle**: GPU helper service task is now unchecked by default; on updates, existing services are safely stopped and restarted (`sc stop` / `sc start`) without unnecessary reinstallation.

### Русский

- **Переработка обновления вкладки кластера и in-place рендеринг**: обновление интерфейса кластера полностью отвязано от ежесекундного тика локальной телеметрии GPU. Вместо полного пересоздания виджетов (`child.destroy()`) внедрено гладкое in-place обновление карточек (`_update_node_card_inplace`), полностью устранившее мерцание, скачки скролла и потерю кликов.
- **Настраиваемые интервалы опроса и комфортный дефолт 30с**: интервал обновления по умолчанию установлен на спокойные 30 секунд (`30s`), в шапку добавлен выпадающий список: `30 сек`, `60 сек`, `2 мин` и `Вручную`. Фоновый опрос останавливается при переходе на другие вкладки.
- **Кнопка ручного опроса с индикацией**: кнопка `[⟳ Обновить]` с визуальным индикатором `[⏳ Опрос...]` и меткой времени последнего успешного обновления.
- **Неблокирующий асинхронный опрос**: метод `ClusterManager.sample_async(callback)` опрашивает узлы в фоновом потоке с безопасными таймаутами без малейшего зависания UI.
- **Бесшовное обновление инсталлятором и Setup Guard**: инсталлятор автоматически обнаруживает и закрывает работающие процессы Station и LLM перед распаковкой, исключая ошибку `DeleteFile код 5 (Отказано в доступе)`.
- **Умное управление службой GPU Helper**: чекбокс службы GPU снят по умолчанию; при обновлении существующая служба плавно перезапускается (`sc stop` / `sc start`) без переустановки.

### Українська

- **Переробка оновлення вкладки кластера та in-place рендеринг**: оновлення інтерфейсу кластера повністю відв'язано від щосекундного тіку локальної телеметрії GPU. Замість повного перевидалення віджетів (`child.destroy()`) впроваджено плавне in-place оновлення карток (`_update_node_card_inplace`), що повністю усунуло мерехтіння, стрибки скролу та втрату кліків.
- **Налаштовувані інтервали опитування та комфортний дефолт 30с**: інтервал оновлення за замовчуванням встановлено на спокійні 30 секунд (`30s`), у шапку додано випадний список: `30 сек`, `60 сек`, `2 хв` та `Вручную`. Фонове опитування зупиняється під час переходу на інші вкладки.
- **Кнопка ручного опитування з індикацією**: кнопка `[⟳ Оновити]` з візуальним індикатором `[⏳ Опитування...]` та міткою часу останнього успішного оновлення.
- **Неблокуюче асинхронне опитування**: метод `ClusterManager.sample_async(callback)` опитує вузли у фоновому потоці з безпечними таймаутами без найменшого зависання UI.
- **Безшовне оновлення інсталятором та Setup Guard**: інсталятор автоматично виявляє та закриває запущені процеси Station та LLM перед копіюванням файлів, усуваючи помилку `DeleteFile код 5 (Відмовлено в доступі)`.
- **Розумне керування службою GPU Helper**: встановлення служби GPU тепер вимкнено за замовчуванням; при оновленні наявна служба плавно перезапускається (`sc stop` / `sc start`) без перевстановлення.

## 0.2.0-beta.8

### English

- **Single Instance Mutex**: Kernel-level Win32 Named Mutex in `src/instance.py` preventing duplicate instances or duplicate tray icons on rapid double-clicks; secondary launches signal the running instance and exit cleanly.
- **Dynamic GPU Adaptation & Tray Defaults**: Default tray style changed to `dual_tile` (two metrics in one tray icon); tray icon count clamped to 1 when system has < 2 GPUs; model dialog GPU selector hides multi-GPU options on single-GPU systems.
- **Cluster UDP Auto-Discovery & Header Polish**: Added UDP broadcast beacon (`src/cluster_discovery.py`) on port 47150 with automatic TTL expiry and «🔍 Auto-discover» button with 1-click addition to cluster; restructured cluster summary header to prevent button overlap.
- **Overheat Alert Threshold**: Default temperature alert threshold set to 90°C.
- **Default Models Directory**: Automatically defaults to `D:\LLM` if drive D: exists (fallback to `C:\LLM`), with directory auto-creation on model downloads.
- **Agent Frontend Auto-Selection**: Automatically selects the first installed frontend (e.g. CLI/terminal) when preferred desktop app is absent, enabling immediate agent launch.
- **Navigation & Dashboard Layout Polish**: Left sidebar buttons compacted to 33px height to fit 768p displays; balanced dashboard padding (`pady=(5, 6)`) eliminating unnecessary scrollbars.
- **ComfyUI Cluster Sync**: Importing cluster nodes now automatically updates `SharedServices` active ComfyUI service URL and deploys the agent skill; clearly separated local vs remote ComfyUI UX.
- **Configurable Logging Directory**: Added `logs_dir` setting supporting local folders and UNC network shares (`\\server\share\laas-logs`), with fallback to default AppData if unreachable.

### Русский

- **Защита от повторного запуска (Named Mutex)**: нативный Win32 Named Mutex в `src/instance.py` исключает запуск дубликатов процесса и появление двойных иконок в трее при быстром двойном клике; второй запуск активирует окно первой копии и мгновенно завершается.
- **Адаптация под 1 GPU и трей по умолчанию**: стиль трея по умолчанию изменён на `dual_tile` (две метрики в одной иконке); число иконок ограничено одной при наличии менее 2 GPU; селектор GPU в диалоге моделей скрывает мульти-GPU при 1 карте.
- **Сетевой автопоиск узлов (UDP Discovery) и шапка кластера**: реализован UDP-маяк (`src/cluster_discovery.py`) на порту 47150 с автоочисткой по TTL и кнопка «🔍 Автопоиск в сети» с добавлением найденных узлов в кластер в 1 клик; шапка кластера разделена на заголовок и панель действий, исключая наложение кнопок.
- **Порог предупреждения о перегреве**: значение порога по умолчанию повышено до 90°C.
- **Дефолтная папка моделей D:\LLM**: автовыбор `D:\LLM` при наличии диска D: (или `C:\LLM`) с автосозданием папки при начале загрузки модели.
- **Автовыбор доступного фронтенда агента**: если десктопный клиент не установлен, автоматически выбирается первый установленный фронтенд (CLI/терминал), делая кнопку запуска агента активной сразу.
- **Компактизация боковой панели и баланс дашборда**: высота кнопок навигации уменьшена до 33px для экранов 768p; сбалансированные отступы карточек (`pady=(5, 6)`) убирают ложный скроллбар.
- **Синхронизация ComfyUI из кластера**: импорт нод обновляет активный URL ComfyUI в `SharedServices` и внедряет навык агентам; разделены сценарии локального и удалённого ComfyUI.
- **Настраиваемая папка логов**: добавлен параметр `logs_dir` с поддержкой локальных папок и сетевых путей UNC (`\\server\share\laas-logs`), с безопасным откатом на стандартную папку при недоступности сети.

### Українська

- **Захист від повторного запуску (Named Mutex)**: нативний Win32 Named Mutex у `src/instance.py` виключає запуск дублікатів процесу та появу подвійних іконок у треї при швидкому подвійному кліку; другий запуск активує вікно першої копії та миттєво завершується.
- **Адаптація під 1 GPU та трей за замовчуванням**: стиль трею за замовчуванням змінено на `dual_tile` (дві метрики в одній іконці); кількість іконок обмежена однією за наявності менше 2 GPU; селектор GPU в діалозі моделей приховує мульти-GPU при 1 карті.
- **Мережевий автопошук вузлів (UDP Discovery) та шапка кластера**: реалізовано UDP-маяк (`src/cluster_discovery.py`) на порту 47150 з автоочищенням за TTL та кнопка «🔍 Автопошук у мережі» з додаванням знайдених вузлів до кластера в 1 клік; шапка кластера розділена на заголовок і панель дій, виключаючи накладання кнопок.
- **Поріг попередження про перегрів**: значення порогу за замовчуванням підвищено до 90°C.
- **Дефолтна папка моделей D:\LLM**: автовибір `D:\LLM` за наявності диска D: (або `C:\LLM`) з автостворенням папки під час початку завантаження моделі.
- **Автовибір доступного фронтенду агента**: якщо десктопний клієнт не встановлено, автоматично вибирається перший встановлений фронтенд (CLI/термінал), роблячи кнопку запуску агента активною одразу.
- **Компактизація бічної панелі та баланс дашборду**: висота кнопок навігації зменшена до 33px для екранів 768p; збалансовані відступи карток (`pady=(5, 6)`) прибирають хибний скролбар.
- **Синхронізація ComfyUI з кластера**: імпорт вузлів оновлює активний URL ComfyUI у `SharedServices` та впроваджує навичку агентам; розділено сценарії локального та віддаленого ComfyUI.
- **Налаштовувана папка логів**: додано параметр `logs_dir` з підтримкою локальних папок та мережевих шляхів UNC (`\\server\share\laas-logs`), з безпечним відкатом на стандартну папку при недоступності мережі.

## 0.2.0-beta.7

### English

- **Cluster LLM Models Discovery**: Introduced `src/node_models.py` (`RemoteModel`, `query_node_models`, `discover_cluster_models`) which queries `/v1/models` across cluster LLM nodes (`llama_swap`, `llama_server`) to automatically discover distributed inference models with timeouts and error handling. Non-LLM nodes (ComfyUI) are automatically excluded.
- **Agent Knowledge & Model Synchronization**:
  - Extended `QwenCodeAdapter.configure_model_provider` to register remote cluster models into `modelProviders.local-agent-station` with their network `baseUrl` (`http://<node-ip>:<port>/v1`), enabling Qwen Code Desktop to seamlessly use models hosted across cluster nodes.
  - Aligned model provider signatures across all agent adapters (Base, Hermes, OpenClaw, Pi).
  - Integrated cluster model discovery into `StationController.preview_sync()`.
- **Automated Sync Pipeline & Cluster UI**:
  - Expanded `KnowledgeDistributor` (`src/skill_distributor.py`) with `sync_models_to_agents()` to update all detected agent runtimes without manual intervention.
  - Added auto-sync triggers to `ClusterManager` upon adding, modifying, or XML-importing cluster nodes.
  - Overhauled "📢 Share with Agents" on the Cluster page to deploy ComfyUI generation skills AND synchronize model catalogs with agents, reporting per-agent status with full EN/UK localization.

### Русский

- **Обнаружение LLM-моделей в кластере**: создан модуль `src/node_models.py` (`RemoteModel`, `query_node_models`, `discover_cluster_models`), опрашивающий OpenAI-совместимый эндпоинт `/v1/models` узлов инференса (`llama_swap`, `llama_server`) с таймаутами и обработкой ошибок. Узлы ComfyUI автоматически фильтруются.
- **Синхронизация моделей и знаний с агентами**:
  - Метод `QwenCodeAdapter.configure_model_provider` расширен поддержкой удалённых моделей: модели кластера регистрируются в `modelProviders.local-agent-station` с их сетевыми адресами (`http://<ip>:<port>/v1`), благодаря чему Qwen Code Desktop видит и может использовать модели с удалённых нод кластера.
  - Сигнатуры методов согласованы во всех адаптерах агентов (Base, Hermes, OpenClaw, Pi).
  - Интегрирован вызов обнаружения моделей в `StationController.preview_sync()`.
- **Автоматическая синхронизация и интерфейс кластера**:
  - В `KnowledgeDistributor` добавлен метод `sync_models_to_agents()` для автоматического обновления конфигураций агентов на диске.
  - В `ClusterManager` встроены триггеры автосинхронизации при добавлении, редактировании и XML-импорте узлов.
  - Кнопка «📢 Рассказать агентам» на странице кластера теперь одновременно разворачивает навык ComfyUI и синхронизирует модели кластера с агентами, выводя подробный отчёт с полной локализацией EN/UK.

### Українська

- **Виявлення LLM-моделей у кластері**: створено модуль `src/node_models.py` (`RemoteModel`, `query_node_models`, `discover_cluster_models`), що опитує OpenAI-сумісний ендпоінт `/v1/models` вузлів інференсу (`llama_swap`, `llama_server`) з таймаутами та обробкою помилок. Вузли ComfyUI автоматично фільтруються.
- **Синхронізація моделей та знань з агентами**:
  - Метод `QwenCodeAdapter.configure_model_provider` розширено підтримкою віддалених моделей: моделі кластера реєструються в `modelProviders.local-agent-station` з їхніми мережевими адресами (`http://<ip>:<port>/v1`), завдяки чому Qwen Code Desktop бачить та може використовувати моделі з віддалених вузлів кластера.
  - Сигнатури методів узгоджено в усіх адаптерах агентів (Base, Hermes, OpenClaw, Pi).
  - Інтегровано виклик виявлення моделей у `StationController.preview_sync()`.
- **Автоматична синхронізація та інтерфейс кластера**:
  - У `KnowledgeDistributor` додано метод `sync_models_to_agents()` для автоматичного оновлення конфігурацій агентів на диску.
  - У `ClusterManager` вбудовано тригери автосинхронізації під час додавання, редагування та XML-імпорту вузлів.
  - Кнопка «📢 Оповістити агентів» на сторінці кластера тепер одночасно розгортає навичку ComfyUI та синхронізує моделі кластера з агентами, виводячи детальний звіт із повною локалізацією EN/UK.

## 0.2.0-beta.6

### English

- **Dashboard Layout Reorganization**: Reordered blocks on the main "Station Overview" dashboard: the GPU hardware telemetry block ("Hardware Now") is now elevated above the "Model and Agent" control panel for immediate hardware visibility.
- **Vertical Compaction & Scrollbar Elimination**: Compacted UI layout vertically by overriding CustomTkinter's default 28px/42px single-line label height constraint (`height=0`), tightening control button/combo row margins from 14px to `(4, 5)`, and reducing tile padding. Total content height reduced by ~168px (from 867px down to 699px at 150% scaling), completely eliminating premature scrollbar appearance on standard display resolutions.

### Русский

- **Перестановка блоков на дашборде**: на главной странице «Обзор станции» блок мониторинга видеокарт («Оборудование сейчас») перемещён наверх — над блоком управления «Модель и агент» — для мгновенного контроля состояния оборудования.
- **Уплотнение вёрстки по вертикали**: устранена паразитная высота меток CustomTkinter (`height=0` для однострочных надписей), вертикальные отступы между рядами кнопок и списков сокращены с 14px до `(4, 5)`, уменьшены зазоры карточек GPU. Общая высота контента снижена на ~168px при масштабе 150% (с 867px до 699px), благодаря чему полоса прокрутки на дашборде больше не появляется при стандартной высоте окна.

### Українська

- **Перестановка блоків на дашборді**: на головній сторінці «Огляд станції» блок моніторингу відеокарт («Обладнання зараз») переміщено нагору — над блоком керування «Модель та агент» — для миттєвого контролю стану обладнання.
- **Ущільнення верстки по вертикалі**: усунено надлишкову висоту міток CustomTkinter (`height=0` для однорядкових написів), вертикальні відступи між рядами кнопок та списків скорочено з 14px до `(4, 5)`, зменшено проміжки карток GPU. Загальну висоту контенту зменшено на ~168px при масштабі 150% (з 867px до 699px), завдяки чому смуга прокручування на дашборді більше не з'являється за стандартної висоти вікна.

## 0.2.0-beta.5

### English

- **Automated Skill Distribution to AI Agents**: Added `SkillDistributor` (`src/skill_distributor.py`) which automatically detects installed AI agent runtimes (Qwen Code Desktop / CLI, Google Antigravity, OpenClaw, Hermes Agent) and deploys the `comfyui-image-gen` skill with zero-dependency CLI execution script, enabling agents to autonomously generate images, logos, UI designs, and mockups via the cluster's RTX 3060 ComfyUI worker.
- **UI Integration**: Added "📢 Share with Agents" button in the cluster header and "📢 Deploy Skill" button directly on ComfyUI node cards.
- **Auto-deployment Hooks**: Automatic skill deployment whenever a ComfyUI node or service is added, updated, or imported via XML.
- **Telegraf Telemetry Restored & Firewall Hardened**: Pinned `hostname = "ollama-1"` in Telegraf config restoring existing InfluxDB/Grafana dashboards after system hostname change, restricted allowed IP range to authorized cluster nodes, and ensured dual InfluxDB + Prometheus export without disruption.

### Русский

- **Автоматическая дистрибуция навыков агентам**: добавлен модуль `SkillDistributor` (`src/skill_distributor.py`), автоматически находящий установленные среды агентов (Qwen Code Desktop / CLI, Google Antigravity, OpenClaw, Hermes Agent) и внедряющий навык `comfyui-image-gen` со скриптом вызова без внешних зависимостей.
- **Интеграция в UI**: добавлена кнопка «📢 Рассказать агентам» в шапке страницы кластера и кнопка «📢 Навык агентам» на карточке узла ComfyUI.
- **Хуки авторазвертывания**: навык автоматически развертывается при импорте XML или обновлении узлов/сервисов ComfyUI.
- **Восстановление телеметрии InfluxDB и сужение доступа Telegraf**: зафиксирован `hostname = "ollama-1"` в конфигурации Telegraf, что восстановило сбор в существующие дашборды InfluxDB/Grafana, доступ к метрикам ограничен доверенными узлами кластера, сохранен одновременный экспорт в InfluxDB и Prometheus.

### Українська

- **Автоматична дистрибуція навичок агентам**: додано модуль `SkillDistributor` (`src/skill_distributor.py`), що автоматично виявляє встановлені середовища агентів (Qwen Code Desktop / CLI, Google Antigravity, OpenClaw, Hermes Agent) та впроваджує навичку `comfyui-image-gen` зі скриптом виклику без зовнішніх залежностей.
- **Інтеграція в UI**: додано кнопку «📢 Оповістити агентів» у шапці сторінки кластера та кнопку «📢 Навичка агентам» на картці вузла ComfyUI.
- **Хуки авторозгортання**: навичка автоматично розгортається при імпорті XML або оновленні вузлів/сервісів ComfyUI.
- **Відновлення телеметрії InfluxDB та звуження доступу Telegraf**: зафіксовано `hostname = "ollama-1"` у конфігурації Telegraf, що відновило збір у наявні дашборди InfluxDB/Grafana, доступ до метрик обмежено довіреними вузлами кластера, збережено одночасний експорт в InfluxDB та Prometheus.

## 0.2.0-beta.4

### English

- **Windows Telegraf-Compatible Telemetry Server**: Built-in zero-dependency telemetry server listening on `:9273/metrics` providing standard Telegraf Prometheus metrics (`nvidia_smi_*`, `cpu_usage_active`, `mem_*`, etc.) on Windows nodes.
- **ComfyUI Image Generation Integration**: Native support for ComfyUI nodes in the cluster with live queue status monitoring (`/prompt`), active generation badges, and hardware stats.
- **Service Profiles in XML Topology**: `<services>` section added to `cluster_topology.xml`, enabling automated registration and management of local/remote worker services (e.g. ComfyUI) on cluster import.
- **Clean Service Management**: Removed legacy Windows startup shortcut for ComfyUI, transferring its lifecycle directly into LAAS managed shared services (`services.yaml`).

### Русский

- **Встроенный Telegraf-совместимый сервер телеметрии для Windows**: встроенный HTTP-сервер на порту `:9273/metrics`, отдающий метрики оборудования в стандартном формате Telegraf Prometheus (`nvidia_smi_*`, `cpu_usage_active`, `mem_*`), объединяющий Windows- и Linux-машины под единым протоколом.
- **Интеграция ComfyUI в кластер**: полноценная поддержка узлов генерации изображений ComfyUI с отслеживанием очереди задач (`/prompt`), визуальными бейджами статуса генерации и мониторингом оборудования.
- **Сервисы в XML-топологии**: добавлена секция `<services>` в `cluster_topology.xml`, позволяющая автоматически переносить и регистрировать профили сервисов (ComfyUI) при импорте настроек кластера.
- **Чистое управление сервисами**: удален устаревший ярлык автозапуска ComfyUI из автозагрузки Windows, запуск и контроль сервиса полностью переведены в менеджер сервисов LAAS (`services.yaml`).

### Українська

- **Вбудований Telegraf-сумісний сервер телеметрії для Windows**: вбудований HTTP-сервер на порту `:9273/metrics`, що віддає метрики обладнання у стандартному форматі Telegraf Prometheus (`nvidia_smi_*`, `cpu_usage_active`, `mem_*`), об'єднуючи Windows- та Linux-машини під єдиним протоколом.
- **Інтеграція ComfyUI до кластера**: повноцінна підтримка вузлів генерації зображень ComfyUI з відстеженням черги завдань (`/prompt`), візуальними бейджами статусу генерації та моніторингом обладнання.
- **Сервіси в XML-топології**: додано секцію `<services>` до `cluster_topology.xml`, що дозволяє автоматично переносити та реєструвати профілі сервісів (ComfyUI) при імпорті налаштувань кластера.
- **Чисте керування сервісами**: видалено застарілий ярлик автозапуску ComfyUI з автозавантаження Windows, запуск та контроль сервісу повністю переведено в менеджер сервісів LAAS (`services.yaml`).

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

