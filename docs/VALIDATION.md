# Validation record

## Baseline before the first installer

The internal 3.0 preview passed **186 Python tests and 10 compiled C# helper protocol
checks** on Windows. Tests cover configuration transactions, process ownership,
agent adapters, startup sequencing/cancellation, service health handling, GPU mode
confirmation and simulated 0/1/2/3/4/8-GPU topologies.

Physical development checks used one Windows PC with two NVIDIA RTX A5000 cards
and an Intel display adapter. CPU inference, one selected GPU and two GPU inference,
model load/unload and backend shutdown were exercised. Qwen Desktop opened and
completed a local dialog; Pi and OpenClaw performed a local file-tool task; OpenClaw
Gateway health and owned start/stop were checked. These are historical integration
results, not a promise for every upstream agent version.

Agent-card start/stop, startup save/reload, one-time execution and cancellation were
checked with a real local fixture process. Windows shortcut creation/read/removal was
checked through COM in an isolated folder. Real Qwen Desktop was launched and stopped
from the packaged application's card. Services were exercised against local HTTP fixtures.

## Installer release

The release process must record actual installation/reinstallation/uninstallation,
data preservation, shortcut targets, startup cleanup and running-app protection.
See the release notes and BUILD.json for the published artifact's evidence and hashes.
Local raw evidence is intentionally excluded from Git because it contains machine data.
CI runs Windows/Linux logic tests and a Windows installer build.

## Not yet established

- Installed new privileged GPU-helper end-to-end acceptance and live mode switching.
- A separate clean Windows machine and physical systems with other GPU counts.
- Windows logout/login with a full model + services + agents startup sequence.
- Prolonged agent sessions, largest model contexts, every native tray interaction.
- Agent-side image-generation tool binding to a remote worker.
- Full English/Ukrainian application UI (Setup and user-facing release docs are translated).

The release is an early preview. Do not equate simulated topology coverage with
physical qualification, or compiled protocol checks with installed-service validation.
