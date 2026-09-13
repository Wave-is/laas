"""Bundled runtime plugin discovery; the control plane imports only this registry."""
import importlib
import re
from pathlib import Path
from ..storage import read_document

def load_adapters():
    adapters = {}
    for manifest in Path(__file__).parent.glob('*/manifest.yaml'):
        meta = read_document(manifest)
        module = manifest.parent.name
        if not re.fullmatch('[a-z_]+', module) or meta.get('enabled', True) is False:
            continue
        cls = getattr(importlib.import_module(f'{__name__}.{module}.adapter'), meta['class'])
        adapters[meta['id']] = cls(meta)
    return adapters
