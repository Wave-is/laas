"""Colors and small helpers shared by the control center and its page modules."""
BG = '#10161e'
PANEL = '#18222e'
EDGE = '#28394a'
TEXT = '#e6eef6'
MUTED = '#91a2b4'
ACCENT = '#56d6b1'
WARNING = '#f8ad88'


def number(value, suffix=''):
    return '—' if value is None else f'{value:g}{suffix}'
