"""Per-card NVIDIA details for the Hardware page: power, fans, PCIe link, P-state, throttling, NVLink.

Read-only ``nvidia-smi`` queries, cached: the poll thread calls :meth:`GpuDetailsCache.refresh`
every few seconds, the query itself runs at most every ``interval`` seconds (NVLink less often).
Unknown or unsupported values are ``None``.
"""
import ctypes
import re
import subprocess
import threading
import time
from .hardware import find_nvidia_smi, hardware, nvmlUtilization_t, nvmlMemory_t

FIELDS = ('index', 'uuid', 'name', 'driver_version', 'vbios_version', 'pstate', 'temperature.gpu', 'utilization.gpu',
          'memory.used', 'memory.total', 'power.draw', 'power.limit', 'fan.speed',
          'pcie.link.gen.current', 'pcie.link.gen.max', 'pcie.link.width.current', 'pcie.link.width.max',
          'clocks_throttle_reasons.active', 'driver_model.current', 'driver_model.pending')
OPTIONAL = ('clocks_throttle_reasons.active', 'driver_model.current', 'driver_model.pending', 'vbios_version')
NUMBERS = {'index': int, 'temperature.gpu': float, 'utilization.gpu': float, 'memory.used': float, 'memory.total': float,
           'power.draw': float, 'power.limit': float, 'fan.speed': float, 'pcie.link.gen.current': int,
           'pcie.link.gen.max': int, 'pcie.link.width.current': int, 'pcie.link.width.max': int}
UNKNOWN = {'', 'n/a', '[n/a]', '[not supported]', 'not supported', '[unknown error]', '[insufficient permissions]', '[gpu is lost]'}
# nvidia-smi clocks_throttle_reasons bit mask -> reason code (labels are translated by the UI).
THROTTLE_BITS = ((0x1, 'idle'), (0x2, 'app_clock'), (0x4, 'sw_power_cap'), (0x8, 'hw_slowdown'), (0x10, 'sync_boost'),
                 (0x20, 'sw_thermal'), (0x40, 'hw_thermal'), (0x80, 'hw_power_brake'), (0x100, 'display_clock'))


def _value(field, raw):
    raw = raw.strip()
    if raw.lower() in UNKNOWN:
        return None
    kind = NUMBERS.get(field)
    if not kind:
        return raw
    try:
        number = float(raw.split()[0])
    except (ValueError, IndexError):
        return None
    return int(number) if kind is int else number


def throttle_reasons(mask):
    """Reason codes from a hex mask such as ``0x0000000000000004``; None when unknown."""
    if mask is None:
        return None
    try:
        value = int(str(mask), 16)
    except ValueError:
        return None
    return [code for bit, code in THROTTLE_BITS if value & bit]


def parse_query(text, fields=FIELDS):
    """uuid -> details from ``--format=csv,noheader,nounits`` output."""
    result = {}
    for line in (text or '').splitlines():
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split(',')]
        if len(cells) != len(fields):
            continue
        row = {field: _value(field, cell) for field, cell in zip(fields, cells)}
        if not row.get('uuid'):
            continue
        row['throttle'] = throttle_reasons(row.get('clocks_throttle_reasons.active'))
        result[row['uuid']] = row
    return result


def parse_nvlink(text):
    """uuid -> [{'link': n, 'active': bool, 'speed_gbps': float or None}] from ``nvidia-smi nvlink -s``."""
    result = {}
    uuid = None
    for line in (text or '').splitlines():
        header = re.match(r'^GPU\s+\d+:.*\(UUID:\s*([^)]+)\)', line.strip())
        if header:
            uuid = header.group(1).strip()
            result[uuid] = []
            continue
        link = re.match(r'^Link\s+(\d+):\s*(.+)$', line.strip())
        if link and uuid:
            state = link.group(2).strip()
            speed = re.match(r'([\d.]+)\s*GB/s', state)
            result[uuid].append({'link': int(link.group(1)), 'active': bool(speed) and float(speed.group(1)) > 0,
                                 'speed_gbps': float(speed.group(1)) if speed else None})
    return result


def run_nvidia_smi(args, timeout=3.0):
    from .hardware import find_nvidia_smi, hidden_options
    exe = find_nvidia_smi()
    if not exe:
        raise FileNotFoundError('nvidia-smi')
    done = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout, encoding='utf-8',
                          errors='replace', stdin=subprocess.DEVNULL, **hidden_options())
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or '').strip()[:300] or f'nvidia-smi exit {done.returncode}')
    return done.stdout


def query_via_nvml():
    """Query detailed GPU metrics directly through in-process NVML C-API (microseconds, zero subprocesses)."""
    if not hardware.is_available or not hardware.nvml:
        return None
    nv = hardware.nvml
    count = ctypes.c_uint()
    if nv.nvmlDeviceGetCount_v2(ctypes.byref(count)) != 0:
        return None
    drv_buf = ctypes.create_string_buffer(64)
    driver_version = drv_buf.value.decode('utf-8', errors='replace').strip() if hasattr(nv, 'nvmlSystemGetDriverVersion') and nv.nvmlSystemGetDriverVersion(drv_buf, 64) == 0 else ''

    result = {}
    for i in range(count.value):
        dev = ctypes.c_void_p()
        if nv.nvmlDeviceGetHandleByIndex_v2(i, ctypes.byref(dev)) != 0:
            continue
        uuid_b = ctypes.create_string_buffer(64)
        nv.nvmlDeviceGetUUID(dev, uuid_b, 64)
        uuid_str = uuid_b.value.decode('utf-8', errors='replace').strip()

        name_b = ctypes.create_string_buffer(64)
        nv.nvmlDeviceGetName(dev, name_b, 64)
        name_str = name_b.value.decode('utf-8', errors='replace').strip()

        vbios_b = ctypes.create_string_buffer(64)
        vbios_str = vbios_b.value.decode('utf-8', errors='replace').strip() if hasattr(nv, 'nvmlDeviceGetVbiosVersion') and nv.nvmlDeviceGetVbiosVersion(dev, vbios_b, 64) == 0 else None

        pstate_val = ctypes.c_int()
        pstate_str = f'P{pstate_val.value}' if hasattr(nv, 'nvmlDeviceGetPerformanceState') and nv.nvmlDeviceGetPerformanceState(dev, ctypes.byref(pstate_val)) == 0 else None

        temp = ctypes.c_uint()
        temp_val = float(temp.value) if hasattr(nv, 'nvmlDeviceGetTemperature') and nv.nvmlDeviceGetTemperature(dev, 0, ctypes.byref(temp)) == 0 else None

        util = nvmlUtilization_t()
        gpu_util = float(util.gpu) if hasattr(nv, 'nvmlDeviceGetUtilizationRates') and nv.nvmlDeviceGetUtilizationRates(dev, ctypes.byref(util)) == 0 else None

        mem = nvmlMemory_t()
        mem_used, mem_total = None, None
        if hasattr(nv, 'nvmlDeviceGetMemoryInfo') and nv.nvmlDeviceGetMemoryInfo(dev, ctypes.byref(mem)) == 0:
            mem_used = round(float(mem.used) / (1024 * 1024), 2)
            mem_total = round(float(mem.total) / (1024 * 1024), 2)

        power = ctypes.c_uint()
        power_draw = round(power.value / 1000.0, 2) if hasattr(nv, 'nvmlDeviceGetPowerUsage') and nv.nvmlDeviceGetPowerUsage(dev, ctypes.byref(power)) == 0 else None

        limit = ctypes.c_uint()
        power_limit = round(limit.value / 1000.0, 2) if hasattr(nv, 'nvmlDeviceGetEnforcedPowerLimit') and nv.nvmlDeviceGetEnforcedPowerLimit(dev, ctypes.byref(limit)) == 0 else None

        fan = ctypes.c_uint()
        fan_speed = float(fan.value) if hasattr(nv, 'nvmlDeviceGetFanSpeed') and nv.nvmlDeviceGetFanSpeed(dev, ctypes.byref(fan)) == 0 else None

        gen_curr = ctypes.c_uint()
        pcie_gen_curr = int(gen_curr.value) if hasattr(nv, 'nvmlDeviceGetCurrPcieLinkGeneration') and nv.nvmlDeviceGetCurrPcieLinkGeneration(dev, ctypes.byref(gen_curr)) == 0 else None

        gen_max = ctypes.c_uint()
        pcie_gen_max = int(gen_max.value) if hasattr(nv, 'nvmlDeviceGetMaxPcieLinkGeneration') and nv.nvmlDeviceGetMaxPcieLinkGeneration(dev, ctypes.byref(gen_max)) == 0 else None

        w_curr = ctypes.c_uint()
        pcie_w_curr = int(w_curr.value) if hasattr(nv, 'nvmlDeviceGetCurrPcieLinkWidth') and nv.nvmlDeviceGetCurrPcieLinkWidth(dev, ctypes.byref(w_curr)) == 0 else None

        w_max = ctypes.c_uint()
        pcie_w_max = int(w_max.value) if hasattr(nv, 'nvmlDeviceGetMaxPcieLinkWidth') and nv.nvmlDeviceGetMaxPcieLinkWidth(dev, ctypes.byref(w_max)) == 0 else None

        mode, pending = ctypes.c_int(), ctypes.c_int()
        if hasattr(nv, 'nvmlDeviceGetDriverModel') and nv.nvmlDeviceGetDriverModel(dev, ctypes.byref(mode), ctypes.byref(pending)) == 0:
            drv_mode = 'WDDM' if mode.value == 0 else 'TCC' if mode.value == 1 else 'UNKNOWN'
            pending_mode = 'WDDM' if pending.value == 0 else 'TCC' if pending.value == 1 else 'UNKNOWN'
        else:
            drv_mode, pending_mode = None, None

        throttle = ctypes.c_ulonglong()
        if hasattr(nv, 'nvmlDeviceGetCurrentClocksThrottleReasons') and nv.nvmlDeviceGetCurrentClocksThrottleReasons(dev, ctypes.byref(throttle)) == 0:
            throttle_mask = hex(throttle.value)
        else:
            throttle_mask = None

        row = {
            'index': i,
            'uuid': uuid_str,
            'name': name_str,
            'driver_version': driver_version,
            'vbios_version': vbios_str,
            'pstate': pstate_str,
            'temperature.gpu': temp_val,
            'utilization.gpu': gpu_util,
            'memory.used': mem_used,
            'memory.total': mem_total,
            'power.draw': power_draw,
            'power.limit': power_limit,
            'fan.speed': fan_speed,
            'pcie.link.gen.current': pcie_gen_curr,
            'pcie.link.gen.max': pcie_gen_max,
            'pcie.link.width.current': pcie_w_curr,
            'pcie.link.width.max': pcie_w_max,
            'clocks_throttle_reasons.active': throttle_mask,
            'driver_model.current': drv_mode,
            'driver_model.pending': pending_mode,
            'throttle': throttle_reasons(throttle_mask),
        }
        result[uuid_str] = row
    return result


class GpuDetailsCache:
    def __init__(self, runner=run_nvidia_smi, clock=time.monotonic, interval=5.0, nvlink_interval=300.0):
        self.runner = runner
        self.clock = clock
        self.interval = interval
        self.nvlink_interval = nvlink_interval
        self.fields = FIELDS
        self._lock = threading.Lock()
        self._details = {}
        self._nvlink = {}
        self._queried = None
        self._nvlink_queried = None
        self.error = None

    def refresh(self, force=False):
        """Query nvidia-smi when the cached data is older than ``interval``. Returns True when queried."""
        now = self.clock()
        if not force and self._queried is not None and now - self._queried < self.interval:
            return False
        self._queried = now
        if not find_nvidia_smi():
            with self._lock:
                self._details = {}
                self._nvlink = {}
                self.error = None
            return False
        try:
            details = self._query()
            error = None
        except Exception as exc:
            details, error = {}, str(exc)
        nvlink = None
        if not error and (force or self._nvlink_queried is None or now - self._nvlink_queried >= self.nvlink_interval):
            self._nvlink_queried = now
            try:
                nvlink = parse_nvlink(self.runner(['nvlink', '-s']))
            except Exception:
                nvlink = {}
        with self._lock:
            self._details = details
            self.error = error
            if nvlink is not None:
                self._nvlink = nvlink
        return True

    def _query(self):
        if self.runner is run_nvidia_smi:
            try:
                res = query_via_nvml()
                if res is not None:
                    return res
            except Exception:
                pass
        try:
            return parse_query(self.runner(['--query-gpu=' + ','.join(self.fields), '--format=csv,noheader,nounits']), self.fields)
        except RuntimeError as exc:
            # Older or newer drivers reject some field names; retry once without the optional ones.
            if self.fields == FIELDS and 'field' in str(exc).lower():
                self.fields = tuple(f for f in FIELDS if f not in OPTIONAL)
                return parse_query(self.runner(['--query-gpu=' + ','.join(self.fields), '--format=csv,noheader,nounits']), self.fields)
            raise

    def get(self, uuid):
        """Details for one card merged with its NVLink links (``{}`` when unknown)."""
        with self._lock:
            row = dict(self._details.get(uuid) or {})
            links = self._nvlink.get(uuid)
        if row or links is not None:
            row['nvlink'] = links
        return row


gpu_details = GpuDetailsCache()
