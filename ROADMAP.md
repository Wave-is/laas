# LAAS roadmap / Дорожная карта

Local Agent AI Station (LAAS) follows `0.MINOR.PATCH-beta.N` until a stable 1.0.0.
Each version ships only when tests pass, the UI is translated (EN/RU/UK) and the
installer is rebuilt. Dates are not promised.

Разработка идёт по версиям. Номер следующей беты растёт, когда закрыт её блок.

## 0.1.0-beta.1 — released locally (2026-09-17)
- One model server, visible address/PID/owner, optional LAN access.
- Engine and GPU helper service installed by Setup; UAC fallback for GPU switching.
- EN/RU/UK interface, Startup page, tray short name LAAS.

## 0.2.0-beta — Reliability / Надёжность
- **Watchdog**: restart a crashed llama-swap with the same model, tray notification,
  restart limits, event history. Never restarts a server that Station does not own.
- **Monitoring**: sampled history (hour/day) of GPU temperature, load, VRAM, power;
  model server requests and generation speed; overheating alerts with thresholds.
- **Logs**: in-app viewer for model server, agents and Station logs with an error filter;
  "Collect diagnostics" zip (settings without secrets, logs, doctor report).

## 0.3.0-beta — Models / Модели
- "Add model" form without YAML: pick .gguf, find mmproj, context, KV cache, GPU count.
- Scan the models folder for files without a profile.
- VRAM fit estimate before loading (GGUF metadata + KV cache).
- Move the models folder with automatic profile path updates.
- Quick chat window to ask the loaded model a question.
- Hugging Face downloads with resume and checksum.

## 0.4.0-beta — Maintenance / Обслуживание
- Engine updates: show llama.cpp / llama-swap versions, check releases, install into a
  user-writable versioned folder, switch back (rollback).
- Station update check via GitHub releases (once the repository is public).
- Backup and transfer: export/import settings, model profiles and agent configurations.
- First-run wizard: GPUs, CUDA, engine, models, agents — what is missing and where to get it.

## 0.5.0-beta — Hardware & automation / Оборудование и автоматизация
- Hardware page: power, fans, PCIe link, NVLink, per-card details.
- Schedules: GPU mode switching at set times; optional idle unload (off by default —
  the model server is used over the network).

## Principles
- Never unload or restart a model server that is in use without explicit confirmation.
- No hidden network calls: update checks and downloads run only when enabled or requested.
- Every new screen and message is translated and covered by tests.
