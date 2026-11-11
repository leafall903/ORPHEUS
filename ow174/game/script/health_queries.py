"""104319 component51 getters over explicit twenty-byte pool records.

Reuses the independently inspected Bastion-lane getter semantics: table260F630,
virtual40/48/58 ->5BBC80/5BC080/5BC9B0. Entity/cache binding remains a host service.
"""

import struct

from ow174.game.script.expr import f32

GETTERS = {
    "STU_9B7648C3": (False, False),
    "STU_32E6E4D8": (False, True),
    "STU_7D7D2470": (True, True),
}


def pool_sum(records, *, maximum=False, normal_only=False):
    """Native array order and float32 rounding; flags17/18 select normal pools."""
    if len(records) != 48:
        raise ValueError("health query requires all48 component51 pool slots")
    total = 0.0
    for record in records:
        if record is None:
            continue
        if not isinstance(record, bytes) or len(record) != 20:
            raise ValueError("health query requires twenty-byte component51 pool records")
        if not struct.unpack_from("<I", record)[0]:
            continue
        if normal_only and (not record[17] or record[18]):
            continue
        total = f32(total + struct.unpack_from("<f", record, 4 if maximum else 8)[0])
    return total
