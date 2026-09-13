"""Generate the owned, reproducible Station logo assets (no fonts needed)."""
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from src.branding import mark_image, mark_svg

target = ROOT / 'assets/brand'
target.mkdir(parents=True, exist_ok=True)
(target / 'station.svg').write_text(mark_svg(), encoding='utf-8')
mark_image(512).save(target / 'station.png')
mark_image(256).save(target / 'station.ico', sizes=[(s, s) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256)])
print(target)
