"""C743E0 component24 broadphase bounds getter; no actor/mover record is inferred."""

import struct
from dataclasses import dataclass


def _float(bits):
    if type(bits) is not int or not 0 <= bits < 1 << 32:
        raise ValueError("geometry field must be explicit float32 bits")
    return struct.unpack("<f", struct.pack("<I", bits))[0]


@dataclass(frozen=True)
class Record24Geometry:
    minimum_x_bits: int  # component24+20, used only as the validity comparison
    maximum_x_bits: int  # component24+30
    envelope: bytes  # component24+40..5F, distinct from current bounds at20/30


def broadphase24(record):
    """Native COMISS/JB fails unordered or maxX<minX; success copies opaque32-byte envelope."""
    if record is None:
        return None
    if not isinstance(record, Record24Geometry):
        raise ValueError("component24 geometry binding is unavailable")
    minimum, maximum = _float(record.minimum_x_bits), _float(record.maximum_x_bits)
    if not maximum >= minimum:
        return None
    if not isinstance(record.envelope, bytes) or len(record.envelope) != 32:
        raise ValueError("component24 broadphase envelope is unavailable")
    return record.envelope
