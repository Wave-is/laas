"""Per-card NVIDIA details for the Hardware page: power, fans, PCIe link, P-state, throttling, NVLink.

Read-only ``nvidia-smi`` queries, cached: the poll thread calls :meth:`GpuDetailsCache.refresh`
every few seconds, the query itself runs at most every ``interval`` seconds (NVLink less often).
Unknown or unsupported values are ``None``.
"""
import re
import subprocess
import threading
import time
from .hardware import find_nvidia_smi

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


def run_nvidia_smi(args, timeout=15):
    from .hardware import find_nvidia_smi, hidden_options
    exe = find_nvidia_smi()
    if not exe:
        raise FileNotFoundError('nvidia-smi')
    done = subprocess.run([exe] + list(args), capture_output=True, text=True, timeout=timeout, encoding='utf-8',
                          errors='replace', stdin=subprocess.DEVNULL, **hidden_options())
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or '').strip()[:300] or f'nvidia-smi exit {done.returncode}')
    return done.stdout


class GpuDetailsCache:
    def __init__(self, runner=run_nvidia_smi, clock=time.monotonic, interval=5.0, nvlink_interval=60.0):
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
