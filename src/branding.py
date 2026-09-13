"""Original Station vector mark; shared geometry for SVG, ICO and UI."""
from PIL import Image, ImageDraw

BACKGROUND = '#101c2b'
SHAPES = (
    ('#79edc4', ((42, 196), (109, 60), (141, 60), (76, 196))),
    ('#48c6e5', ((141, 60), (214, 196), (176, 196), (124, 96))),
    ('#79edc4', ((96, 152), (159, 152), (174, 181), (82, 181))),
)

def mark_image(size=256):
    scale = max(256, size * 3)
    image = Image.new('RGBA', (scale, scale))
    draw = ImageDraw.Draw(image)
    k = scale / 256
    draw.rounded_rectangle((0, 0, scale - 1, scale - 1), radius=52*k, fill=BACKGROUND)
    for color, points in SHAPES:
        draw.polygon([(x*k, y*k) for x, y in points], fill=color)
    return image.resize((size, size), Image.Resampling.LANCZOS)

def mark_svg():
    polygons = ''.join(f'<polygon fill="{color}" points="' +
        ' '.join(f'{x},{y}' for x, y in points) + '"/>' for color, points in SHAPES)
    return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" role="img" '
        'aria-label="Local Agent AI Station"><title>Local Agent AI Station</title>'
        f'<rect width="256" height="256" rx="52" fill="{BACKGROUND}"/>{polygons}</svg>\n')
