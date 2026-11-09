"""104319 HealthComponent getters over explicit component-51 pool records.

260F630's virtual40/48/58 are 5BBC80/5BC080/5BC9B0. Each sums
the 48 twenty-byte records in array order, rounding after every float32 add.
48/58 additionally require byte17 nonzero and byte18 zero. These are pool
flags, not inferred hero names or a guessed maximum-minus-damage formula.
"""

import struct

from ow174.game.script.expr import f32

GETTERS = {
    "STU_9B7648C3": (False, False),  # all current pools
    "STU_32E6E4D8": (False, True),  # normal current pools
    "STU_7D7D2470": (True, True),  # normal maximum pools
}


def pool_sum(records, *, maximum=False, normal_only=False):
    """Sum supplied wire-layout pools; None entries represent zero/absent ids."""
    if len(records) != 48:
        raise ValueError("health queries require all 48 component-51 pool slots")
    total = 0.0
    for record in records:
        if record is None:
            continue
        if len(record) != 20:
            raise ValueError("health queries require twenty-byte component-51 pool records")
        if not struct.unpack_from("<I", record)[0]:
            continue
        if normal_only and (not record[17] or record[18]):
            continue
        total = f32(total + struct.unpack_from("<f", record, 4 if maximum else 8)[0])
    return total
