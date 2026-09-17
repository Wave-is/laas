"""Independent text/fill channels selected by stable UUID or aggregation."""
import math
import re
from .i18n import tr

# Labels are translated once at import; keys are the stored values used by validation.
METRICS = {'temp': tr('Температура'), 'load': tr('Загрузка GPU'), 'vram': tr('Занято VRAM'), 'off': tr('Не показывать')}
STYLES = {'two_icons': tr('Две отдельные иконки'), 'dual_tile': tr('Два показателя в одной'),
          'single_peak': tr('Максимум по всем GPU'), 'single_avg': tr('Среднее по всем GPU'), 'logo': tr('Логотип Station')}
THEMES = {'dark_tile': tr('Тёмная плитка'), 'white_tile': tr('Светлая плитка'), 'afterburner': tr('Цифры без фона')}
COLORS = {'#79edc4': tr('Мятный'), '#48c6e5': tr('Голубой'), '#c4a5ff': tr('Лиловый'), '#ffc879': tr('Янтарный')}

def channels_for(settings):
    channels = settings.get('tray_channels')
    if channels:
        return [dict(c) for c in channels]
    return [{'source': f'auto:{i}', 'text_metric': settings.get(f'tray_metric_gpu{i}') or 'temp',
             'fill_metric': 'load', 'color': color} for i, color in enumerate(('#79edc4', '#48c6e5'))]

def validate_tray(settings):
    for key, choices in [('tray_style', STYLES), ('tray_theme', THEMES), ('tray_display_mode', METRICS)]:
        if key in settings and settings[key] not in choices:
            raise ValueError(tr('Недопустимое значение настройки {key}', key=key))
    channels = settings.get('tray_channels')
    if channels is None:
        return
    if not isinstance(channels, list) or len(channels) != 2:
        raise ValueError(tr('Для трея нужны ровно два показателя'))
    for c in channels:
        if not isinstance(c, dict) or not isinstance(c.get('source'), str) or not c['source']:
            raise ValueError(tr('Выберите GPU или сводный режим для каждого показателя трея'))
        if c.get('text_metric') not in METRICS or c.get('fill_metric') not in METRICS:
            raise ValueError(tr('Недопустимый показатель трея'))
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', str(c.get('color', ''))):
            raise ValueError(tr('Цвета трея должны быть в формате #RRGGBB'))

def metric_value(gpu, metric):
    if metric == 'off' or gpu is None:
        return None
    value = gpu.get({'temp': 'temp_c', 'load': 'load_percent', 'vram': 'vram_used_pct'}.get(metric, ''))
    if value is None and metric == 'vram':
        total, used = gpu.get('vram_total_mib'), gpu.get('vram_used_mib')
        if total and used is not None:
            value = 100 * used / total
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        return None
    return value

def channel_value(gpus, source, metric):
    if source in ('peak', 'average'):
        values = [v for g in gpus if (v := metric_value(g, metric)) is not None]
        return (sum(values) / len(values) if source == 'average' else max(values)) if values else None
    if source.startswith('auto:') and source[5:].isdigit():
        index = int(source[5:])
        gpu = gpus[index] if index < len(gpus) else None
    else:
        gpu = next((g for g in gpus if g.get('uuid') == source), None)
    return metric_value(gpu, metric)

def readouts(gpus, settings):
    channels = channels_for(settings)
    style = settings.get('tray_style', 'dual_tile')
    if style in ('single_peak', 'single_avg'):
        channels = [dict(channels[0], source='peak' if style == 'single_peak' else 'average',
                         text_metric=settings.get('tray_display_mode') or channels[0]['text_metric'])]
    return [dict(c, value=channel_value(gpus, c['source'], c['text_metric']),
                 fill=channel_value(gpus, c['source'], c['fill_metric'])) for c in channels]

def format_metric(value, metric):
    if metric == 'off':
        return ''
    return '—' if value is None else f'{value:.0f}' + ('°C' if metric == 'temp' else '%')

# Short lowercase names for tooltips: "GPU 0: загрузка 81% · видеопамять 40%".
METRIC_NAMES = {'temp': tr('температура'), 'load': tr('загрузка'), 'vram': tr('видеопамять')}

def source_label(source, gpus):
    if source == 'peak':
        return tr('Максимум GPU')
    if source == 'average':
        return tr('Среднее GPU')
    if source.startswith('auto:') and source[5:].isdigit():
        index = int(source[5:])
        gpu = gpus[index] if index < len(gpus) else None
    else:
        gpu = next((g for g in gpus if g.get('uuid') == source), None)
    if gpu is None:
        return tr('GPU недоступен')
    return 'GPU {}'.format(gpu.get('index', gpus.index(gpu)))

def describe_reading(reading, gpus):
    """One tooltip line naming the GPU and what the numbers and the fill actually show."""
    parts = []
    for metric, value in ((reading['text_metric'], reading['value']), (reading['fill_metric'], reading['fill'])):
        if metric in METRIC_NAMES and not any(m == metric for m, _ in parts):
            parts.append((metric, value))
    values = ' · '.join(f'{METRIC_NAMES[m]} {format_metric(v, m)}' for m, v in parts)
    label = source_label(reading['source'], gpus)
    return f'{label}: {values}' if values else label
