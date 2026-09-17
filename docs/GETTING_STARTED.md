# Getting started

[Русский](GETTING_STARTED.ru.md) · [Українська](GETTING_STARTED.uk.md)

1. Download `LocalAgentAIStation-<version>-Setup-x64.exe` from
   [Releases](https://github.com/Wave-is/laas/releases). Choose the release marked **Pre-release**.
2. Run Setup, choose a language and install. Windows 10/11 x64 is required;
   Python is included. The current preview has no code-signing certificate.
3. Open **Local Agent AI Station** from Start or the desktop. The dashboard shows
   the current model, agents and detected GPUs. Nothing starts automatically on first launch.
4. On **Агенты** (Agents), select an installed interface and press **Запустить**.
   **Установить** opens the agent's official releases. Install the agent separately,
   then press **Повторить обнаружение**. **Остановить** stops an instance owned by Station.
5. For local inference, configure a llama.cpp/llama-swap executable and a model profile
   under **Модели**. See [model profiles](MODEL_PROFILES.md) and `docs/examples`.
   Model weights and agent subscriptions/accounts are not included.
6. In **Настройки**, optionally enable Windows startup. Component startup is a
   separate choice: local services → model readiness → agent interfaces. Set a delay,
   choose the components and save. `--no-startup` skips component startup for recovery.

**Services:** add a local process with a command, or a remote ComfyUI/HTTP endpoint.
Remote services have Check/Open actions; Station does not start a remote computer.
Monitoring can be paused. See [service examples](SERVICES_GUIDE_RU.md).

**Tray:** right-click for model/agent/GPU controls. Closing the window normally
hides it in the tray; choose **Выход** to fully close Station. Tray appearance and
two metric channels are configurable in Settings.

**GPU modes:** monitoring works without the optional privileged helper. Mode switching
requires compatible NVIDIA hardware and the [helper](GPU_MODE_HELPER.md).
Disconnect monitors from any card being switched to TCC. The helper is not installed
by Setup and end-to-end acceptance of the installed service is still pending.

## Locations, updates and removal

| Item | Default location |
|---|---|
| Program | `%ProgramFiles%\Local Agent AI Station` |
| Settings, profiles, logs | `%LOCALAPPDATA%\LocalAgentAIStation` |
| Start menu / desktop | Ordinary current-user Windows shortcuts |

Exit Station, then run the new installer over the existing installation. Settings
are preserved. This preview uses manual updates; there is no automatic updater.
Uninstall through **Settings → Apps → Installed apps → Local Agent AI Station**.
Uninstall removes program files, its shortcuts and its Windows startup entry;
user data, models and external agents remain. Back up the data folder before a downgrade.

For troubleshooting, open the logs from Station or run:

```powershell
& "$env:ProgramFiles\Local Agent AI Station\LocalAgentAIStation.exe" --doctor --output "$env:TEMP\station-doctor.json"
```

Diagnostics can contain local paths, GPU identifiers and agent configuration details.
Review and redact them before attaching them to an issue. Do not upload API keys.
