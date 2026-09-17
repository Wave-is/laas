from copy import deepcopy
from types import SimpleNamespace
import queue
import sys
import pytest
from src.tray_settings import channel_value, metric_value, readouts, validate_tray
from src.tray_renderer import DynamicTrayRenderer

GPUS = [{'uuid': 'GPU-a', 'temp_c': 40, 'load_percent': 0, 'vram_used_mib': 25, 'vram_total_mib': 100},
        {'uuid': 'GPU-b', 'temp_c': 70, 'load_percent': 100, 'vram_used_pct': 75},
        {'uuid': 'intel', 'temp_c': None, 'load_percent': None}]
PREFS = {'tray_style': 'two_icons', 'tray_theme': 'dark_tile', 'tray_display_mode': 'temp',
         'tray_channels': [dict(source='GPU-a', text_metric='temp', fill_metric='load', color='#79edc4'),
                           dict(source='GPU-b', text_metric='vram', fill_metric='temp', color='#48c6e5')]}

def test_channels_follow_uuid_and_keep_text_and_fill_independent():
    normal = readouts(GPUS, PREFS)
    reordered = readouts(list(reversed(GPUS)), PREFS)
    assert normal == reordered
    assert (normal[0]['value'], normal[0]['fill']) == (40, 0)
    assert (normal[1]['value'], normal[1]['fill']) == (75, 70)
    assert readouts(GPUS[:1], PREFS)[1]['value'] is None

def test_aggregate_ignores_unavailable_values_and_supports_many_gpus():
    assert channel_value(GPUS, 'average', 'temp') == 55
    assert channel_value(GPUS, 'peak', 'load') == 100
    assert channel_value([], 'peak', 'temp') is None
    assert metric_value(GPUS[0], 'vram') == 25
    assert metric_value({'temp_c': float('nan')}, 'temp') is None
    assert channel_value([{'temp_c': v} for v in range(8)], 'peak', 'temp') == 7

@pytest.mark.parametrize('style', ['two_icons', 'dual_tile', 'single_peak', 'single_avg', 'logo'])
def test_layouts_handle_zero_one_and_many_gpus(style):
    renderer = DynamicTrayRenderer(size=32)
    for gpus in ([], GPUS[:1], GPUS, GPUS * 3):
        for theme in ('dark_tile', 'white_tile', 'afterburner'):
            prefs = dict(PREFS, tray_style=style, tray_theme=theme)
            image = renderer.render(gpus, settings=prefs)
            assert image.size == (32, 32) and image.mode == 'RGBA'
            assert image.getbbox() is not None

def test_fill_changes_background_without_changing_numeric_value():
    renderer = DynamicTrayRenderer(size=64)
    low = renderer.render(GPUS, settings=PREFS, channel_index=0)
    high_gpus = deepcopy(GPUS)
    high_gpus[0]['load_percent'] = 100
    high = renderer.render(high_gpus, settings=PREFS, channel_index=0)
    assert low.getpixel((5, 45)) != high.getpixel((5, 45))
    assert readouts(GPUS, PREFS)[0]['value'] == readouts(high_gpus, PREFS)[0]['value']
    unknown = renderer.render([], settings=PREFS, channel_index=0)
    zero = renderer.render([dict(GPUS[0], temp_c=0)], settings=PREFS, channel_index=0)
    assert unknown.tobytes() != zero.tobytes()

def test_invalid_tray_preferences_do_not_silently_become_valid():
    validate_tray(PREFS)
    bad = deepcopy(PREFS)
    bad['tray_channels'][0]['color'] = 'invalid'
    with pytest.raises(ValueError):
        validate_tray(bad)
    with pytest.raises(ValueError):
        validate_tray(dict(PREFS, tray_channels=[]))

@pytest.mark.skipif(sys.platform != 'win32', reason='Windows native tray menu')
def test_native_menu_queues_actions_without_executing_gpu_changes(monkeypatch):
    from src.ui.tray_controls import TrayControls
    from src.profile_storage import profile_storage
    class Harness(TrayControls):
        pass
    harness = Harness()
    harness.events = queue.Queue()
    harness.busy = False
    harness.topology = None
    harness.ready_model_ids = set()
    harness.ready_model_names = []
    harness.controller = SimpleNamespace(adapters={}, frontends={})
    monkeypatch.setattr('src.ui.tray_controls.shared_services.profiles', lambda: {'test': {'name': 'Worker'}})
    menu = harness._tray_menu()
    entries = {item.text: item for item in menu.items}
    assert {'Модель', 'Все GPU в WDDM', 'Все GPU в TCC', 'Первая GPU в WDDM, остальные в TCC', 'Службы', 'Вид значка'} <= entries.keys()
    entries['Модель'].submenu.items[-1](None)
    assert harness.events.get_nowait() == ('tray_action', ('model', 'none'), None)
    entries['Первая GPU в WDDM, остальные в TCC'](None)
    kind, (action, profile_id), _ = harness.events.get_nowait()
    assert kind == 'tray_action' and action == 'gpu' and profile_id == 'gpu-first-wddm-rest-tcc' and profile_id in profile_storage.gpu_profiles
    next(item for item in entries['Службы'].submenu.items if item.text == 'Worker').submenu.items[0](None)
    assert harness.events.get_nowait() == ('tray_action', ('service_start', 'test'), None)
