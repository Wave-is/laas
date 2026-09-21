"""Unit tests for GPU details parsing and caching."""
from src import gpu_details as gd


def test_throttle_reasons_decoding():
    assert gd.throttle_reasons(None) is None
    assert gd.throttle_reasons("invalid") is None
    assert gd.throttle_reasons("0x0") == []
    reasons = gd.throttle_reasons("0x24")
    assert "sw_power_cap" in reasons
    assert "sw_thermal" in reasons


def test_parse_query_csv():
    sample = (
        "0, GPU-1111-2222, NVIDIA RTX A5000, 550.54, 94.02.59.00.01, P8, 42.0, 15.0, "
        "2048.0, 24576.0, 75.5, 230.0, 30.0, 4, 4, 16, 16, 0x0000000000000000, TCC, TCC\n"
    )
    result = gd.parse_query(sample)
    assert "GPU-1111-2222" in result
    card = result["GPU-1111-2222"]
    assert card["name"] == "NVIDIA RTX A5000"
    assert card["temperature.gpu"] == 42.0
    assert card["power.draw"] == 75.5
    assert card["power.limit"] == 230.0
    assert card["fan.speed"] == 30.0
    assert card["pcie.link.gen.current"] == 4
    assert card["pcie.link.width.current"] == 16
    assert card["driver_model.current"] == "TCC"
    assert card["throttle"] == []


def test_parse_nvlink_output():
    sample = """
GPU 0: NVIDIA RTX A5000 (UUID: GPU-1111)
    Link 0: 14.062 GB/s
    Link 1: 14.062 GB/s
GPU 1: NVIDIA RTX A5000 (UUID: GPU-2222)
    Link 0: 14.062 GB/s
    Link 1: 14.062 GB/s
"""
    parsed = gd.parse_nvlink(sample)
    assert "GPU-1111" in parsed
    assert "GPU-2222" in parsed
    assert len(parsed["GPU-1111"]) == 2
    assert parsed["GPU-1111"][0]["active"] is True
    assert parsed["GPU-1111"][0]["speed_gbps"] == 14.062


def test_cache_refresh_and_get():
    query_output = (
        "0, GPU-1111, NVIDIA RTX A5000, 550.54, 94.02.59.00.01, P8, 45.0, 10.0, "
        "1024.0, 24576.0, 60.0, 230.0, 25.0, 4, 4, 16, 16, 0x4, TCC, TCC\n"
    )
    calls = []

    def mock_runner(args):
        calls.append(list(args))
        if "nvlink" in args:
            return "GPU 0: NVIDIA RTX A5000 (UUID: GPU-1111)\n    Link 0: 14.0 GB/s\n"
        return query_output

    t = 100.0
    cache = gd.GpuDetailsCache(runner=mock_runner, clock=lambda: t, interval=5.0, nvlink_interval=30.0)

    assert cache.refresh() is True
    assert len(calls) == 2

    t += 2.0
    assert cache.refresh() is False
    assert len(calls) == 2

    t += 4.0
    assert cache.refresh() is True
    assert len(calls) == 3

    data = cache.get("GPU-1111")
    assert data["power.draw"] == 60.0
    assert data["throttle"] == ["sw_power_cap"]
    assert len(data["nvlink"]) == 1


def test_gpu_details_fast_return_when_no_smi(monkeypatch):
    calls = []
    def mock_runner(args):
        calls.append(args)
        return ""
    monkeypatch.setattr(gd, "find_nvidia_smi", lambda: None)
    cache = gd.GpuDetailsCache(runner=mock_runner)
    assert cache.refresh() is False
    assert len(calls) == 0

