# Work log

## 2026-09-21 — Virtual Machine (WaveVM) & RDP UI Lazy Loading Optimization, Non-GPU Fast Return, Mutex Path Normalization & Vision Backend Parameters (Release v0.2.0-beta.14)

1. UI Performance & Freezing Fix for Virtual Machines (WaveVM) & Software Rendering:
   - Root cause: `ControlCenter` synchronously constructed all 12 page views with ~400+ Tkinter Canvas widgets during `__init__`. In virtual machines (WaveVM) and RDP sessions without hardware GDI acceleration, rendering 400+ canvas elements synchronously caused huge startup delays, window freezing, and sluggish tab navigation.
   - Solution: Implemented lazy page construction (`self._built_pages = set()`, `_ensure_page_built(name)`). On startup, only the active Station overview page is built. Background managers (`MetricsStore`, `MetricsSampler`, `ScheduleManager`, `StartupRunner`) are initialized cleanly during startup, while UI widget trees for pages (Cluster, Hardware, Logs, Models, Monitoring, Schedules, etc.) are built only when first navigated to.
   - Guarded telemetry and periodic refresh hooks so non-active/unbuilt pages never trigger canvas redraws in the background.

2. Instant Non-GPU Hardware Probe Bypass (`src/gpu_details.py`):
   - Fast-return in `GpuDetailsCache.refresh()` when `find_nvidia_smi()` is `None`. Eliminates blocking subprocess executions on CPU-only machines and virtual machines.

3. Single-Instance Mutex Normalization (`src/instance.py`):
   - Canonicalized directory paths with `Path(directory).resolve()` and `os.path.normcase()` for Win32 named mutex and port hashing, guaranteeing robust single-instance enforcement and reliable window restoration.

4. Vision Model Backend CLI Parameters (`src/model_backend.py`, `src/profiles_schema.py`, `src/validation.py`):
   - Added schema fields `no_mmproj_offload` (bool), `image_min_tokens` (optional int), `image_max_tokens` (optional int) to `ModelProfile`.
   - Updated `model_backend.py` command builder to generate `--no-mmproj-offload`, `--image-min-tokens <N>`, and `--image-max-tokens <N>`.

5. Testing & Verification:
   - Added 4 new regression tests covering vision flags generation, fast-return on non-GPU systems, lazy page creation, and instance path normalization.
   - Full test suite: 291 passed in 11.8s.


1. Extended Context Override for Large Context Windows (512K / 1M):
   - Root cause: GGUF models such as `Qwen3.8-27B-Q6_K_L.gguf` define `qwen35.context_length = 262144` in their header metadata. When llama-server was started with `-c 524288`, llama.cpp silently clamped the context buffer to the GGUF metadata default (`262144`). When Qwen Code Desktop sent requests larger than 256K (e.g. 262,490 tokens), llama-server rejected the request with `400 request (262490 tokens) exceeds the available context size (262144 tokens), try increasing it`.
   - In `src/model_backend.py`, added automated GGUF header inspection via `read_gguf_cached(weights)`. When `model.context > (info.context_length or 0)`, `build_model_entry` automatically passes `--override-kv {info.architecture}.context_length=int:{model.context}` (e.g. `--override-kv qwen35.context_length=int:524288`).
   - Verified with real backend configuration: `qwen3.8-27b-long` now properly allocates the full 524,288 token context window.

2. Robust In-Place OTA Updates for Per-User & System Installs (`src/app_updates.py`):
   - Root cause: In beta 11, `apply_update.ps1` ran the installer silently without `/CURRENTUSER` or `/DIR`. Because Inno Setup was configured with `PrivilegesRequired=admin`, silent execution from a standard user session without elevation failed to install into `Program Files`, resulting in an exit code failure without updating the existing per-user copy in `%LOCALAPPDATA%\Programs\Local Agent AI Station`.
   - In `src/app_updates.py`, added automatic detection of installation scope (`/CURRENTUSER` for user-space installations in `%LOCALAPPDATA%\...` and `/ALLUSERS` for system-wide installations).
   - Explicitly passed `/DIR="{target_dir}"`, `/SP-`, `/SILENT`, `/CLOSEAPPLICATIONS`, and `/NORESTART`.
   - Enhanced relaunch candidates in `apply_update.ps1` to check both target executable path and standard path variants with spaces.

3. Testing & Release:
   - Added automated test `test_build_model_entry_adds_override_kv_for_extended_context` to `tests/test_model_server.py`.
   - All 287 automated tests pass with 0 failures.
   - Built standalone installer `LocalAgentAIStation-0.2.0-beta.13-Setup-x64.exe` (565.1 MB) bundled with full CUDA 13 engine.
   - Published release `v0.2.0-beta.13` to GitHub with installer, source zip, `BUILD.json`, and `SHA256SUMS.txt`.

## 2026-09-21 — Bundle CUDA 13 Runtime DLLs, Fix Qualification Import, Purge Legacy 3.0.0-alpha.1 & Release v0.2.0-beta.12

1. Missing CUDA 13 DLL Bundling for Remote/Worker PCs:
   - Root cause: `ggml-cuda.dll` dynamically links to `cublas64_13.dll`, which in turn links to `cublasLt64_13.dll` and uses `nvcudart_hybrid64.dll`. On remote PCs without CUDA 13 Toolkit installed in PATH, `ggml-cuda.dll` failed `LoadLibrary` (WinError 126), causing llama-server to fall back to CPU inference.
   - Copied `cublas64_13.dll` (50MB), `cublasLt64_13.dll` (477MB), and `nvcudart_hybrid64.dll` (1.1MB) from `C:\Users\iswav\Desktop\CUDA13_DLL` into `D:\AI\QWEN_LOCAL_STACK_2026\llama.cpp`.
   - Included all three libraries in the Inno Setup installer payload (`dist/engine/llama.cpp`). Remote PCs now get full NVIDIA GPU acceleration immediately upon installation without requiring CUDA Toolkit setup.

2. Model Qualification Import Fix (`src/ui/pages/models.py`):
   - Fixed erroneous relative import `from ..qualification import qualify_model` to `from ...qualification import qualify_model` on line 100.
   - "Загрузить и проверить модель" on the Models page now operates without raising `No module named 'src.ui.qualification'`.

3. Termination of Legacy 3.0.0-alpha.1 Update Loop & Updater Relaunch Hardening:
   - Root cause: GitHub repository contained an obsolete pre-release `v3.0.0-alpha.1` (ID 387889436) from Sep 13. SemVer comparison evaluated 3.0.0 > 0.2.0, causing Station to download the obsolete alpha. When applied, the legacy installer did not install into the modern `LocalAgentAIStation` directory, causing the relaunch check to fail. After manual start, the cycle repeated.
   - Deleted release `387889436` and remote tag `v3.0.0-alpha.1` permanently from GitHub via REST API.
   - Hardened `src/app_updates.py`: SemVer comparison now strictly skips any releases with major version >= 3 while on the 0.x line.
   - Overhauled `apply_update.ps1`: added detailed log `%TEMP%\laas_update.log`, accepted exit codes 0 and 6, added fallback to `%LOCALAPPDATA%\Programs\LocalAgentAIStation\LocalAgentAIStation.exe`, and verified process relaunch.

4. Version Bump & Publication:
   - Bumped `VERSION` to `0.2.0-beta.12` (`WINDOWS_VERSION = (0, 2, 0, 12)`).
   - Localized release notes added to `CHANGELOG.md` in English, Russian, and Ukrainian.
   - Built standalone installer `LocalAgentAIStation-0.2.0-beta.12-Setup-x64.exe` (565.1 MB) bundled with full CUDA 13 engine.
   - Published release `v0.2.0-beta.12` to GitHub with installer, source zip, `BUILD.json`, and `SHA256SUMS.txt`.
   - Verified live `app_updates.check()` reports "У вас актуальная версия Station (0.2.0-beta.12)".

## 2026-09-21 — Fix Qwen Code Context Limit Truncation, Silent Agent Sync & Test Environment Sandboxing

1. Root Cause Analysis of Context Compression Error:
   - User encountered `Context is too large to send safely after automatic compression. Estimated prompt tokens: 108066; hard limit: 42536; compression status: COMPRESSION_FAILED_OUTPUT_TRUNCATED` when launching Qwen Code Desktop from desktop shortcut.
   - Identified root cause: `tests/conftest.py` sandboxed `LOCAL_AGENT_STATION_HOME` but omitted `QWEN_HOME`, `HERMES_HOME`, `PI_CODING_AGENT_DIR`, `OPENCLAW_HOME`, and `LOCALAPPDATA`. Every test run in `pytest` triggered agent sync with empty local models and cluster defaults, overwriting `~/.qwen/settings.json` with generic model names (`... @ Primary LLM Node`) and a fallback `contextWindowSize: 65536`.
   - With `contextWindowSize: 65536`, Qwen Code calculates a hard safety limit of `42536` tokens. When the user resumed an existing project session with `108066` tokens, compression failed and prevented continuation.

2. Test Environment Hardening (`tests/conftest.py`):
   - Explicitly sandboxed `QWEN_HOME`, `HERMES_HOME`, `PI_CODING_AGENT_DIR`, `OPENCLAW_HOME`, and `LOCALAPPDATA` inside the temporary test sandbox.
   - Running the test suite now leaves user settings completely untouched.

3. Silent Agent Synchronization (`src/controller.py`, `src/ui/control_center.py`):
   - Added silent synchronization before launching any frontend in `StationController.launch_frontend()` so external agents always get the latest model catalog and active model before start.
   - Added `sync_all_agents_silently()` method to `StationController` and invoked it during application initialization in `_agents_discovered`.
   - Immediately synchronized `~/.qwen/settings.json` with all correct model profiles:
     - `[48GB] Qwen 3.8 27B Q6_K_L [256K] (Production)`: `contextWindowSize: 262144`
     - `[48GB] Qwen 3.8 27B Q6_K_L [512K] (Long Context)`: `contextWindowSize: 524288`
     - `[24GB] Qwen 3.8 27B IQ3_M [256K] (1x GPU TCC)`: `contextWindowSize: 262144`
     - `[48GB] Qwen 3.6 35B-A3B MoE [128K] (Fast, 2x GPU)`: `contextWindowSize: 131072`
     - `[24GB] Qwen 3.6 35B-A3B MoE [64K] (Fast, 1x GPU)`: `contextWindowSize: 65536`

4. Verification:
   - Full test run (`286 passed`) confirmed zero modifications to `~/.qwen/settings.json`.
   - Qwen Code Desktop hard limit with 256K context is >200K tokens, safely supporting 108K resumed prompt histories.

## 2026-09-21 — Compact Icon Agent Controls (▶/⏹/🔧), Direct Launch, Default OTA Auto-Download & Release v0.2.0-beta.11

1. Compact Symbol Controls & Dedicated Settings (`src/ui/agent_controls.py`, `src/ui/control_center.py`):
   - Replaced bulky localized text buttons (`[ Запустить агента ]`, `[ Остановить агента ]`) with compact, clean 44px symbol buttons: Play (`▶`), Stop (`⏹`), and Settings (`🔧`).
   - Standardized layout across both the Dashboard agent control row and the Agents page individual agent cards.
   - Preserved icon text during dynamic state refreshes (`_refresh_agent_launch_states`, `_refresh_dashboard_buttons`).

2. Direct Agent Launch (`src/ui/control_center.py`):
   - Streamlined `_launch_frontend`: pressing `▶` now directly starts the agent executable (`self.controller.launch_frontend()`) without popping up configuration review modals, diff popups, or smoke checks.
   - Dedicated Settings action (`_configure_agent`): pressing `🔧` explicitly opens the configuration review dialog to preview and synchronize model endpoints with external agent settings (e.g. `~/.qwen/settings.json`).

3. Telegram-Style OTA Auto-Download Default & Seamless Update (`src/config.py`, `src/app_updates.py`, `src/ui/control_center.py`):
   - Enabled `app_update_auto_download: True` by default so updates are automatically downloaded in the background when discovered.
   - In `src/ui/control_center.py`, clicking the sidebar badge when update is `READY` immediately triggers `apply_update(silent=True)` and exits Station after 300ms for seamless 1-click update without opening the full dialog.
   - Added filter in `src/app_updates.py` to ignore legacy pre-release tags (`3.0.0-alpha.1`), ensuring SemVer comparison always targets `0.2.0-beta.*` releases.

4. Version Bump & GitHub Publication:
   - Bumped `VERSION` to `0.2.0-beta.11` (`WINDOWS_VERSION = (0, 2, 0, 11)`).
   - Built Inno Setup installer `LocalAgentAIStation-0.2.0-beta.11-Setup-x64.exe` (182.7 MB) bundled with the full LLM engine from `D:\AI\QWEN_LOCAL_STACK_2026`.
   - Published GitHub Release `v0.2.0-beta.11` on `Wave-is/laas` with installer, source archive, `BUILD.json`, and `SHA256SUMS.txt`.

5. Testing & Verification:
   - Added `test_check_ignores_legacy_3_0_0_alpha` to `tests/test_app_updates.py`.
   - All 286 automated tests pass with 0 failures.

## 2026-09-20 — Telegram-style Over-The-Air (OTA) Updates Engine & UI Integration

1. OTA Update Engine (`src/app_updates.py`):
   - Implemented `UpdateManager` (singleton `update_manager`) and `UpdateState` state machine (`IDLE`, `CHECKING`, `AVAILABLE`, `DOWNLOADING`, `READY`, `INSTALLING`, `ERROR`).
   - GitHub Releases API integration querying latest release for `LocalAgentAIStation-*-Setup-x64.exe` and `SHA256SUMS.txt`.
   - Streaming HTTP chunked download with live progress calculation and cancellation support (`cancel_download()`).
   - Checksum verification: downloads `SHA256SUMS.txt`, computes SHA256 of the downloaded `.exe.part`, checks match before promoting to `.exe`.
   - Detached 1-click updater (`apply_update(silent=True)`): writes a detached PowerShell runner script (`apply_update.ps1`) that waits for Station's PID to terminate, executes the installer with `/SILENT /CLOSEAPPLICATIONS /NORESTART`, relaunches `LocalAgentAIStation.exe`, and cleans up temp files.

2. Telegram-Style UI Sidebar Integration (`src/ui/control_center.py`):
   - Added dynamic update button/badge (`self.sidebar_update_btn`) packed directly above `self.sidebar_version_label` at the bottom of the sidebar.
   - Real-time reactivity via `update_manager.subscribe`:
     - `AVAILABLE`: `[ 📥 Обновить до v... ]` (accent pill)
     - `DOWNLOADING`: `[ ⏳ Загрузка 45% ]` (blue progress pill)
     - `READY`: `[ 🚀 Перезапустить: v... ]` (green restart pill)
     - `INSTALLING`: `[ ⏳ Установка… ]` (amber installing pill)
     - `IDLE`: hidden (`pack_forget`) with zero extra blank space.
   - Non-blocking delayed check 15s after startup and periodic hourly check in background polling thread.
   - Clean unsubscribe on application shutdown.

3. Maintenance Page Enhancements (`src/ui/pages/maintenance.py`):
   - Replaced static check section with full OTA dashboard: status label, progress bar, action buttons (Check, Download, Restart & Apply, Cancel, Releases), and switches for `app_update_check` and `app_update_auto_download`.

4. Telegram-style Update Dialog (`src/ui/update_dialog.py`):
   - Modal window showing version, release date, download size, scrollable release notes (changelog), live download progress bar, and action buttons.
   - Developer mode warning for Git checkouts (`sys.frozen == False`) recommending `git pull` or manual install.

5. Configuration & Strict i18n (`src/config.py`, `locales/`):
   - Added configuration keys `app_update_check: True`, `app_update_auto_download: False`, `app_update_interval_hours: 4`.
   - Full English and Ukrainian translation catalogs in `locales/en/app_updates.json` and `locales/uk/app_updates.json`.
   - Zero untranslated or raw Cyrillic strings in source files.

6. Automated Tests & Verification:
   - Created `tests/test_app_updates.py` with 7 comprehensive unit tests (semver comparison, update checking, chunked download, SHA256 match, SHA256 mismatch error handling, detached update script generation, and UI state transitions).
   - All 4 i18n tests pass 100% (`pytest tests/test_i18n.py`).
   - All 285 tests across the entire test suite pass with zero failures.

## 2026-09-20 — Review Dialog Label Shadowing Fix, Qwen Smoke Auth Flags & Settings Sync

1. Bug Fix in Review Dialog (`src/ui/control_center.py`):
   - Found root cause of `unsupported operand type(s) for +: 'CTkLabel' and 'str'`: in `review()`, the local header widget `label = ctk.CTkLabel(...)` shadowed the method argument `label=None`. When clicking «Применить показанные изменения», `label or title` passed the `CTkLabel` instance to `self.worker(apply, callback, label=...)`, which crashed during `(label + '…')`.
   - Renamed header widget to `title_label` in `review()`.
   - Hardened `worker()` to safely coerce `label` via `label_text = str(label) if label else ''`.

2. Bug Fix in Qwen Adapter Smoke Test (`src/agents/qwen_code/adapter.py`):
   - Added automatic directory creation `Path(workspace).mkdir(parents=True, exist_ok=True)` to prevent `[WinError 267]` (`NotADirectoryError`).
   - In `smoke()`, when `configuration_path` is passed, explicitly included `--auth-type openai --model {model.backend_model_id} --openai-base-url {model.endpoint}` so Qwen Code CLI does not fall back to Aliyun/DashScope with 401 Unauthorized.
   - Verified live smoke check with `Qwen3.8-27B-IQ3_M.gguf`: returns `True` and `"Проверка Qwen Code через модель пройдена"`.

3. Qwen Code Desktop Configuration Sync:
   - Synchronized `~/.qwen/settings.json` with active model `[24GB] Qwen 3.8 27B IQ3_M [256K] (1x GPU TCC)` (`id: qwen3.8-27b-single-tcc`, endpoint `http://127.0.0.1:9292/v1`).
   - Verification confirms `preview.status == 'IN SYNC'` and `model_binding_state == 'READY'`. Clicking «Запустить агента» no longer requires or displays the review dialog.

4. Tests:
   - Added automated tests in `tests/test_regressions_v3.py` verifying Qwen smoke argument flags and confirming `review()` does not shadow `label`.
   - 77 core tests pass 100%.

## 2026-09-20 — Model catalog standardization by [VRAM] prefix, 256K 1x A5000 TCC & dual Qwen 3.6 MoE

1. VRAM Prefix Standardization & Catalog Clean-up:
   - Established strict `[VRAM]` naming prefix: `[16GB]`, `[24GB]`, `[48GB]` across all models so required hardware is immediately obvious in any UI.
   - Reduced catalog to 6 canonical models, removing deprecated/experimental profiles (`qwen3.8-27b-q5-single-tcc`, `qwen38-iq3-fast-196k-exp`, `qwen3.8-27b-ultra-1m-exp`, `qwen38-quality-q8-rollback`, `qwen38-fallback-128k`).
   - Standardized profiles:
     - `[48GB] Qwen 3.8 27B Q6_K_L [256K] (Production)` (2x A5000 NVLink, KV `q8_0`, MTP3)
     - `[48GB] Qwen 3.8 27B Q6_K_L [512K] (Long Context)` (2x A5000 NVLink, KV `q4_0`, MTP3)
     - `[48GB] Qwen 3.6 35B-A3B MoE [128K] (Fast, 2x GPU)` (2x A5000 NVLink)
     - `[24GB] Qwen 3.8 27B IQ3_M [256K] (1x GPU TCC)` (GPU 1 TCC, full 256K context)
     - `[24GB] Qwen 3.6 35B-A3B MoE [64K] (Fast, 1x GPU)` (GPU 1 TCC, fast MoE)
     - `[16GB] Qwen 3.8 27B @ Friend A4000 [64K]` (Remote cluster node)

2. 256K Context on 1x A5000 (GPU 1 TCC):
   - Configured `qwen3.8-27b-single-tcc` with `Qwen3.8-27B-IQ3_M.gguf` (12.95 GiB) + `mmproj` (0.87 GiB) + 256K context (`q4_0` KV cache, 4.50 GiB) + CUDA overhead (1.25 GiB) = ~19.57 GiB total VRAM.
   - Leaves >4.4 GiB free VRAM headroom on the 24GB card, ensuring 100% stability without risk of OOM under heavy load.
   - Pinned strictly to `CUDA_VISIBLE_DEVICES=GPU-00641cc0-95f0-9f2f-22d3-bf7412c9e483`.

3. Dual Qwen 3.6 35B MoE Profiles:
   - Configured 2x GPU profile `qwen36-a3b-q6-vision-128k` (128K context, full 48GB NVLink allocation).
   - Added 1x GPU profile `qwen36-fast-single-gpu` (64K context, GPU 1 TCC, `cpu_offload: true`).

4. Verification:
   - 55 test suite regression, topology and i18n tests pass 100%.
   - `CompatibilityEvaluator` verified `can_run=True` and `status=COMPATIBLE` for all models.
   - Station compiled `llama-swap.yaml` with zero skipped models.
   - Qwen Code Desktop synchronized (`settings.json`) with active model `qwen3.8-27b-single-tcc`.
   - GPU 0 confirmed in WDDM (games), GPU 1 confirmed in TCC (compute).

## 2026-09-19 — single RTX A5000 TCC model Qwen 3.8 27B Q5_K_L with game isolation on GPU 0

1. VRAM Modeling & Quantization Selection:
   - Evaluated all Qwen 3.8 27B quantizations on physical 24GB RTX A5000 hardware:
     - Q8_0 (27.1 GiB) and Q6_K_L (22.4 GiB + 1.2 GiB buffers) exceed 24 GiB VRAM on a single GPU.
     - IQ3_M (12.9 GiB) underutilizes the card (~65% VRAM) and loses quality on complex code.
     - `Qwen3.8-27B-Q5_K_L.gguf` (20.06 GiB weights + 888 MiB mmproj + 1.2 GiB CUDA overhead) with 64K context (`q4_0` KV) or 32K context (`q8_0` KV) utilizes ~23.8 GiB out of 24.0 GiB (97.1% VRAM), providing maximum possible FP16 benchmark accuracy on a single 24GB card.
   - Retained full capabilities: Vision (`mmproj-Qwen3.8-27B-bf16.gguf`), speculative decoding (`mtp_depth: 3`), and agent tool calling.

2. Hardware Isolation for Gaming:
   - Applied GPU profile `gpu-first-wddm-rest-tcc` via running service `LocalAgentGpuModeHelper`:
     - GPU 0 (`GPU-0c498e10-c0b3-1e34-eb47-583bf465f6c2`): stays in WDDM mode for user gaming (*Civilization VI* remained running without interruption).
     - GPU 1 (`GPU-00641cc0-95f0-9f2f-22d3-bf7412c9e483`): successfully switched to TCC mode (0% WDDM overhead, full 24576 MiB available for CUDA).
   - Enforced hard GPU pinning: `CUDA_VISIBLE_DEVICES=GPU-00641cc0-95f0-9f2f-22d3-bf7412c9e483` in both Station `llama-swap.yaml` and standalone `llama-swap.json`. GPU 0 is physically invisible to the LLM engine.

3. Station & Agent Configuration:
   - Registered model profile `qwen3.8-27b-q5-single-tcc` in `C:\Users\iswav\AppData\Local\LocalAgentAIStation\config\model_profiles.yaml`.
   - Updated `D:\AI\QWEN_LOCAL_STACK_2026\configs\llama-swap.json` with matching model entry.
   - Added `game-ai` station preset («Игры + ИИ») in `src/profile_storage.py` and translations in `locales/en/tray_gpu.json` and `locales/uk/tray_gpu.json`.
   - Extended `src/compatibility.py` to classify first NVIDIA card as graphics when `first_wddm_rest_tcc` is active.
   - Synchronized `~/.qwen/settings.json` via `QwenCodeAdapter`: registered `qwen3.8-27b-q5-single-tcc` in `modelProviders.local-agent-station` and bound as active model (`model.name = qwen3.8-27b-q5-single-tcc`).
   - Dispatched knowledge to other installed agents (Hermes) via `KnowledgeDistributor`.

4. Verification:
   - 83 unit/regression tests passed; 4 i18n tests passed (100% translation coverage across EN/RU/UK).
   - `nvidia-smi` confirmed GPU 0 in WDDM (games) and GPU 1 in TCC (compute).
   - Station compiled backend `llama-swap.yaml` verified with exact GPU 1 UUID pinning and model parameters.

## 2026-09-19 — installer auto-close running processes, unchecked gpuhelper, safe service update

1. Running Process Handling & DeleteFile Code 5 Elimination (`installer/Station.iss`, `src/setup_guard.py`):
   - In `src/setup_guard.py`: Created dual Win32 named mutexes (`Global\LocalAgentAIStation.SetupGuard` and `Local\LocalAgentAIStation.SetupGuard`), allowing elevated installer instances (UAC) to see user-session running processes across session boundaries.
   - In `installer/Station.iss`: Configured `AppMutex` with both Global and Local names; enabled `CloseApplications=yes` to leverage Windows Restart Manager.
   - Added `PrepareToInstall` hook that gracefully requests running Station instances to close (`taskkill /IM LocalAgentAIStation.exe`), waits for clean termination, and issues forced termination (`taskkill /F /IM LocalAgentAIStation.exe /IM llama-server.exe /IM llama-swap.exe`) to ensure zero locked files in the installation directory prior to extraction.

2. GPU Helper Service Preservation on Updates (`installer/Station.iss`, `src/services/install_helper.ps1`):
   - In `installer/Station.iss`: Marked `gpuhelper` task with `Flags: unchecked` so it is not selected by default on fresh installs or updates.
   - Added detection of already installed `LocalAgentGpuModeHelper` service (`GpuServiceExisted`). If the service is present:
     - Stopped via `sc stop LocalAgentGpuModeHelper` before file replacement.
     - Safely restarted via `sc start LocalAgentGpuModeHelper` in `CurStepChanged(ssPostInstall)`.
     - Completely skips running `install_helper.ps1`, eliminating destructive service deletion (`sc delete`) and 10-second polling delays.
   - In `src/services/install_helper.ps1`: Changed update logic to modify `binPath=` in-place and restart rather than deleting and recreating.

3. Testing, Build & Release:
   - All 276 unit/integration tests passed.
   - Built full release installer `LocalAgentAIStation-0.2.0-beta.9-Setup-x64.exe` (182.7 MB).
   - Re-published release assets to GitHub tag `v0.2.0-beta.9` and updated `SHA256SUMS.txt`.

## 2026-09-19 — cluster refresh overhaul for slow PCs

1. Decoupled UI from Local Telemetry Ticks (`src/ui/pages/cluster.py`):
   - Removed `self.telemetry_hooks.append(self._refresh_cluster_ui)` which caused unconditional ~1-2s GUI re-rendering triggered by local GPU telemetry.
   - Cluster tab now manages its own update cycle independently from local hardware polling.

2. In-Place Widget Updates (`src/ui/pages/cluster.py`):
   - Eliminated the destructive `for child in self.cluster_nodes_container.winfo_children(): child.destroy()` pattern on metric ticks.
   - Separated rendering into structural creation (`_build_node_card`) and zero-destruction in-place updates (`_update_node_card_inplace`) caching references in `self._node_card_widgets`.
   - Structural rebuilds only execute when the node set changes (add, remove, XML import).
   - Solved UI flickering, scroll position jumping, and lost mouse clicks completely.

3. User-Controlled Refresh Modes & Header Controls (`src/config.py`, `src/ui/pages/cluster.py`):
   - Added `'cluster_refresh_mode'` setting in `DEFAULT_SETTINGS` (default: `'manual'`).
   - Added mode selector dropdown: `[Вручную]`, `[15 сек]`, `[30 сек]`, `[60 сек]`.
   - Added manual refresh button `[⟳ Обновить]` that changes state to `[⏳ Опрос...]` during HTTP querying.
   - Added timestamp indicator showing the time of the last successful refresh.
   - Automatic intervals only tick when the active page is `'cluster'` (`self.current_page_name == 'cluster'`).

4. Adaptive Background Poller & Non-Blocking Sampling (`src/cluster_manager.py`):
   - Added `sample_async(callback=None)` executing sampling on a daemon thread and dispatching results via thread-safe callbacks.
   - Background `ClusterSampler` sleeps when mode is `'manual'`, preventing background HTTP request storm.
   - Reduced unreachable node join timeout to 2.0s to avoid UI or thread stalls.

5. Localization & Tests:
   - Added EN/UK translations for all new UI labels and messages in `locales/en/cluster.json` and `locales/uk/cluster.json`.
   - Added unit tests in `tests/test_cluster.py` covering config, `sample_async`, i18n keys, and UI methods.
   - All 276 tests pass with 100% success.


1. Win32 Named Mutex Single Instance (`src/instance.py`, `main.pyw`):
   - Added kernel-level Win32 Named Mutex (`CreateMutexW`, `ERROR_ALREADY_EXISTS 183`) preventing race conditions on rapid double-clicks.
   - Secondary instance immediately signals running instance via loopback socket to bring window to front and exits `0` cleanly.
   - Started listener loop immediately on acquire with queued show events, eliminating the 2-second startup window race.
2. Dynamic GPU Adaptation & Tray Defaults (`src/config.py`, `src/ui/tray_controls.py`, `src/ui/pages/model_dialogs.py`):
   - Changed default `tray_style` in `DEFAULT_SETTINGS` from `two_icons` to `dual_tile` (two metrics in one tray icon).
   - Clamped tray icons to 1 whenever detected NVIDIA/discrete GPU count is < 2, eliminating unwanted second dummy tray icon on single-GPU machines.
   - Updated model dialog GPU selector to only present multi-GPU options when 2+ GPUs are detected.
3. Cluster Page UI Overlap Fix & UDP Auto-Discovery (`src/cluster_discovery.py`, `src/ui/pages/cluster.py`):
   - Restructured summary card into two clean rows: Title/Subtitle row on top (with Refresh button), and dedicated Action Toolbar underneath, completely preventing button overlap at any screen width.
   - Implemented `ClusterDiscovery` (`src/cluster_discovery.py`): UDP broadcast beacon on port 47150 announcing hostname, IP, port, endpoints, and GPU specs; background listener with automatic TTL expiry.
   - Added «🔍 Автопоиск в сети» button and `ClusterDiscoveryDialog` on Cluster page for 1-click addition of discovered LAN nodes.
4. Overheat Alert Threshold 90°C (`src/ui/pages/monitoring.py`, `src/metrics_history.py`, `src/config.py`):
   - Changed `DEFAULT_THRESHOLD = 90` (was 85) in `monitoring.py` and `DEFAULT_SETTINGS['monitoring_alert_threshold_c'] = 90`.
5. Default Models Directory `D:\LLM` (`src/model_server.py`, `src/hf_download.py`):
   - `model_server.models_dir()` now defaults to `D:\LLM` if drive D: exists, otherwise `C:\LLM`.
   - `safe_target` in `hf_download.py` automatically creates target directory on download.
6. Agent Frontend Auto-Selection (`src/ui/agent_controls.py`, `src/ui/control_center.py`):
   - When preferred frontend (`qwen-desktop`) is not installed, automatically selects the first *installed* frontend (`qwen-terminal`), enabling the «Запустить агента» button immediately.
7. Sidebar & Dashboard Layout Polish (`src/ui/control_center.py`):
   - Compacted left sidebar navigation: reduced button height from 42 to 33, padding to 1, brand and footer vertical margins tightened, fitting all 10 page buttons on 768p displays without cutoff.
   - Balanced dashboard padding (`pady=(5, 6)`) for cards, combos, and status boxes, providing breathing room while keeping the entire station page within window height.
8. ComfyUI Cluster Sync & Local vs Remote UX (`src/cluster_manager.py`):
   - `import_nodes_xml` automatically updates `SharedServices` active ComfyUI service profile with remote node's URL (e.g. `http://192.168.x.x:8188`) instead of keeping disconnected `127.0.0.1:8188`, and deploys the agent skill.
9. Configurable Logging Directory (`src/paths.py`, `src/config.py`, `main.pyw`, `src/ui/control_center.py`):
   - Added `'logs_dir'` setting in `config.py`.
   - Added `logs_dir()` in `paths.py` with fallback to default `%LOCALAPPDATA%/LocalAgentAIStation/logs` if custom/network path is unreachable.
   - Added «Папка журналов (логов)» field in Settings supporting UNC network shares (`\\server\share\laas-logs`), and «Открыть папку логов» button.
10. Testing & Verification:
    - Added unit tests: `tests/test_instance.py`, `tests/test_cluster_discovery.py`, `tests/test_secondary_pc_fixes.py`.
    - Added full EN/UK translations in `locales/{en,uk}/cluster.json` and `locales/{en,uk}/control_center.json`.
    - All 272 automated pytest tests pass with zero failures.
 
1. Remote Node Models Discovery:
   - Created `src/node_models.py` (`RemoteModel`, `query_node_models`, `discover_cluster_models`) to discover available models from cluster LLM nodes over OpenAI-compatible `/v1/models` endpoints.
   - Non-LLM nodes (ComfyUI) and disabled nodes are automatically filtered out.
   - Safe HTTP request handling with 3s timeout and robust JSON parsing.
2. Agent Adapters & Controller Integration:
   - Extended `QwenCodeAdapter.configure_model_provider` to accept `cluster_models` and register them into `modelProviders.local-agent-station` with remote `baseUrl`, deduplicating entries.
   - Updated `AgentRuntimeAdapter` (`src/agents/base.py`), Hermes, OpenClaw, and Pi adapters to accept `cluster_models=None`.
   - Updated `StationController.preview_sync` to query `discover_cluster_models(cluster_manager)` and forward remote models to the adapter.
3. Knowledge Distribution & Auto-Sync:
   - Expanded `src/skill_distributor.py` (`KnowledgeDistributor`) with `sync_models_to_agents()` method to automatically sync models to all discovered agents without requiring GUI interaction.
   - Wired auto-sync into `ClusterManager` (`add_node`, `update_node`, `import_nodes_xml`) for LLM node changes.
   - Overhauled the «📢 Рассказать агентам» button on `ClusterPage` (`src/ui/pages/cluster.py`) to deploy ComfyUI skills AND synchronize model catalogs with agents, reporting combined status to user.
   - Added full translations in `locales/en/cluster.json` and `locales/uk/cluster.json`.
4. Verification & Testing:
   - Created `tests/test_node_models.py` (5 tests covering endpoint queries, format variations, error resilience, node filtering, adapter registration).
   - Verified live synchronization against active cluster nodes: 7 models discovered on cluster nodes and successfully written into `~/.qwen/settings.json`.
   - All 263 pytest tests pass with zero failures.

## 2026-09-17 — dashboard layout polish: block reordering & vertical compaction

1. Dashboard Block Reordering:
   - In `src/ui/control_center.py` (`_build_station`), moved «Оборудование сейчас» (GPU tiles) directly under the top stats tiles, and placed «Модель и агент» below it per user request.
2. Vertical Compacting & Scrollbar Elimination:
   - Applied `height=0` to single-line labels (`heading`, `status_label`, `card` headers, `stats` tiles, GPU tile rows, server status label, and prompt label) to override CustomTkinter's default 28px/42px label height constraint.
   - Reduced button/combo row vertical padding from 14px to `(4, 5)`, eliminating the huge 28px gaps between consecutive rows.
   - Reduced GPU tile and card header margins (`pady`), saving ~168px vertically at 150% DPI scaling (content height reduced from 867px to 699px).
   - Dynamic auto-hiding scrollbar now remains completely unmapped/hidden at standard window resolutions without awkward vertical scrolling.
3. Verification:
   - Verified programmatically that `station_page._scrollbar.winfo_ismapped() == False` with simulated 3-device topology.
   - All 258 pytest tests pass with zero regressions.

1. Installer Language Prompt:
   - Configured `installer/Station.iss` with `ShowLanguageDialog=yes` and `UsePreviousLanguage=no` so that
     the three-language selection dialog (English, Russian, Ukrainian) is always presented to the user
     upon every installer launch, including upgrades.
2. UI Polish & Stubs Cleanup:
   - Dynamic auto-hiding scrollbar across all pages in `src/ui/control_center.py` with slim transparent styling.
   - Cleaned up obsolete "section in development" entries from locales.
3. Release Packaging, Public GitHub Repository & Latest Release:
   - Bumped version to `0.2.0-beta.2` (`WINDOWS_VERSION = (0, 2, 0, 2)`).
   - Rebuilt full release installer with bundled engine.
   - Switched GitHub repository visibility from private to public per user request.
   - Published release `v0.2.0-beta.2` as the public Latest Release on GitHub with all assets.

## 2026-09-17 — release 0.2.0-beta.1: schedules, model dialog fixes, Inno Setup 6.7.3

1. Schedules & Idle Unload:
   - Added `ScheduleManager` in `src/schedules.py` supporting time-of-day execution, day-of-week recurrence
     (every day, weekdays, weekends), actions (`gpu_profile`, `model_profile`, `unload_model`, `stop_backend`),
     and an idle auto-unload timer.
   - Built full `SchedulesPage` UI with task listing, delete/edit controls, and `AddTaskDialog`.
   - Added complete translations: `locales/en/schedules.json` and `locales/uk/schedules.json`.
   - Created automated tests in `tests/test_schedules.py` (4 tests covering engine and auto-unload).
2. Bugfixes & Model Dialogs:
   - Fixed `ScanDialog` and `HfDialog` constructor signature mismatch (`on_saved` vs `on_add`), and
     `MoveDialog` (`on_done` default value). Added signature regression tests in `tests/test_model_features.py`.
3. Installer & Packaging:
   - Installed Inno Setup 6.7.3 and updated `build_installer.ps1` with fallback discovery under `%LOCALAPPDATA%\Programs\Inno Setup 6\ISCC.exe`.
   - Updated `src/version.py` to `VERSION = '0.2.0-beta.1'` (`WINDOWS_VERSION = (0, 2, 0, 1)`).
   - Documented comprehensive release notes in EN, RU, UK in `CHANGELOG.md`.
4. UI Polish & Quality:
   - Implemented dynamic auto-hiding scrollbars for all pages (`CTkScrollableFrame` in `control_center.py`):
     when page content fits the window, scrollbar is completely removed; when content overflows, a slim
     8px semi-transparent thumb appears. Eliminates persistent gray scrollbar ribbon on dashboard.
   - Removed obsolete "section in development" catalog entries from locales.
5. Verification:
   - All 245 pytest tests pass in 5.3s with zero failures.
   - 10 compiled C# helper protocol checks pass cleanly.
   - Live hardware telemetry and model server (PID 29208) verified intact.

## 2026-09-17 — roadmap integration: watchdog, monitoring, logs, models, maintenance, GPU details

Completed integration of the modular architecture and new functionality:
1. Reliability: ModelServerWatchdog daemon (crash/hang detection, backoff, JSONL history,
   deliberate stop suppression in UI/tray), MetricsStore (SQLite) + MetricsSampler with overheat
   hysteresis alerting, pure-Tk canvas charts (temp, load, VRAM, power, tokens/sec), diagnostics
   log tail reader with credential redaction and ZIP bundle export, LogsPage with error filter/search.
2. Models: GGUF header reader with KV-cache VRAM estimator, HuggingFace downloader with resume
   and SHA-256 validation, model library backend (scan, mmproj auto-pairing, move folder),
   complete dialog suite (ModelDialog, ScanDialog, MoveDialog, ChatDialog, HfDialog) wired into ModelsPage.
3. Maintenance: engine_updates (llama.cpp/llama-swap version detection, install, rollback),
   app_updates (Station GitHub release checker with SemVer compare), backup/restore manager with
   secret redaction and pre-import safety backups, interactive MaintenancePage.
4. Hardware: GpuDetailsCache for background queries of power draw, fans, PCIe link, throttle reasons,
   and NVLink bandwidth, integrated into HardwarePage.
5. Localization: complete English and Ukrainian translation catalogs for all new modules (240 tests pass,
   including 100% i18n coverage without missing keys or placeholder mismatches).
Validation: 240 pytest tests passed in 5.5s with zero failures; clean UI import verification.

## 2026-09-17 — clarity, single model server, folders, Program Files installer

Audit found: Services/tray "start server" used the legacy user llama-swap JSON while model
load used the generated YAML; autostart of Qwen Code Desktop always failed because a hand-
edited ~/.qwen/settings.json made the whole managed block differ; an unknown field in
model_profiles.yaml crashed Station at import (2026-09-16); 0.0.0.0 bind was hard-coded;
no address/PID/owner was shown anywhere; ~100 English messages leaked into the UI.
Fixed all of the above (see CHANGELOG 3.0.0-alpha.2). New module src/model_server.py.
Leftovers (old Hermes copy, .station-audit, runtime/, logs) moved to ../_archive/2026-09-17.
Validation: 187 pytest passed; source UI rendered and screenshotted against a copy of the
real configuration; live backend_info against the running server reported 0.0.0.0:9292.


## 2026-09-13 — first installer release preparation

Added a shared version source, Windows file metadata and an application lifetime mutex
for Setup. Added per-user Inno Setup packaging, standard shortcuts/uninstall, ownership-
checked startup cleanup, three-language release presentation and reproducible release inputs.
Private pre-installer progress is retained in ignored local handoff notes.

Validation baseline: 186 Python tests and 10 compiled helper checks before this change.
Installer installation/publishing work is still active; results must be recorded only
once completed. Detailed machine actions are in handoff-local/INSTALLER_RELEASE_PROGRESS.md.

## 2026-09-13 — installer lifecycle validated

186 Python tests and 10 compiled helper checks passed with the installer build.
Actual install, EN/RU/UK repair, installed diagnostics, active-GUI update refusal,
normal exit, uninstall, owned Startup cleanup and data preservation passed locally.
The first CI revealed Windows npm shims being treated as native POSIX executables;
fixed suffix handling, then all four Python matrix jobs passed. Packaging CI and
final publication are still in progress. Private raw reports remain in runtime.

## 2026-09-13 — alpha.1 published and installed

Source ba85033: local 186+10 checks and full installer lifecycle passed. All five CI
jobs passed, including hosted Windows installer tests. A same-version repair was
tested; future cross-version upgrades remain release-specific validation. Standard
per-user installation, existing data preservation, shortcuts and installed GUI were
verified. Old launcher/build/startup cleanup completed on the development machine.
Private prerelease v3.0.0-alpha.1 published with five assets, downloaded hash checks
passed. No repository visibility change. Application UI remains Russian; presentation
and Setup are translated. Broader GPU-helper/physical-machine/agent qualification
remains on the roadmap. Local raw evidence and machine details are ignored by Git.
