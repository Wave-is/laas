"""Sampled monitoring history: GPU and model server metrics in a small SQLite database.

Everything here is read-only towards hardware and the model server:
- GPU values come from the polling topology, llama-swap ``GET /metrics`` (power, fan) and, as a
  fallback, ``nvidia-smi --query-gpu``;
- request and generation data come from the llama-server processes listed by llama-swap
  ``GET /running``. They are queried directly on their loopback ``proxy`` address (``/metrics`` when
  llama-server runs with ``--metrics``, otherwise ``/slots``). Station never goes through
  ``/upstream/<model>/...`` because llama-swap would load a model that has just been unloaded.

Storage keeps raw samples (≈10 s) for one day, 1-minute averages for up to seven days, and a hard
row limit. Importing this module creates no files.
"""
import csv
import logging
import math
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

SAMPLE_INTERVAL = 10.0
RAW_RETENTION = 24 * 3600
RETENTION = 7 * 24 * 3600
DOWNSAMPLE_STEP = 60
MAINTENANCE_INTERVAL = 3600
MAX_GPU_ROWS = 400_000
MAX_SERVER_ROWS = 100_000
HTTP_TIMEOUT = 2.0
LOOPBACK = ('127.0.0.1', 'localhost', '::1')


def history_path():
    from .paths import data_dir
    return data_dir() / 'metrics' / 'history.sqlite3'


def _float(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


# ---------------------------------------------------------------- Prometheus text parsing

def _parse_labels(text):
    labels, i, n = {}, 0, len(text)
    while i < n:
        while i < n and text[i] in ' ,':
            i += 1
        eq = text.find('=', i)
        if eq < 0:
            break
        key = text[i:eq].strip()
        i = eq + 1
        if i >= n or text[i] != '"':
            break
        i += 1
        value = []
        while i < n and text[i] != '"':
            if text[i] == '\\' and i + 1 < n:
                i += 1
                value.append({'n': '\n'}.get(text[i], text[i]))
            else:
                value.append(text[i])
            i += 1
        labels[key] = ''.join(value)
        i += 1
    return labels


def parse_prometheus(text):
    """[(name, labels, value)] from Prometheus text exposition; malformed lines are skipped."""
    result = []
    for raw in (text or '').splitlines():
        line = raw.strip()
        if not line or line.startswith('#'):
            continue
        try:
            if '{' in line:
                name, rest = line.split('{', 1)
                end = rest.rfind('}')
                if end < 0:
                    continue
                labels, tail = _parse_labels(rest[:end]), rest[end + 1:]
            else:
                name, _, tail = line.partition(' ')
                labels = {}
            parts = tail.split()
            value = _float(parts[0]) if parts else None
        except Exception:
            continue
        if name.strip() and value is not None:
            result.append((name.strip(), labels, value))
    return result


def gpus_from_swap_metrics(samples):
    """{uuid: partial GPU record} from llama-swap ``llamaswap_gpu_*`` gauges."""
    fields = {'llamaswap_gpu_temperature_celsius': 'temp', 'llamaswap_gpu_util_percent': 'load',
              'llamaswap_gpu_power_draw_watts': 'power', 'llamaswap_gpu_fan_speed_percent': 'fan',
              'llamaswap_gpu_memory_used_bytes': 'vram_used', 'llamaswap_gpu_memory_total_bytes': 'vram_total'}
    gpus = {}
    for name, labels, value in samples:
        key = fields.get(name)
        uuid = labels.get('uuid')
        if not key or not uuid:
            continue
        record = gpus.setdefault(uuid, {'uuid': uuid, 'index': _int(labels.get('id')), 'name': labels.get('name') or ''})
        record[key] = value / 1048576 if key.startswith('vram') else value
    return gpus


def llama_server_stats(samples):
    """Counters of one llama-server ``/metrics`` page (names use ``llamacpp:`` prefix)."""
    values = {name.split(':', 1)[-1]: value for name, labels, value in samples if name.startswith('llamacpp')}
    return {'in_flight': values.get('requests_processing'),
            'tokens_total': values.get('tokens_predicted_total'),
            'speed': values.get('predicted_tokens_seconds')}


def swap_server_stats(samples):
    """Request counters llama-swap may expose in newer versions; absent values stay None."""
    in_flight = requests_total = None
    for name, labels, value in samples:
        lowered = name.lower()
        if 'in_flight' in lowered or 'inflight' in lowered:
            in_flight = (in_flight or 0) + value
        elif lowered.endswith('requests_total'):
            requests_total = (requests_total or 0) + value
    return {'in_flight': in_flight, 'requests_total': requests_total}


def parse_smi_extras(text):
    """{uuid: {'power', 'power_limit', 'fan'}} from ``uuid,power.draw,power.limit,fan.speed`` CSV."""
    result = {}
    for row in csv.reader((text or '').splitlines(), skipinitialspace=True):
        if len(row) < 4 or not row[0].strip().startswith('GPU-'):
            continue
        result[row[0].strip()] = {'power': _float(row[1]), 'power_limit': _float(row[2]), 'fan': _float(row[3])}
    return result


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- storage

class MetricsStore:
    """Thread-safe SQLite history. Every operation opens its own short connection under a lock."""

    def __init__(self, path=None):
        self.path = Path(path) if path else history_path()
        self.lock = threading.RLock()
        self._ready = False

    def _connect(self):
        if not self._ready:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(str(self.path), timeout=10)
        if not self._ready:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS gpu_samples (
                    ts REAL NOT NULL, uuid TEXT NOT NULL, idx INTEGER, name TEXT,
                    temp REAL, load REAL, vram_used REAL, vram_total REAL, power REAL, fan REAL,
                    resolution INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS gpu_samples_ts ON gpu_samples(ts);
                CREATE TABLE IF NOT EXISTS server_samples (
                    ts REAL NOT NULL, online REAL, in_flight REAL, requests REAL, tokens_per_s REAL,
                    resolution INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS server_samples_ts ON server_samples(ts);
            ''')
            self._ready = True
        return connection

    def add_sample(self, ts, gpus, server=None):
        gpu_rows = [(ts, g['uuid'], g.get('index'), g.get('name'), _float(g.get('temp')), _float(g.get('load')),
                     _float(g.get('vram_used')), _float(g.get('vram_total')), _float(g.get('power')), _float(g.get('fan')))
                    for g in gpus if g.get('uuid')]
        with self.lock:
            connection = self._connect()
            try:
                with connection:
                    connection.executemany('INSERT INTO gpu_samples(ts, uuid, idx, name, temp, load, vram_used, vram_total, power, fan) '
                                           'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', gpu_rows)
                    if server is not None:
                        connection.execute('INSERT INTO server_samples(ts, online, in_flight, requests, tokens_per_s) VALUES (?, ?, ?, ?, ?)',
                                           (ts, 1.0 if server.get('online') else 0.0, _float(server.get('in_flight')),
                                            _float(server.get('requests')), _float(server.get('tokens_per_s'))))
            finally:
                connection.close()

    def maintain(self, now=None):
        """Average raw samples older than a day into 1-minute rows, drop data older than 7 days, cap rows."""
        now = time.time() if now is None else now
        cutoff = math.floor((now - RAW_RETENTION) / DOWNSAMPLE_STEP) * DOWNSAMPLE_STEP
        step = DOWNSAMPLE_STEP
        with self.lock:
            connection = self._connect()
            try:
                with connection:
                    connection.execute(
                        'INSERT INTO gpu_samples(ts, uuid, idx, name, temp, load, vram_used, vram_total, power, fan, resolution) '
                        f'SELECT CAST(ts / {step} AS INTEGER) * {step} + {step // 2}, uuid, MAX(idx), MAX(name), AVG(temp), AVG(load), '
                        f'AVG(vram_used), MAX(vram_total), AVG(power), AVG(fan), {step} FROM gpu_samples '
                        f'WHERE resolution = 0 AND ts < ? GROUP BY uuid, CAST(ts / {step} AS INTEGER)', (cutoff,))
                    connection.execute('DELETE FROM gpu_samples WHERE resolution = 0 AND ts < ?', (cutoff,))
                    connection.execute(
                        'INSERT INTO server_samples(ts, online, in_flight, requests, tokens_per_s, resolution) '
                        f'SELECT CAST(ts / {step} AS INTEGER) * {step} + {step // 2}, AVG(online), AVG(in_flight), SUM(requests), '
                        f'AVG(tokens_per_s), {step} FROM server_samples WHERE resolution = 0 AND ts < ? '
                        f'GROUP BY CAST(ts / {step} AS INTEGER)', (cutoff,))
                    connection.execute('DELETE FROM server_samples WHERE resolution = 0 AND ts < ?', (cutoff,))
                    for table, limit in (('gpu_samples', MAX_GPU_ROWS), ('server_samples', MAX_SERVER_ROWS)):
                        connection.execute(f'DELETE FROM {table} WHERE ts < ?', (now - RETENTION,))
                        count = connection.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
                        if count > limit:
                            connection.execute(f'DELETE FROM {table} WHERE rowid IN '
                                               f'(SELECT rowid FROM {table} ORDER BY ts LIMIT ?)', (count - limit,))
            finally:
                connection.close()

    def gpu_series(self, since, bucket=10, until=None):
        """{uuid: {'index', 'name', 'points': [(ts, temp, load, vram_pct, power, fan, vram_used, vram_total)]}}."""
        until = time.time() + 60 if until is None else until
        bucket = max(1, int(bucket))
        with self.lock:
            if not self.path.is_file():
                return {}
            connection = self._connect()
            try:
                rows = connection.execute(
                    f'SELECT uuid, MAX(idx), MAX(name), CAST(ts / {bucket} AS INTEGER) * {bucket} + {bucket / 2} AS b, '
                    'AVG(temp), AVG(load), AVG(vram_used * 100.0 / NULLIF(vram_total, 0)), AVG(power), AVG(fan), '
                    'AVG(vram_used), MAX(vram_total) '
                    f'FROM gpu_samples WHERE ts >= ? AND ts <= ? GROUP BY uuid, CAST(ts / {bucket} AS INTEGER) ORDER BY b',
                    (since, until)).fetchall()
            finally:
                connection.close()
        result = {}
        for uuid, idx, name, ts, *values in rows:
            series = result.setdefault(uuid, {'index': idx, 'name': name or '', 'points': []})
            if idx is not None:
                series['index'] = idx
            series['points'].append((ts, *values))
        return result

    def server_series(self, since, bucket=10, until=None):
        """[(ts, online, in_flight, requests, tokens_per_s)] averaged per bucket (requests summed)."""
        until = time.time() + 60 if until is None else until
        bucket = max(1, int(bucket))
        with self.lock:
            if not self.path.is_file():
                return []
            connection = self._connect()
            try:
                return [tuple(row) for row in connection.execute(
                    f'SELECT CAST(ts / {bucket} AS INTEGER) * {bucket} + {bucket / 2} AS b, AVG(online), AVG(in_flight), '
                    f'SUM(requests), AVG(tokens_per_s) FROM server_samples WHERE ts >= ? AND ts <= ? '
                    f'GROUP BY CAST(ts / {bucket} AS INTEGER) ORDER BY b', (since, until)).fetchall()]
            finally:
                connection.close()

    def row_counts(self):
        with self.lock:
            if not self.path.is_file():
                return 0, 0
            connection = self._connect()
            try:
                return tuple(connection.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('gpu_samples', 'server_samples'))
            finally:
                connection.close()


# ---------------------------------------------------------------- overheating alerts

class OverheatMonitor:
    """Alert when a GPU stays at or above the threshold for N samples.

    After an alert the GPU is disarmed until it cools ``rearm_delta`` °C below the threshold, and a GPU
    is alerted at most once per ``min_interval`` seconds.
    """

    def __init__(self, consecutive=3, rearm_delta=5.0, min_interval=600.0):
        self.consecutive = consecutive
        self.rearm_delta = rearm_delta
        self.min_interval = min_interval
        self.state = {}

    def update(self, uuid, temp, threshold, now, enabled=True):
        state = self.state.setdefault(uuid, {'count': 0, 'armed': True, 'last': None})
        temp = _float(temp)
        if temp is None:
            state['count'] = 0
            return False
        state['count'] = state['count'] + 1 if temp >= threshold else 0
        if not state['armed'] and temp <= threshold - self.rearm_delta:
            state['armed'] = True
        if not enabled or not state['armed'] or state['count'] < self.consecutive:
            return False
        if state['last'] is not None and now - state['last'] < self.min_interval:
            return False
        state['armed'] = False
        state['last'] = now
        return True


# ---------------------------------------------------------------- sampling

def _http_get(url, timeout=HTTP_TIMEOUT):
    import requests
    return requests.get(url, timeout=timeout)


def _run_smi_extras():
    from .hardware import find_nvidia_smi, hidden_options
    import subprocess
    executable = find_nvidia_smi()
    if not executable:
        return {}
    result = subprocess.run([executable, '--query-gpu=uuid,power.draw,power.limit,fan.speed', '--format=csv,noheader,nounits'],
                            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=5, **hidden_options())
    return parse_smi_extras(result.stdout) if result.returncode == 0 else {}


class MetricsSampler:
    """Collects one sample per ``interval`` from poll snapshots on a background thread."""

    def __init__(self, store=None, server_url=None, http_get=_http_get, smi=_run_smi_extras, clock=time.time,
                 interval=SAMPLE_INTERVAL, on_alert=None, alert_settings=None):
        self.store = store or MetricsStore()
        self.server_url = server_url
        self.http_get = http_get
        self.smi = smi
        self.clock = clock
        self.interval = interval
        self.on_alert = on_alert
        self.alert_settings = alert_settings or (lambda: (True, 85.0))
        self.overheat = OverheatMonitor()
        self.last_sample = None
        self.last_maintenance = None
        self.previous = {}
        self.latest = None
        self.busy = threading.Lock()

    # Poll hook signature: (topology, backend_info, running_model_ids).
    def on_poll(self, topology, backend_info, running):
        now = self.clock()
        if self.last_sample is not None and now - self.last_sample < self.interval - 0.5:
            return False
        if not self.busy.acquire(blocking=False):
            return False
        self.last_sample = now
        thread = threading.Thread(target=self._sample_safely, args=(topology, backend_info, now), daemon=True)
        thread.start()
        return True

    def _sample_safely(self, topology, backend_info, now):
        try:
            self.sample(topology, backend_info, now)
        except Exception:
            log.exception('Metrics sample failed')
        finally:
            self.busy.release()

    def _url(self):
        if self.server_url:
            return self.server_url() if callable(self.server_url) else self.server_url
        from . import model_server
        return model_server.local_url()

    def _get_text(self, url):
        try:
            response = self.http_get(url)
            return response.text if response.status_code == 200 else None
        except Exception:
            return None

    def _get_json(self, url):
        try:
            response = self.http_get(url)
            return response.json() if response.status_code == 200 else None
        except Exception:
            return None

    def collect_gpus(self, topology, swap_samples):
        from_swap = gpus_from_swap_metrics(swap_samples)
        gpus = []
        for device in getattr(topology, 'devices', None) or []:
            if getattr(device, 'vendor', '') == 'CPU' or not getattr(device, 'uuid', None):
                continue
            extra = from_swap.pop(device.uuid, {})
            gpus.append({'uuid': device.uuid, 'index': device.index, 'name': device.name,
                         'temp': device.temp_c if device.temp_c is not None else extra.get('temp'),
                         'load': device.load_percent if device.load_percent is not None else extra.get('load'),
                         'vram_used': device.vram_used_mib if device.vram_used_mib is not None else extra.get('vram_used'),
                         'vram_total': device.vram_total_mib if device.vram_total_mib is not None else extra.get('vram_total'),
                         'power': extra.get('power'), 'fan': extra.get('fan')})
        gpus.extend(from_swap.values())
        if any(g.get('power') is None for g in gpus) or not gpus:
            try:
                extras = self.smi() or {}
            except Exception:
                extras = {}
            for gpu in gpus:
                extra = extras.get(gpu['uuid'], {})
                for key in ('power', 'fan'):
                    if gpu.get(key) is None:
                        gpu[key] = extra.get(key)
        return gpus

    def collect_server(self, base, online, swap_samples, now):
        if not online:
            self.previous.pop('server', None)
            return {'online': False}
        stats = swap_server_stats(swap_samples)
        in_flight, requests, tokens_total, speed_known, slot_tokens = stats['in_flight'], None, None, False, 0.0
        tasks = {}
        running = self._get_json(base + '/running') or {}
        for row in running.get('running', []) if isinstance(running, dict) else []:
            proxy = row.get('proxy') if isinstance(row, dict) and row.get('state') == 'ready' else None
            parts = urlsplit(proxy) if isinstance(proxy, str) else None
            if not parts or parts.scheme != 'http' or parts.hostname not in LOOPBACK:
                continue
            proxy = proxy.rstrip('/')
            text = self._get_text(proxy + '/metrics')
            if text is not None:
                server = llama_server_stats(parse_prometheus(text))
                if server['in_flight'] is not None:
                    in_flight = (in_flight or 0) + server['in_flight']
                if server['tokens_total'] is not None:
                    tokens_total = (tokens_total or 0) + server['tokens_total']
                    speed_known = True
            slots = self._get_json(proxy + '/slots')
            if isinstance(slots, list):
                speed_known = True
                busy = 0
                for slot in slots:
                    if not isinstance(slot, dict):
                        continue
                    key = f"{proxy}#{slot.get('id')}"
                    decoded = 0
                    for token in slot.get('next_token') or []:
                        decoded = max(decoded, _int(token.get('n_decoded')) or 0)
                    processing = bool(slot.get('is_processing'))
                    busy += processing
                    tasks[key] = (slot.get('id_task'), decoded if processing else 0)
                if text is None:
                    in_flight = (in_flight or 0) + busy
        previous = self.previous.get('server')
        dt = now - previous['ts'] if previous else None
        if previous and tasks:
            started = 0
            for key, (task, decoded) in tasks.items():
                before = previous['tasks'].get(key)
                if before is None:
                    continue
                if task != before[0] and task is not None:
                    started += 1
                    slot_tokens += decoded
                else:
                    slot_tokens += max(0, decoded - before[1])
            requests = started
        if stats['requests_total'] is not None and previous and previous.get('requests_total') is not None:
            requests = max(0.0, stats['requests_total'] - previous['requests_total'])
        tokens_per_s = None
        if dt and dt > 0 and speed_known:
            if tokens_total is not None and previous and previous.get('tokens_total') is not None:
                tokens_per_s = max(0.0, tokens_total - previous['tokens_total']) / dt
            elif tokens_total is None:
                tokens_per_s = slot_tokens / dt
        self.previous['server'] = {'ts': now, 'tasks': tasks, 'tokens_total': tokens_total,
                                   'requests_total': stats['requests_total']}
        return {'online': True, 'in_flight': in_flight, 'requests': requests, 'tokens_per_s': tokens_per_s}

    def sample(self, topology, backend_info, now=None):
        now = self.clock() if now is None else now
        online = bool((backend_info or {}).get('online'))
        base = self._url().rstrip('/')
        swap_samples = []
        if online:
            text = self._get_text(base + '/metrics')
            swap_samples = parse_prometheus(text) if text else []
        gpus = self.collect_gpus(topology, swap_samples)
        server = self.collect_server(base, online, swap_samples, now)
        self.store.add_sample(now, gpus, server)
        self.latest = {'ts': now, 'gpus': gpus, 'server': server}
        if self.last_maintenance is None or now - self.last_maintenance >= MAINTENANCE_INTERVAL:
            self.last_maintenance = now
            try:
                self.store.maintain(now)
            except Exception:
                log.exception('Metrics maintenance failed')
        self.check_alerts(gpus, now)
        return self.latest

    def check_alerts(self, gpus, now):
        try:
            enabled, threshold = self.alert_settings()
        except Exception:
            enabled, threshold = True, 90.0
        alerts = [gpu for gpu in gpus if self.overheat.update(gpu['uuid'], gpu.get('temp'), threshold, now, enabled)]
        for gpu in alerts:
            if self.on_alert:
                self.on_alert(gpu, threshold)
        return alerts
