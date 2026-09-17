"""Monitoring history: storage, downsampling, alerts and metrics parsing. No network, no hardware."""
from types import SimpleNamespace
import threading
from src import metrics_history as mh

DAY = 24 * 3600
SWAP_METRICS = '''# HELP llamaswap_cpu_util_percent CPU utilization per core (0-100)
# TYPE llamaswap_cpu_util_percent gauge
llamaswap_cpu_util_percent{core="0"} 16
llamaswap_memory_total_bytes 205761019904
llamaswap_network_bytes_total{interface="LAN 2,5G",direction="recv"} 515128834
llamaswap_gpu_temperature_celsius{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 42
llamaswap_gpu_util_percent{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 7
llamaswap_gpu_memory_used_bytes{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 2.0239613952e+10
llamaswap_gpu_memory_total_bytes{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 2.4146608128e+10
llamaswap_gpu_fan_speed_percent{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 30
llamaswap_gpu_power_draw_watts{id="0",name="NVIDIA RTX A5000",uuid="GPU-aaa"} 8.58
llamaswap_gpu_power_draw_watts{id="1",name="NVIDIA RTX A5000",uuid="GPU-bbb"} 10.8
broken line without value
weird{label="a \\"quoted\\" b"} NaN
'''


def gpu(uuid='GPU-aaa', index=0, temp=50, **extra):
    return dict(uuid=uuid, index=index, name='RTX A5000', temp=temp, load=10, vram_used=1000, vram_total=24000, power=100, fan=30, **extra)


def test_prometheus_parsing_and_gpu_extras():
    samples = mh.parse_prometheus(SWAP_METRICS)
    names = [s[0] for s in samples]
    assert 'weird' not in names and 'broken' not in names
    assert ('llamaswap_network_bytes_total', {'interface': 'LAN 2,5G', 'direction': 'recv'}, 515128834.0) in samples
    gpus = mh.gpus_from_swap_metrics(samples)
    assert gpus['GPU-aaa']['power'] == 8.58 and gpus['GPU-aaa']['fan'] == 30 and gpus['GPU-aaa']['index'] == 0
    assert round(gpus['GPU-aaa']['vram_total']) == 23028
    assert gpus['GPU-bbb'] == {'uuid': 'GPU-bbb', 'index': 1, 'name': 'NVIDIA RTX A5000', 'power': 10.8}
    assert mh.parse_prometheus('a{x="1"} 2\nb 3 1700000000') == [('a', {'x': '1'}, 2.0), ('b', {}, 3.0)]


def test_llama_server_and_smi_parsing():
    text = 'llamacpp:requests_processing 1\nllamacpp:tokens_predicted_total 5000\nllamacpp:predicted_tokens_seconds 31.5\n'
    assert mh.llama_server_stats(mh.parse_prometheus(text)) == {'in_flight': 1, 'tokens_total': 5000, 'speed': 31.5}
    assert mh.swap_server_stats(mh.parse_prometheus('llamaswap_requests_total{model="a"} 4\nllamaswap_requests_total{model="b"} 1\n'
                                                   'llamaswap_in_flight_requests 2')) == {'in_flight': 2, 'requests_total': 5}
    extras = mh.parse_smi_extras('GPU-aaa, 120.5, 230.00, 45\nGPU-bbb, [N/A], [N/A], [N/A]\ngarbage\n')
    assert extras == {'GPU-aaa': {'power': 120.5, 'power_limit': 230.0, 'fan': 45.0},
                      'GPU-bbb': {'power': None, 'power_limit': None, 'fan': None}}


def test_storage_roundtrip_with_missing_values(tmp_path):
    store = mh.MetricsStore(tmp_path / 'metrics/history.sqlite3')
    assert store.gpu_series(0) == {} and store.server_series(0) == []
    now = 1_800_000_000
    store.add_sample(now, [gpu(), {'uuid': 'GPU-bbb', 'index': 1, 'name': 'X', 'temp': None, 'vram_total': 0}],
                     {'online': True, 'in_flight': 1, 'requests': 2, 'tokens_per_s': 30.0})
    store.add_sample(now + 10, [gpu(temp=60)], {'online': False})
    series = store.gpu_series(now - 1, bucket=10, until=now + 100)
    points = series['GPU-aaa']['points']
    assert [p[1] for p in points] == [50, 60]
    assert points[0][3] == 1000 * 100 / 24000 and points[0][4] == 100 and points[0][5] == 30
    assert series['GPU-bbb']['points'][0][1:4] == (None, None, None)
    server = store.server_series(now - 1, bucket=10, until=now + 100)
    assert server[0][1:] == (1.0, 1.0, 2.0, 30.0) and server[1][1:] == (0.0, None, None, None)
    bucketed = store.gpu_series(now - 1, bucket=60, until=now + 100)
    assert len(bucketed['GPU-aaa']['points']) <= 2


def test_downsampling_and_pruning(tmp_path):
    store = mh.MetricsStore(tmp_path / 'h.sqlite3')
    now = 1_800_000_000
    old = (now - 3 * DAY) // 60 * 60
    for i in range(6):  # one minute of raw samples, three days old
        store.add_sample(old + i * 10, [gpu(temp=40 + i)], {'online': True, 'in_flight': 0, 'requests': 1, 'tokens_per_s': None})
    store.add_sample(now - 8 * DAY, [gpu()], {'online': True})
    store.add_sample(now - 60, [gpu(temp=70)], {'online': True})
    store.maintain(now)
    assert store.row_counts() == (2, 2)
    points = store.gpu_series(now - 7 * DAY, bucket=1, until=now)['GPU-aaa']['points']
    assert points[0][0] == old + 30.5 and points[0][1] == 42.5  # averaged minute
    assert points[1][1] == 70  # recent raw sample untouched
    server = store.server_series(now - 7 * DAY, bucket=1, until=now)
    assert server[0][3] == 6 and server[0][4] is None  # requests summed, missing speed stays missing
    store.maintain(now)
    assert store.row_counts() == (2, 2)


def test_row_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(mh, 'MAX_GPU_ROWS', 5)
    store = mh.MetricsStore(tmp_path / 'h.sqlite3')
    now = 1_800_000_000
    for i in range(8):
        store.add_sample(now - 100 + i, [gpu(temp=i)])
    store.maintain(now)
    assert store.row_counts()[0] == 5
    assert [p[1] for p in store.gpu_series(0, bucket=1, until=now)['GPU-aaa']['points']] == [3, 4, 5, 6, 7]


def test_overheat_hysteresis_and_rate_limit():
    monitor = mh.OverheatMonitor()
    t = 0
    def feed(temp):
        nonlocal t
        t += 10
        return monitor.update('GPU-aaa', temp, 85, t)
    assert [feed(x) for x in (86, 90, 84, 85, 85)] == [False] * 5  # interrupted streak restarts
    assert feed(88) is True
    assert [feed(90) for _ in range(10)] == [False] * 10  # stays hot: no repeat while disarmed
    assert feed(81) is False  # 4 °C below: still disarmed
    assert [feed(86) for _ in range(3)] == [False] * 3
    assert feed(80) is False  # re-armed, but the last alert was < 10 minutes ago
    assert [feed(86) for _ in range(3)] == [False] * 3
    t = 700
    assert feed(86) is True  # rate limit passed, streak continued
    assert monitor.update('GPU-bbb', 99, 85, t, enabled=False) is False
    assert monitor.update('GPU-bbb', None, 85, t) is False


class Response:
    def __init__(self, status=200, text='', data=None):
        self.status_code, self.text, self._data = status, text, data

    def json(self):
        return self._data


def test_sampler_collects_throttles_and_alerts(tmp_path):
    store = mh.MetricsStore(tmp_path / 'h.sqlite3')
    requested = []
    slots = [{'id': 0, 'id_task': 5, 'is_processing': True, 'next_token': [{'n_decoded': 100}]}]
    def http_get(url):
        requested.append(url)
        if url == 'http://127.0.0.1:9292/metrics':
            return Response(text=SWAP_METRICS)
        if url == 'http://127.0.0.1:9292/running':
            return Response(data={'running': [{'model': 'm', 'state': 'ready', 'proxy': 'http://127.0.0.1:5801'},
                                              {'model': 'x', 'state': 'starting', 'proxy': 'http://127.0.0.1:5802'},
                                              {'model': 'r', 'state': 'ready', 'proxy': 'http://10.0.0.5:5803'}]})
        if url == 'http://127.0.0.1:5801/metrics':
            return Response(501, '{"error": "no metrics"}')
        if url == 'http://127.0.0.1:5801/slots':
            return Response(data=slots)
        raise AssertionError(url)
    clock = SimpleNamespace(now=1_800_000_000.0)
    alerts = []
    sampler = mh.MetricsSampler(store, server_url='http://127.0.0.1:9292', http_get=http_get, smi=lambda: {},
                                clock=lambda: clock.now, on_alert=lambda g, th: alerts.append((g['uuid'], th)),
                                alert_settings=lambda: (True, 80.0))
    device = SimpleNamespace(index=0, uuid='GPU-aaa', name='NVIDIA RTX A5000', vendor='NVIDIA', temp_c=90,
                             load_percent=None, vram_used_mib=100, vram_total_mib=24000)
    topology = SimpleNamespace(devices=[device])
    online = {'online': True}
    for step in range(3):
        result = sampler.sample(topology, online, clock.now)
        slots[0]['next_token'][0]['n_decoded'] += 200
        clock.now += 10
    assert all('/upstream/' not in url and '5802' not in url and '10.0.0.5' not in url for url in requested)
    assert {g['uuid'] for g in result['gpus']} == {'GPU-aaa', 'GPU-bbb'}
    first = next(g for g in result['gpus'] if g['uuid'] == 'GPU-aaa')
    assert first['temp'] == 90 and first['load'] == 7 and first['power'] == 8.58 and first['vram_used'] == 100
    assert result['server'] == {'online': True, 'in_flight': 1, 'requests': 0, 'tokens_per_s': 20.0}
    assert alerts == [('GPU-aaa', 80.0)]
    assert len(store.gpu_series(0, bucket=1, until=clock.now)['GPU-aaa']['points']) == 3

    slots[0].update(id_task=9, is_processing=False)
    assert sampler.sample(topology, online, clock.now)['server']['requests'] == 1
    assert sampler.sample(topology, {'online': False}, clock.now + 10)['server'] == {'online': False}

    # Poll hook: at most one sample per interval, never on the calling thread twice at once.
    sampler.sample = lambda *args: None
    sampler.last_sample = None
    assert sampler.on_poll(topology, online, []) is True
    assert sampler.on_poll(topology, online, []) is False
    clock.now += 10
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=1)
    assert sampler.on_poll(topology, online, []) is True


def test_sampler_uses_nvidia_smi_when_power_is_missing(tmp_path):
    store = mh.MetricsStore(tmp_path / 'h.sqlite3')
    device = SimpleNamespace(index=1, uuid='GPU-ccc', name='GPU', vendor='NVIDIA', temp_c=None, load_percent=5,
                             vram_used_mib=None, vram_total_mib=None)
    sampler = mh.MetricsSampler(store, server_url='http://127.0.0.1:9292', http_get=lambda url: Response(500),
                                smi=lambda: {'GPU-ccc': {'power': 55.0, 'fan': None}}, clock=lambda: 1.0)
    result = sampler.sample(SimpleNamespace(devices=[device]), {'online': False}, 1.0)
    assert result['gpus'] == [{'uuid': 'GPU-ccc', 'index': 1, 'name': 'GPU', 'temp': None, 'load': 5,
                               'vram_used': None, 'vram_total': None, 'power': 55.0, 'fan': None}]
