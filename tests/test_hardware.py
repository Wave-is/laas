from src.hardware import hardware

def test_hardware_detection():
    count=hardware.get_gpu_count()
    assert count>=0
    gpus=hardware.query_all_gpus()
    assert len(gpus)==count
    assert len({g['uuid'] for g in gpus})==count
    for g in gpus:
        assert {'uuid','name','temp_c','driver_mode'} <= g.keys()
        assert g['vram_total_mib'] is None or g['vram_total_mib']>=0

def test_summary_metrics():
    summary=hardware.get_summary_metrics()
    assert summary['gpu_count']>=0
    assert summary['peak_temp'] is None or summary['peak_temp']>=0
    assert summary['total_vram_pct'] is None or 0<=summary['total_vram_pct']<=100

def test_cpu_only_summary_does_not_invent_metrics(monkeypatch):
    monkeypatch.setattr(hardware,'query_all_gpus',lambda:[])
    summary=hardware.get_summary_metrics()
    assert summary['gpu_count']==0 and summary['peak_temp'] is None and summary['total_vram_pct'] is None
