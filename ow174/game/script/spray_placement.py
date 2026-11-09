"""The 104319 STU_861D9015 wire fields for an already resolved spray placement."""

import math
import struct

from ow174.game.script.expr import f32, round_half_away

STATE_CLASS = "STU_861D9015"
CODEC = "861D9015"
POLE = 0.9998455047607422


def _quantize(angle: float, scale: float) -> int:
    return min(0xFFFF, max(0, 0x8000 + round_half_away(f32(f32(angle) * scale))))


def _direction(vector: tuple[float, float, float]) -> tuple[int, int]:
    """DB1050's two unsigned angles for a unit vector in native coordinates."""
    x, y, z = map(f32, vector)
    if not all(map(math.isfinite, (x, y, z))):
        raise ValueError("spray direction must be finite")
    yaw = math.atan2(x, z) if x or z else 0.0
    pitch = math.copysign(math.pi / 2, y) if abs(y) > POLE else math.asin(y)
    return _quantize(yaw, 10430.3779296875), _quantize(pitch, 20860.755859375)


def wire_fields(payload: dict) -> dict:
    """Encode a resolved direction, point and normal, or an explicit empty result."""
    if not payload:
        return {"a": 0}
    yaw, pitch = _direction(payload["direction"])
    normal_yaw, normal_pitch = _direction(payload["normal"])
    point = tuple(map(f32, payload["point"]))
    if len(point) != 3 or not all(map(math.isfinite, point)):
        raise ValueError("spray point must be a finite three-component position")
    x, y, z = struct.unpack("<3I", struct.pack("<3f", *point))
    entity = payload.get("entity", 0)
    extra = payload.get("extra", 0)
    fields = {
        "a": 1, "v0": yaw, "v1": pitch, "v2": x, "v3": y, "v4": z,
        "v5": normal_yaw, "v6": normal_pitch, "has_entity": int(bool(entity)),
        "g": int(bool(payload.get("g", False))),
    }
    if entity:
        if not 0x80000000 < entity <= 0xFFFFFFFF or entity & 0x1FFFFFFF in (0, 0x1FFFFFFF):
            raise ValueError("invalid spray attachment entity")
        fields.update(entity=entity, has_extra=int(bool(extra)))
        if extra:
            if not 0 < extra <= 0xFFFFFFFF:
                raise ValueError("invalid spray attachment extra value")
            fields["extra"] = extra
    return fields
