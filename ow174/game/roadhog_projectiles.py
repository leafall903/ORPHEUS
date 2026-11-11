"""Known 104319 Scrap Gun definitions. Both carry 02F9 and omit 0145."""

import math

WHOLE_HOG = 0x0400000000000405

SCRAP = frozenset((0x0400000000000295, 0x040000000000029A))


def _basis(forward):
    size = math.hypot(forward[0], forward[2])
    right = (forward[2] / size, 0.0, -forward[0] / size) if size > 1e-9 else (1.0, 0.0, 0.0)
    up = (forward[1] * right[2], forward[2] * right[0] - forward[0] * right[2],
          -forward[1] * right[0])
    return right, up, forward


def reorient(vectors, before, after):
    """Carry the authored child cone through a reconstructed reflected aim."""
    old, new = _basis(before), _basis(after)
    result = []
    for vector in vectors:
        local = [sum(a * b for a, b in zip(vector, axis, strict=True)) for axis in old]
        result.append(tuple(sum(k * axis[i] for k, axis in zip(local, new, strict=True))
                            for i in range(3)))
    return tuple(result)
