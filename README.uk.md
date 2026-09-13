<p align="center"><img src="assets/brand/station.svg" width="104" alt="Local Agent AI Station"></p>
<h1 align="center">Local Agent AI Station</h1>
<p align="center">Локальні моделі, агенти та GPU — в одній Windows-панелі.</p>
<p align="center"><a href="README.ru.md">Русский</a> · <b>Українська</b> · <a href="README.md">English</a></p>
<p align="center"><a href="https://github.com/Wave-is/laas/releases">Завантажити ранню версію</a> · <a href="docs/GETTING_STARTED.uk.md">Початок роботи</a> · <a href="https://github.com/Wave-is/laas/issues">Повідомити про помилку</a></p>

**3.0.0-alpha.1 · Рання версія · Windows 10/11 x64 · MIT**

Station об'єднує запуск моделей, інтерфейси агентів, стан обладнання та керування
з трея. Працює з CPU й різною кількістю GPU; можливості відеокарт залежать
від обладнання та драйверів.

| Розділ | Можливості |
|---|---|
| Головна панель | Стани моделі, сервера й агента, показники GPU, запуск і зупинка |
| Агенти | Виявлення Qwen Code/Desktop, Hermes, Pi Coding Agent, OpenClaw та Aider; офіційні релізи; керування власними процесами |
| Моделі | Профілі llama.cpp/llama-swap, вибір пристроїв, запуск і вивантаження |
| Трей | Швидкі команди, два налаштовувані канали показників із цифрами та заповненням тла |
| Служби | Локальні процеси та віддалені ComfyUI/HTTP worker з вимиканням опитування |
| Автозапуск | Окремий вибір запуску з Windows і запуску служб, моделі, агентів; затримка та скасування |

## Встановлення

Завантажте **LocalAgentAIStation-3.0.0-alpha.1-Setup-x64.exe** з
[Releases](https://github.com/Wave-is/laas/releases). Інсталятор доступний українською,
російською та англійською. Python включено, права адміністратора не потрібні.
Інтерфейс застосунку поки російською. Агенти, ваги моделей і рушії встановлюються
окремо. Під час першого запуску вони автоматично не вмикаються.

Програма: `%LOCALAPPDATA%\Programs\Local Agent AI Station`.
Дані: `%LOCALAPPDATA%\LocalAgentAIStation`. Є ярлики «Пуска» й робочого столу,
видалення засобами Windows та оновлення поверх встановленої версії.
Докладніше — [початок роботи](docs/GETTING_STARTED.uk.md).

## Межі ранньої версії

- Підпису коду й автоматичного оновлення поки немає.
- Моніторинг GPU включено. Для TCC/WDDM потрібні сумісні NVIDIA та окремий
  привілейований helper; інсталятор його не встановлює. Повна перевірка встановленої
  нової служби ще не завершена. Див. [GPU helper](docs/GPU_MODE_HELPER.md).
- Фізичні тести: CPU, одна вибрана NVIDIA та дві NVIDIA на одному ПК.
  Тести моделюють 0/1/2/3/4/8 GPU; інші реальні конфігурації не сертифіковані.
- Є інтеграційні перевірки Qwen Desktop, Pi й OpenClaw. Підтримка адаптера не
  гарантує сумісність із кожною майбутньою версією агента. Див. [перевірки](docs/VALIDATION.md).
- Підключення інструмента генерації зображень до агентів і тривала перевірка
  максимального контексту ще в роботі. Запис віддаленої служби сам по собі не створює інструмент агента.

## Розробка

[Участь](CONTRIBUTING.md) · [Архітектура](docs/ARCHITECTURE.md) ·
[Збірка релізу](docs/RELEASING.md) · [Подальша робота](ROADMAP.md).
[HANDOFF](docs/HANDOFF.md) зберігає стан розробки; приватні налаштування,
журнали й відомості про ПК до Git не входять. Помилки можна описувати будь-якою з трьох мов.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build_installer.ps1 -Python .\.venv\Scripts\python.exe -ISCC 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Перед пакуванням зафіксуйте зміни в Git. Залежності описані в
[RELEASING](docs/RELEASING.md). Із вихідного коду: `.venv\Scripts\pythonw.exe main.pyw --no-startup`.

[Ліцензія MIT](LICENSE), [ліцензії залежностей](docs/THIRD_PARTY.md).
Station — незалежний проєкт, не пов'язаний із виробниками агентів і моделей.
