import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from src.tray_renderer import renderer
from PIL import Image

def test_tray_render_temp():
    sample_gpus = [
        {"temp_c": 56, "load_percent": 30, "vram_used_mib": 14000, "vram_total_mib": 24000},
        {"temp_c": 54, "load_percent": 25, "vram_used_mib": 14000, "vram_total_mib": 24000}
    ]
    img = renderer.render(sample_gpus, display_mode="temp")
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)
    assert img.mode == "RGBA"

def test_tray_render_load():
    sample_gpus = [{"temp_c": 75, "load_percent": 95, "vram_used_mib": 20000, "vram_total_mib": 24000}]
    img = renderer.render(sample_gpus, display_mode="load")
    assert isinstance(img, Image.Image)
    assert img.size == (64, 64)
