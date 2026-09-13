# Contributing / Участие / Участь

English, Russian and Ukrainian issues and pull requests are welcome.
Сообщения и исправления на всех трёх языках приветствуются.
Повідомлення та виправлення всіма трьома мовами вітаються.

Read [AGENTS.md](AGENTS.md), [HANDOFF](docs/HANDOFF.md) and the
[architecture](docs/ARCHITECTURE.md) before changing orchestration code.
Keep machine paths, credentials, production settings and logs out of Git.
Use `handoff-local/` for private progress and reproducible local evidence.

Use Python 3.11–3.13, install `requirements-dev.txt`, run `python -m pytest -q`.
Windows packaging additionally runs the C# protocol checks and PyInstaller.
See [RELEASING](docs/RELEASING.md). Linux CI validates portable logic, not a Linux GUI release.

Describe the problem, resulting behavior and relevant checks in each pull request.
Do not stop processes by name; verify ownership. Keep GPU modes, model selection,
agent selection and Windows startup independent. Do not auto-install privileged services.
Documentation translations should describe the same behavior and limitations.
