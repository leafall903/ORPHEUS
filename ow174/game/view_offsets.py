"""Immutable external104319 FirstPerson offsets, not native camera/bone execution."""

import hashlib
import json
import math
import os
import re
from pathlib import Path
from types import MappingProxyType

BUILD = "1.74.0.0.104319"


def read_catalog(raw):
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get('version')) is not int
            or data.get('version') != 1 or data.get('build') != BUILD):
        raise ValueError('view offsets require version1/build104319')
    bodies = data.get('bodies')
    if not isinstance(bodies, dict):
        raise ValueError('view offset bodies must be a mapping')
    result = {}
    for key, row in bodies.items():
        if not isinstance(key, str) or not re.fullmatch(r'0x0400[0-9A-F]{12}', key):
            raise ValueError('view offset body must be a full003 GUID')
        if not isinstance(row, dict) or set(row) != {'standing', 'crouched'}:
            raise ValueError('view offset body needs standing and crouched values')
        values = row['standing'], row['crouched']
        if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in values):
            raise ValueError('view offsets must be positive finite numbers')
        result[int(key, 16)] = tuple(float(v) for v in values)
    return MappingProxyType(result)


_path = os.environ.get('ORPHEUS_VIEW_OFFSETS')
_raw = Path(_path).read_bytes() if _path else None
CATALOG_SHA256 = hashlib.sha256(_raw).hexdigest() if _raw is not None else None
_CATALOG = read_catalog(_raw) if _raw is not None else MappingProxyType({})


def get(body):
    """Known resource pair, or unknown; Combat retains its declared legacy fallback."""
    return _CATALOG.get(body)
