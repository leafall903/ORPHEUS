"""Optional external .053 status resources, kept separate from the base runtime.

This is resource loading only. Applying and retiring a status requires an
explicit gameplay host; finding its graph does not establish native semantics.
"""

import gzip
import json
import os
from functools import cache
from pathlib import Path


@cache
def load(path: str) -> dict:
    data = json.loads(gzip.decompress(Path(path).read_bytes()))
    if data.get("status_supplement_version") != 1 or data.get("target_build") != "1.74.0.0.104319":
        raise ValueError("status supplement must declare version1 and target build104319")
    records, definitions = data.get("graphs"), data.get("status_definitions")
    if not isinstance(records, list) or not isinstance(definitions, dict):
        raise ValueError("status supplement requires graphs and status definitions")
    indexes = set()
    for record in records:
        index = record.get("index") if isinstance(record, dict) else None
        if type(index) is not int or not 0 < index <= 0xFFFF or index in indexes:
            raise ValueError("invalid or duplicate status graph index")
        indexes.add(index)
    for key, value in definitions.items():
        if (not isinstance(key, str) or len(key) != 18 or not key.startswith("0x04A0")
                or not isinstance(value, dict) or value.get("$") != "STU_76F2C784"):
            raise ValueError("invalid .053 status definition")
        try:
            number = int(key, 16)
        except ValueError as exc:
            raise ValueError("invalid .053 status GUID") from exc
        if number & 0xFFFFFFFFFFFF == 0:
            raise ValueError("status definition must have a nonzero resource index")
    return data


def configured() -> dict | None:
    path = os.environ.get("ORPHEUS_STATUS_BUNDLE")
    return load(str(Path(path).resolve())) if path else None
