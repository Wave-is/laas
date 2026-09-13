"""Independent text/fill indicators without inventing missing measurements."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from .config import config
from .branding import mark_image
from .tray_settings import readouts

class DynamicTrayRenderer:
    def __init__(self, size=64):
        self.size = size

    def render(self, gpus, display_mode=None, is_running=True, style=None,
               metric_gpu0=None, metric_gpu1=None, theme=None, settings=None, channel_index=None):
        prefs = dict(settings) if settings is not None else {key: config.get(key) for key in
            ('tray_style', 'tray_theme', 'tray_channels', 'tray_metric_gpu0', 'tray_metric_gpu1', 'tray_display_mode')}
        for key, value in [('tray_style', style), ('tray_theme', theme), ('tray_display_mode', display_mode),
                           ('tray_metric_gpu0', metric_gpu0), ('tray_metric_gpu1', metric_gpu1)]:
            if value is not None:
                prefs[key] = value
        if display_mode and display_mode.startswith('gpu') and display_mode[3:].isdigit():
            channel_index = int(display_mode[3:])
            prefs['tray_style'] = 'two_icons'
        style = prefs.get('tray_style') or 'dual_tile'
        if style == 'logo' or display_mode in ('logo', 'icon'):
            return self.render_logo(is_running)
        readings = readouts(gpus, prefs)
        if channel_index is not None:
            readings = readings[channel_index:channel_index+1]
        elif style == 'two_icons':
            readings = readings[:1]
        if not readings:
            return self.render_logo(is_running)
        size = self.size * 3
        image = Image.new('RGBA', (size, size))
        theme = prefs.get('tray_theme') or 'dark_tile'
        for index, reading in enumerate(readings):
            self._tile(image, reading, (0, index * size // len(readings), size,
                       (index + 1) * size // len(readings)), theme)
        draw = ImageDraw.Draw(image)
        draw.line((size*.2, size-3, size*.8, size-3), fill='#79edc4' if is_running else '#718198', width=4)
        return image.resize((self.size, self.size), Image.Resampling.LANCZOS)

    @staticmethod
    def _tile(image, reading, box, theme):
        x, y, right, bottom = box
        width, height = right-x, bottom-y
        mask = Image.new('L', image.size)
        ImageDraw.Draw(mask).rounded_rectangle((x+1, y+1, right-2, bottom-2),
            radius=min(width, height)*.13, fill=255)
        layer = Image.new('RGBA', image.size)
        draw = ImageDraw.Draw(layer)
        background = '#f1f5fa' if theme == 'white_tile' else '#122031'
        if theme != 'afterburner':
            draw.rectangle(box, fill=background)
        fill = reading['fill']
        if fill is not None:
            amount = max(0, min(100, fill)) / 100
            if amount > 0:
                color = reading['color']
                rgb = tuple(int(color[i:i+2], 16) for i in (1, 3, 5))
                overlay = Image.new('RGBA', image.size)
                ImageDraw.Draw(overlay).rectangle((x, bottom-height*amount, right, bottom), fill=(*rgb, 110))
                layer = Image.alpha_composite(layer, overlay)
        image.alpha_composite(Image.composite(layer, Image.new('RGBA', image.size), mask))
        draw = ImageDraw.Draw(image)
        text = '' if reading['text_metric'] == 'off' else ('—' if reading['value'] is None else str(round(reading['value'])))
        font_size = int(height * .92)
        while True:
            try:
                font = ImageFont.truetype(str(Path('C:/Windows/Fonts/segoeuib.ttf')), font_size)
            except OSError:
                font = ImageFont.load_default(size=font_size)
            bounds = draw.textbbox((0, 0), text, font=font)
            if bounds[2]-bounds[0] <= width*.91 or font_size <= 8:
                break
            font_size -= 2
        color = '#101c2b' if theme == 'white_tile' else '#f4fbff'
        if theme == 'afterburner':
            color = reading['color']
        draw.text((x+(width-bounds[2]+bounds[0])/2-bounds[0],
                   y+(height-bounds[3]+bounds[1])/2-bounds[1]-height*.025), text,
                  font=font, fill=color, stroke_width=1 if theme == 'afterburner' else 0, stroke_fill='#101c2b')

    def render_logo(self, is_running=True):
        image = mark_image(self.size)
        draw = ImageDraw.Draw(image)
        r = max(2, self.size // 13)
        x, y = self.size-r-2, self.size-r-2
        draw.ellipse((x-r, y-r, x+r, y+r), fill='#79edc4' if is_running else '#718198', outline='#101c2b', width=1)
        return image

renderer = DynamicTrayRenderer()
