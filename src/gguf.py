"""GGUF header reader and VRAM estimate.

Only the file header is read: the metadata key/value section and, optionally, the tensor
descriptors (names and shapes) that follow it to count parameters. Tensor data is never read,
so a 30 GB model is inspected in milliseconds.
"""
import math
import os
import re
import struct
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .i18n import tr

MAGIC = b'GGUF'
MIB = 1024 * 1024
# Arrays longer than this (tokenizer vocabularies) are skipped; only their length is kept.
MAX_ARRAY_ITEMS = 1024
MAX_STRING = 16 * MIB
MAX_KV_COUNT = 1_000_000
MAX_TENSORS = 1_000_000

_SCALARS = {0: '<B', 1: '<b', 2: '<H', 3: '<h', 4: '<I', 5: '<i', 6: '<f', 7: '<?', 10: '<Q', 11: '<q', 12: '<d'}
STRING, ARRAY = 8, 9

# llama_ftype values stored in general.file_type.
FILE_TYPES = {0: 'F32', 1: 'F16', 2: 'Q4_0', 3: 'Q4_1', 7: 'Q8_0', 8: 'Q5_0', 9: 'Q5_1', 10: 'Q2_K', 11: 'Q3_K_S',
    12: 'Q3_K_M', 13: 'Q3_K_L', 14: 'Q4_K_S', 15: 'Q4_K_M', 16: 'Q5_K_S', 17: 'Q5_K_M', 18: 'Q6_K', 19: 'IQ2_XXS',
    20: 'IQ2_XS', 21: 'Q2_K_S', 22: 'IQ3_XS', 23: 'IQ3_XXS', 24: 'IQ1_S', 25: 'IQ4_NL', 26: 'IQ3_S', 27: 'IQ3_M',
    28: 'IQ2_S', 29: 'IQ2_M', 30: 'IQ4_XS', 31: 'IQ1_M', 32: 'BF16', 36: 'TQ1_0', 37: 'TQ2_0', 38: 'MXFP4_MOE'}

# Bytes per cached element for llama.cpp KV cache types (block size / elements per block).
KV_BYTES = {'f32': 4.0, 'f16': 2.0, 'bf16': 2.0, 'q8_0': 34 / 32, 'q5_1': 24 / 32, 'q5_0': 22 / 32,
    'q4_1': 20 / 32, 'q4_0': 18 / 32, 'iq4_nl': 18 / 32}
KV_TYPES = ('f16', 'q8_0', 'q4_0')

QUANT_PATTERN = re.compile(r'(?<![A-Za-z0-9])((?:UD-)?(?:I?Q\d(?:_[A-Z0-9]+)*|F16|F32|BF16|MXFP4(?:_MOE)?))(?![A-Za-z0-9])', re.I)
SPLIT_PATTERN = re.compile(r'-(\d{5})-of-(\d{5})\.gguf$', re.I)


class GgufError(ValueError):
    pass


@dataclass
class GgufInfo:
    path: str
    version: int = 0
    tensor_count: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)
    parameter_count: Optional[int] = None

    def arch_value(self, key, default=None):
        return self.metadata.get(f'{self.architecture}.{key}', default)

    @property
    def architecture(self):
        return str(self.metadata.get('general.architecture') or '')

    @property
    def name(self):
        return str(self.metadata.get('general.name') or '')

    @property
    def size_label(self):
        return str(self.metadata.get('general.size_label') or '')

    @property
    def file_type(self):
        value = self.metadata.get('general.file_type')
        return FILE_TYPES.get(value, '' if value is None else str(value))

    @property
    def context_length(self):
        return _int(self.arch_value('context_length'))

    @property
    def block_count(self):
        return _int(self.arch_value('block_count'))

    @property
    def embedding_length(self):
        return _int(self.arch_value('embedding_length'))

    @property
    def head_count(self):
        return _first_int(self.arch_value('attention.head_count'))

    @property
    def head_count_kv(self):
        """Per-layer KV head counts (list) or one number for every layer."""
        value = self.arch_value('attention.head_count_kv')
        return value if isinstance(value, list) else _int(value) if value is not None else self.head_count

    @property
    def key_length(self):
        value = _int(self.arch_value('attention.key_length'))
        if value:
            return value
        return self.embedding_length // self.head_count if self.embedding_length and self.head_count else None

    @property
    def value_length(self):
        return _int(self.arch_value('attention.value_length')) or self.key_length

    @property
    def is_mmproj(self):
        return self.architecture == 'clip' or 'mmproj' in Path(self.path).name.lower()

    @property
    def split_count(self):
        return _int(self.metadata.get('split.count')) or 1

    def kv_elements_per_token(self):
        """Cached K+V elements per token over all attention layers; None when metadata is insufficient."""
        layers = self.block_count
        if not layers:
            return None
        rank = _int(self.arch_value('attention.kv_lora_rank'))
        if rank:  # Multi-head latent attention (DeepSeek-style): one compressed vector per layer.
            return layers * (rank + (_int(self.arch_value('rope.dimension_count')) or 0))
        key, value = self.key_length, self.value_length
        if not key or not value:
            return None
        heads = self.head_count_kv
        if isinstance(heads, list):
            per_layer = [_int(h) or 0 for h in heads[:layers]]
        else:
            per_layer = [heads or 0] * layers
        interval = _int(self.arch_value('full_attention_interval'))
        if interval and interval > 1:  # Hybrid models: only every n-th layer keeps a KV cache.
            per_layer = [h if (i + 1) % interval == 0 else 0 for i, h in enumerate(per_layer)]
        return sum(per_layer) * (key + value)

    def summary(self):
        heads = self.head_count_kv
        return {
            'architecture': self.architecture, 'name': self.name, 'size_label': self.size_label,
            'parameter_count': self.parameter_count, 'file_type': self.file_type,
            'context_length': self.context_length, 'block_count': self.block_count,
            'embedding_length': self.embedding_length, 'head_count': self.head_count,
            'head_count_kv': max(heads) if isinstance(heads, list) and heads else heads,
            'key_length': self.key_length, 'value_length': self.value_length,
        }


def _int(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)) and math.isfinite(value):
        return int(value)
    return None


def _first_int(value):
    if isinstance(value, list):
        values = [_int(v) for v in value if _int(v)]
        return max(values) if values else None
    return _int(value)


class _Reader:
    def __init__(self, handle):
        self.handle = handle

    def read(self, size):
        data = self.handle.read(size)
        if len(data) != size:
            raise GgufError(tr('Файл GGUF обрезан или повреждён'))
        return data

    def unpack(self, fmt):
        return struct.unpack(fmt, self.read(struct.calcsize(fmt)))[0]

    def string(self, length_format):
        length = self.unpack(length_format)
        if length > MAX_STRING:
            raise GgufError(tr('Файл GGUF содержит слишком длинную строку'))
        return self.read(length).decode('utf-8', errors='replace')

    def skip(self, size):
        self.handle.seek(size, os.SEEK_CUR)

    def value(self, kind, length_format, keep=True):
        if kind in _SCALARS:
            return self.unpack(_SCALARS[kind])
        if kind == STRING:
            if keep:
                return self.string(length_format)
            self.skip(self.unpack(length_format))
            return None
        if kind == ARRAY:
            item_kind = self.unpack('<I')
            count = self.unpack(length_format)
            keep_items = keep and count <= MAX_ARRAY_ITEMS
            if item_kind in _SCALARS and not keep_items:
                self.skip(struct.calcsize(_SCALARS[item_kind]) * count)
                return {'array_length': count}
            items = [self.value(item_kind, length_format, keep_items) for _ in range(count)]
            return items if keep_items else {'array_length': count}
        raise GgufError(tr('Неизвестный тип значения GGUF: {kind}', kind=kind))


def read_gguf(path, count_parameters=True):
    """Parse the GGUF header of path. Raises GgufError for files that are not GGUF."""
    with open(path, 'rb') as handle:
        reader = _Reader(handle)
        if reader.read(4) != MAGIC:
            raise GgufError(tr('Это не файл GGUF: {path}', path=path))
        version = reader.unpack('<I')
        if version not in (1, 2, 3):
            raise GgufError(tr('Неподдерживаемая версия GGUF: {version}', version=version))
        length_format = '<I' if version == 1 else '<Q'
        tensor_count = reader.unpack(length_format)
        kv_count = reader.unpack(length_format)
        if kv_count > MAX_KV_COUNT or tensor_count > MAX_TENSORS:
            raise GgufError(tr('Файл GGUF повреждён: недопустимое число записей'))
        metadata = {}
        for _ in range(kv_count):
            key = reader.string(length_format)
            metadata[key] = reader.value(reader.unpack('<I'), length_format)
        info = GgufInfo(str(path), version, tensor_count, metadata)
        count = _int(metadata.get('general.parameter_count'))
        if count is None and count_parameters:
            try:
                total = 0
                for _ in range(tensor_count):
                    reader.skip(reader.unpack(length_format))  # name
                    dims = reader.unpack('<I')
                    if dims > 8:
                        raise GgufError(tr('Файл GGUF повреждён: недопустимое описание тензора'))
                    size = 1
                    for _ in range(dims):
                        size *= reader.unpack(length_format)
                    reader.skip(4 + 8)  # type, offset
                    total += size
                count = total if total else None
            except GgufError:
                count = None
        info.parameter_count = count
        return info


_cache = {}
_cache_lock = threading.Lock()


def read_gguf_cached(path):
    """read_gguf memoised by (path, size, mtime); returns None when the file is missing or unreadable."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (os.path.normcase(os.path.abspath(path)), stat.st_size, stat.st_mtime_ns)
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        info = read_gguf(path)
    except (OSError, GgufError):
        info = None
    with _cache_lock:
        _cache[key] = info
    return info


def split_parts(path):
    """All parts of a split model (model-00001-of-00003.gguf ...) or just [path]."""
    path = Path(path)
    match = SPLIT_PATTERN.search(path.name)
    if not match:
        return [path]
    total = int(match.group(2))
    prefix = path.name[:match.start()]
    return [path.with_name(f'{prefix}-{i:05d}-of-{total:05d}.gguf') for i in range(1, total + 1)]


def weights_size(path):
    """Bytes of the model file, including every part of a split model that exists."""
    size = 0
    for part in split_parts(path):
        try:
            size += part.stat().st_size
        except OSError:
            pass
    return size


def quant_from_name(filename, info=None):
    stem = Path(filename).name
    stem = SPLIT_PATTERN.sub('', stem)
    stem = re.sub(r'\.gguf$', '', stem, flags=re.I)
    matches = QUANT_PATTERN.findall(stem)
    if matches:
        return matches[-1].upper()
    return info.file_type if info else ''


def format_parameters(count):
    if not count:
        return '—'
    if count >= 1e9:
        return f'{count / 1e9:.1f}B'
    return f'{count / 1e6:.0f}M'


def kv_cache_bytes(info, context, kv_type='f16'):
    elements = info.kv_elements_per_token() if info else None
    if elements is None:
        return None
    return int(elements * max(1, context) * KV_BYTES.get((kv_type or 'f16').lower(), 2.0))


@dataclass
class VramEstimate:
    weights_mib: int
    mmproj_mib: int
    kv_mib: Optional[int]
    overhead_mib: int

    @property
    def total_mib(self):
        return self.weights_mib + self.mmproj_mib + (self.kv_mib or 0) + self.overhead_mib

    @property
    def complete(self):
        return self.kv_mib is not None


# Compute buffers (llama.cpp with flash attention) plus CUDA context per device, in MiB.
COMPUTE_BASE_MIB = 768
PER_GPU_MIB = 384


def estimate_vram(weights_bytes, info, context, kv_type='f16', mmproj_bytes=0, gpu_count=1):
    kv = kv_cache_bytes(info, context, kv_type)
    context_margin = int(context / 1024 * 2)  # compute buffers grow slowly with context
    return VramEstimate(
        weights_mib=math.ceil((weights_bytes or 0) / MIB),
        mmproj_mib=math.ceil((mmproj_bytes or 0) / MIB),
        kv_mib=None if kv is None else math.ceil(kv / MIB),
        overhead_mib=COMPUTE_BASE_MIB + PER_GPU_MIB * max(1, gpu_count) + context_margin)


FITS, TIGHT, NO_FIT, UNKNOWN = 'fits', 'tight', 'no_fit', 'unknown'


@dataclass
class FitResult:
    verdict: str
    need_mib: int
    total_mib: Optional[int]
    free_mib: Optional[int]
    gpu_count: int

    def label(self):
        return {FITS: tr('Помещается'), TIGHT: tr('Впритык'), NO_FIT: tr('Не помещается'),
                UNKNOWN: tr('Неизвестно')}[self.verdict]

    def details(self):
        if self.total_mib is None:
            return tr('нужно ≈{need} GiB; объём VRAM неизвестен', need=gib(self.need_mib))
        text = tr('нужно ≈{need} GiB из {total} GiB на {count} GPU', need=gib(self.need_mib), total=gib(self.total_mib), count=self.gpu_count)
        if self.free_mib is not None and self.free_mib < self.need_mib <= self.total_mib:
            text += ' · ' + tr('сейчас свободно {free} GiB', free=gib(self.free_mib))
        return text


def gib(mib):
    return f'{(mib or 0) / 1024:.1f}'


TIGHT_RATIO = 0.92


def fit_verdict(estimate, devices):
    """Compare an estimate with the capacity of devices (objects with vram_total_mib/vram_free_mib).

    Capacity is the whole memory of the cards: another model loaded right now frees its memory when
    it is swapped out. Free memory is reported separately.
    """
    need = estimate.total_mib
    devices = list(devices or [])
    totals = [d.vram_total_mib for d in devices]
    if not devices or any(t is None for t in totals):
        return FitResult(UNKNOWN, need, None, None, len(devices))
    total = sum(totals)
    frees = [d.vram_free_mib for d in devices]
    free = None if any(f is None for f in frees) else sum(frees)
    if need > total:
        verdict = NO_FIT
    elif not estimate.complete:
        verdict = UNKNOWN
    elif need > total * TIGHT_RATIO:
        verdict = TIGHT
    else:
        verdict = FITS
    return FitResult(verdict, need, total, free, len(devices))
