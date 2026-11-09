"""Immutable external003 target tags;0145 exclusion is a reconstructed policy.

An absent definition is unknown, not an implicitly empty tag array. The native
.097 profile/filter evaluator and dynamic target tags remain unsupported.
"""

import hashlib
import json
import os
import re
from pathlib import Path
from types import MappingProxyType

BUILD = "1.74.0.0.104319"
EXCLUDED_TAG = 0x0D60000000000145


def read_catalog(raw):
    data = json.loads(raw)
    if (not isinstance(data, dict) or type(data.get("version")) is not int
            or data.get("version") != 1 or data.get("build") != BUILD):
        raise ValueError("projectile tags require version1/build104319")
    entities = data.get("entities")
    if not isinstance(entities, dict):
        raise ValueError("projectile tag entities must be a mapping")
    result = {}
    for key, values in entities.items():
        if not isinstance(key, str) or not re.fullmatch(r"0x0400[0-9A-F]{12}", key):
            raise ValueError("projectile tag entity must be a full003 GUID")
        if (not isinstance(values, list) or any(not isinstance(tag, str)
                or not re.fullmatch(r"0x0D60[0-9A-F]{12}", tag) for tag in values)
                or len(values) != len(set(values))):
            raise ValueError("invalid projectile target tags")
        result[int(key, 16)] = frozenset(int(value, 16) for value in values)
    return MappingProxyType(result)


_path = os.environ.get("ORPHEUS_PROJECTILE_TAGS")
_raw = Path(_path).read_bytes() if _path else None
CATALOG_SHA256 = hashlib.sha256(_raw).hexdigest() if _raw is not None else None
_CATALOG = read_catalog(_raw) if _raw is not None else MappingProxyType({})


def absorbable(definition):
    known = _CATALOG.get(definition)
    return known is not None and EXCLUDED_TAG not in known
