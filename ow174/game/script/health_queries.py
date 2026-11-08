"""104319 component51 virtual40/48/58 over explicit forty-eight pool records.

Original getters5BBC80/5BC080/5BC9B0 round each addition to float32. The
normal subset requires byte17 !=0 and byte18 ==0; an id-zero slot is absent.
Entity lookup and component allocation are separate host responsibilities.
"""

import struct

from ow174.game.script.expr import f32

GETTERS = {
    "STU_9B7648C3": (False, False),
    "STU_32E6E4D8": (False, True),
    "STU_7D7D2470": (True, True),
}


def pool_sum(records, *, maximum=False, normal_only=False):
    if len(records) != 48:
        raise ValueError("health queries require all 48 component51 pool slots")
    total = 0.0
    for record in records:
        if record is None:
            continue
        if len(record) != 20:
            raise ValueError("health queries require twenty-byte component51 pool records")
        if not struct.unpack_from("<I", record)[0]:
            continue
        if normal_only and (not record[17] or record[18]):
            continue
        value = struct.unpack_from("<f", record, 4 if maximum else 8)[0]
        total = f32(total + value)
    return total
