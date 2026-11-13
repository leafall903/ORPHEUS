"""One-frame velocity consumer, 104319 C31870/C32860/C329C0.

Stationary world frame, Y up. Numeric/vector queues use C2EC10/C2ED10's
same-frame overwrite and C2FAA0/C2FCC0 ordering. This does not implement the
Wall Ride controller or the engine's persistent modifier retirement storage.
"""

import math
from dataclasses import dataclass, replace

from ow174.game.mover import ADD, MULTIPLY, SET, f32
from ow174.game.script.modifiers import insertion_index

TINY = 1.1920928955078125e-5
PROJECTED_MIN_SQUARE = 1.1754943508222875e-36  # original RVA25E1860, compared at C31FA6


@dataclass(frozen=True)
class Change:
    frame: int
    index: int  # numeric0..3, or vector request index-1 (host tag, not a native slot)
    op: int
    value: float | tuple
    key: int | None = None
    priority: float = 0.0
    mode: int = 1  # internal queue0 world SET,1 delta,4 deferred projection floor

    @property
    def start(self):
        return self.frame  # insertion_index compares the native start-frame field


def enqueue(changes, change):
    """Upsert one kind-3 request in its separate numeric/vector subarray.

    Unkeyed records are direct consumer fixtures and cannot enter this store.
    Same-key replacement preserves position even if its priority changes.
    """
    if (type(change) is not Change or type(change.key) is not int
            or not 0 <= change.key < 1 << 64 or not math.isfinite(change.priority)):
        raise ValueError("velocity queue requires a uint64 key and finite priority")
    if (change.mode not in (0, 1, 4)
            or (change.mode in (0, 4) and (change.index != -1 or change.op != SET))):
        raise ValueError("unsupported velocity queue mode or projected operation")
    try:
        change = replace(change, priority=f32(change.priority))
    except OverflowError as error:
        raise ValueError("velocity priority must fit float32") from error
    vector_kind = change.index == -1
    indexes = [i for i, row in enumerate(changes) if (row.index == -1) == vector_kind]
    if any(changes[i].key is None for i in indexes):
        raise ValueError("cannot combine keyed requests with direct consumer fixtures")
    for i in reversed(indexes):
        if changes[i].key == change.key:
            if changes[i].frame == change.frame:
                changes[i] = change
                return
            changes.pop(i)
            indexes = [j for j, row in enumerate(changes) if (row.index == -1) == vector_kind]
            break
    rows = [changes[i] for i in indexes]
    position = insertion_index(rows, change)
    changes.insert(indexes[position] if position < len(indexes) else len(changes), change)


def scalar(current, operand, operation):
    if operation == ADD:
        return f32(current + operand)
    if operation == MULTIPLY:
        return f32(current * operand)
    if operation == SET:
        return operand
    raise ValueError("unsupported velocity operation")


def consume(velocity, jump_speed, changes, frame):
    """Numeric slots32..35 in index order, then stored vector requests.

Input sequence is already ordered; only [frame, frame+1) rows act. Scalar3
operates on vertical/jump_speed, not on jump height. Vector index1 requests
have already been converted into full-vector deltas at enqueue time. Mode4
SET2 instead raises the requested projection using this consumption velocity.
"""
    value = tuple(map(f32, velocity))
    active = [change for change in changes if change.frame == frame]
    for index in range(4):
        rows = [change for change in active if change.index == index]
        if not rows:
            continue
        if index in (0, 1):
            axes = (0, 1, 2) if index == 0 else (0, 2)
            square = f32(sum(f32(value[i] * value[i]) for i in axes))
            norm = f32(math.sqrt(square))
            if norm <= TINY:
                continue
            adjusted = norm
        else:
            if index == 3 and jump_speed <= TINY:
                continue
            adjusted = f32(value[1] / jump_speed) if index == 3 else value[1]
        for change in rows:
            adjusted = scalar(adjusted, change.value, change.op)
        if index in (0, 1):
            scale = f32(adjusted / norm)
            value = tuple(f32(axis * scale) if i in axes else axis for i, axis in enumerate(value))
        else:
            vertical = f32(adjusted * jump_speed) if index == 3 else adjusted
            value = value[0], vertical, value[2]
    vectors = [change for change in active if change.index == -1]
    # Native C31BD0/C31CD8/C31F57 always use separate delta, world SET,
    # projection passes, including when the world SET pass has no records.
    # Stable sorting preserves the native queue's priority/key order per pass.
    vectors.sort(key=lambda change: {1: 0, 0: 1, 4: 2}[change.mode])
    for change in vectors:
        if change.index == -1:
            if change.mode == 4:
                requested = change.value
                square = f32(sum(f32(axis * axis) for axis in requested))
                if square < PROJECTED_MIN_SQUARE:
                    continue
                length = f32(math.sqrt(square))
                if length <= 0.0:
                    continue
                direction = tuple(f32(axis / length) for axis in requested)
                projection = f32(sum(f32(axis * unit) for axis, unit in zip(value, direction, strict=True)))
                gap = f32(length - projection)
                if gap > 0.0:
                    value = tuple(
                        f32(axis + f32(unit * gap)) for axis, unit in zip(value, direction, strict=True))
            elif change.mode == 0:
                value = change.value
            else:
                value = tuple(f32(axis + delta) for axis, delta in zip(value, change.value, strict=True))
    return value


def numeric(frame, index, operation, value):
    if type(frame) is not int or frame < 0 or type(index) is not int or not 0 <= index <= 3:
        raise ValueError("invalid velocity frame or numeric index")
    if operation not in (ADD, MULTIPLY, SET) or not math.isfinite(value):
        raise ValueError("unsupported velocity operation or value")
    return Change(frame, index, operation, f32(value))


def vector(frame, operation, requested, current):
    if type(frame) is not int or frame < 0 or operation not in (ADD, MULTIPLY, SET):
        raise ValueError("invalid velocity frame or vector operation")
    if len(requested) != 3 or len(current) != 3 or not all(map(math.isfinite, (*requested, *current))):
        raise ValueError("invalid velocity vector")
    delta = []
    for previous, operand in zip(current, requested, strict=True):
        previous, operand = f32(previous), f32(operand)
        target = scalar(previous, operand, operation)
        delta.append(f32(target - previous))
    return Change(frame, -1, ADD, tuple(delta))


def projected(frame, requested):
    """Mode4 SET2 retains the requested vector until C31F57-style consumption."""
    if type(frame) is not int or frame < 0 or len(requested) != 3 or not all(map(math.isfinite, requested)):
        raise ValueError("invalid projected velocity frame or vector")
    return Change(frame, -1, SET, tuple(map(f32, requested)), mode=4)


def world_set(frame, requested):
    """Authored enum4 maps through C329C0 to internal queue0/SET2, including zero."""
    if type(frame) is not int or frame < 0 or len(requested) != 3 or not all(map(math.isfinite, requested)):
        raise ValueError("invalid world SET velocity frame or vector")
    return Change(frame, -1, SET, tuple(map(f32, requested)), mode=0)
