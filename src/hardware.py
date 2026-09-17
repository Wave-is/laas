"""Read-only hardware telemetry. Unknown values stay unknown; no simulated fallback."""
import csv
import ctypes
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time
from typing import Any
from .i18n import tr

NVML_TEMPERATURE_GPU = 0
NVML_DRIVER_TCC = 1
class nvmlMemory_t(ctypes.Structure):
    _fields_ = [('total', ctypes.c_ulonglong), ('free', ctypes.c_ulonglong), ('used', ctypes.c_ulonglong)]
class nvmlUtilization_t(ctypes.Structure):
    _fields_ = [('gpu', ctypes.c_uint), ('memory', ctypes.c_uint)]

def hidden_options():
    return {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}

def find_nvidia_smi():
    if os.name == 'nt':
        p = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32/nvidia-smi.exe'
        if p.is_file():
            return str(p)
    return shutil.which('nvidia-smi')

def run_smi(*args, timeout=5):
    executable = find_nvidia_smi()
    if not executable:
        raise FileNotFoundError(tr('nvidia-smi не установлен'))
    result = subprocess.run([executable, *args], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=timeout, **hidden_options())
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout

def number(value):
    try:
        val = float(value)
        return int(val) if val.is_integer() else val
    except (ValueError, TypeError):
        return None

def parse_smi_csv(text):
    result = []
    for p in csv.reader(text.splitlines(), skipinitialspace=True):
        if len(p) != 13 or not p[0].strip().isdigit() or not p[1].startswith('GPU-'):
            continue
        mode, pending = p[4].strip().upper(), p[5].strip().upper()
        total, used, free = map(number, p[8:11])
        result.append(dict(index=int(p[0]), uuid=p[1].strip(), pci_bus_id=p[2].strip(), name=p[3],
            vendor='NVIDIA', driver_mode=mode if mode in ('TCC', 'WDDM') else 'UNKNOWN',
            pending_driver_mode=pending, is_tcc=mode == 'TCC',
            tcc_supported=True if mode == 'TCC' else None,
            display_active={'enabled': True, 'disabled': False}.get(p[11].strip().lower()),
            temp_c=number(p[6]), load_percent=number(p[7]), mem_load_percent=None,
            vram_total_mib=total, vram_used_mib=used, vram_free_mib=free,
            vram_used_pct=round(used / total * 100, 1) if total and used is not None else None,
            nvlink_active=None, nvlink_links=None, driver_version=p[12].strip()))
    return result

class HardwareEngine:
    def __init__(self):
        self._lock = threading.RLock()
        self._cache = []
        self._cache_at = 0
        self.last_error = None
        self._init_nvml()

    def _init_nvml(self):
        self.nvml = None
        self.is_available = False
        try:
            lib = ctypes.CDLL('nvml.dll' if os.name == 'nt' else 'libnvidia-ml.so.1')
            if lib.nvmlInit_v2() == 0:
                self.nvml = lib
                self.is_available = True
        except OSError:
            pass

    def reinit(self):
        with self._lock:
            if self.nvml:
                try:
                    self.nvml.nvmlShutdown()
                except Exception:
                    pass
            self._cache_at = 0
            self._init_nvml()

    def _query_via_nvidia_smi(self):
        fields = 'index,uuid,pci.bus_id,name,driver_model.current,driver_model.pending,temperature.gpu,utilization.gpu,memory.total,memory.used,memory.free,display_active,driver_version'
        return parse_smi_csv(run_smi('--query-gpu=' + fields, '--format=csv,noheader,nounits'))

    def query_all_gpus(self, force=False):
        with self._lock:
            if not force and time.monotonic() - self._cache_at < 2.5:
                return [dict(g) for g in self._cache]
            self.last_error = None
            try:
                rows = self._query_via_nvidia_smi()
            except FileNotFoundError:
                rows = []
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                self.last_error = str(exc)
                rows = []
            if os.name == 'nt':
                try:
                    from .windows_graphics import enumerate_adapters
                    for row in enumerate_adapters():
                        # DXGI enumeration cannot see TCC; NVIDIA UUIDs remain authoritative.
                        if row['vendor'] != 'NVIDIA' or not rows:
                            row['index'] = max((r['index'] for r in rows), default=-1) + 1
                            rows.append(row)
                except (OSError, RuntimeError) as exc:
                    self.last_error = self.last_error or str(exc)
            self._cache = rows
            self._cache_at = time.monotonic()
            return [dict(g) for g in rows]

    def get_gpu_count(self):
        return len(self.query_all_gpus())

    def get_summary_metrics(self):
        rows = self.query_all_gpus()
        temperatures = [g['temp_c'] for g in rows if g.get('temp_c') is not None]
        loads = [g['load_percent'] for g in rows if g.get('load_percent') is not None]
        known = [g for g in rows if g.get('vram_total_mib') and g.get('vram_used_mib') is not None]
        total = sum(g['vram_total_mib'] for g in known)
        return dict(gpu_count=len(rows), peak_temp=max(temperatures, default=None),
            avg_load=round(sum(loads)/len(loads), 1) if loads else None,
            total_vram_pct=round(sum(g['vram_used_mib'] for g in known)/total*100, 1) if total else None,
            gpus=rows, error=self.last_error)

hardware = HardwareEngine()

def get_authoritative_driver_modes(force_refresh=False):
    return {g['uuid'].upper(): g['driver_mode'] for g in hardware.query_all_gpus(force_refresh)
            if g['driver_mode'] in ('WDDM', 'TCC')}
