<p align="center"><img src="assets/brand/station.svg" width="104" alt="Local Agent AI Station"></p>
<h1 align="center">Local Agent AI Station</h1>
<p align="center">Your local models, agents and GPUs. One Windows control center.</p>
<p align="center"><a href="README.ru.md">Русский</a> · <a href="README.uk.md">Українська</a> · <b>English</b></p>
<p align="center"><a href="https://github.com/Wave-is/laas/releases">Download preview</a> · <a href="docs/GETTING_STARTED.md">Getting started</a> · <a href="https://github.com/Wave-is/laas/issues">Report an issue</a></p>

**3.0.0-alpha.1 · Early preview · Windows 10/11 x64 · MIT**

Station brings model launch, agent interfaces, hardware status and tray controls into
one desktop application. It works with CPU-only systems and different GPU counts;
GPU features depend on the hardware and driver capabilities.

| Area | What you can do |
|---|---|
| Dashboard | See model/server/agent status and GPU telemetry; launch and stop components |
| Agents | Discover Qwen Code/Desktop, Hermes, Pi Coding Agent, OpenClaw and Aider; open official installers/releases; manage owned processes |
| Models | Configure llama.cpp/llama-swap profiles, select devices, launch and unload local models |
| Tray | Use quick controls and two configurable metric channels with numeric values and background fill |
| Services | Manage local processes; check remote ComfyUI/HTTP workers with optional monitoring |
| Startup | Choose Windows startup separately from services, model and agent startup; set a delay and cancel |

## Install

Download **LocalAgentAIStation-3.0.0-alpha.1-Setup-x64.exe** from
[Releases](https://github.com/Wave-is/laas/releases). Choose English, Russian or
Ukrainian in Setup. Python is bundled; no administrator rights are required.
The application UI is currently Russian. Agents, model weights and inference engines
are separate installations. First launch does not automatically start them.

The program uses `%LOCALAPPDATA%\Programs\Local Agent AI Station`; its data uses
`%LOCALAPPDATA%\LocalAgentAIStation`. Start-menu and optional desktop shortcuts,
Windows uninstall and in-place updates are supported. See the
[quick start](docs/GETTING_STARTED.md) for setup, updates and removal.

## Preview boundaries

- This release is unsigned. There is no automatic updater yet.
- GPU monitoring is included. TCC/WDDM changes require compatible NVIDIA cards and
  an optional privileged helper, which Setup does not install. Installed-service
  end-to-end acceptance is still pending; see [GPU helper](docs/GPU_MODE_HELPER.md).
- Physical testing used CPU, one selected NVIDIA GPU and two NVIDIA GPUs on one PC.
  Tests simulate 0/1/2/3/4/8 GPU topologies; other physical configurations are not certified.
- Qwen Desktop, Pi and OpenClaw have integration evidence; adapter coverage does not
  guarantee compatibility with every upstream release. See [validation](docs/VALIDATION.md).
- Agent image-generation tool integration and prolonged maximum-context qualification
  remain work in progress. A configured remote service is not automatically an agent tool.

## Develop and contribute

See [CONTRIBUTING](CONTRIBUTING.md), [architecture](docs/ARCHITECTURE.md),
[release procedure](docs/RELEASING.md) and [roadmap](ROADMAP.md).
Development checkpoints live in [HANDOFF](docs/HANDOFF.md); private machine notes,
settings and test evidence stay outside Git. Report bugs in English, Russian or Ukrainian.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build_installer.ps1 -Python .\.venv\Scripts\python.exe -ISCC 'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
```

Commit your source changes before packaging. Build dependencies and setup details
are described in [RELEASING](docs/RELEASING.md). The source can also be run with
`.venv\Scripts\pythonw.exe main.pyw --no-startup`.

[MIT license](LICENSE). Bundled dependencies retain their own licenses;
see [third-party notices](docs/THIRD_PARTY.md). Station is an independent project
and is not affiliated with the agent or model vendors.
