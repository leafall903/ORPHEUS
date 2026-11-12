"""C724B0/C73160 and C72220/C73000 queries over explicitly supplied spatial data.

No world manager, category membership, actor ownership or second-root data is
inferred. Returned payloads still require the full target acceptance pipeline.
"""

import math
import struct
from dataclasses import dataclass

from .expr import f32


def temporal_tick(seconds):
    """C75C5A..C75CF5 binary64 seconds to uint64 microseconds, including CVTT sentinel."""
    if type(seconds) is not float:
        raise ValueError("temporal seconds must be explicit binary64")
    value = seconds * 1000000.0
    offset, threshold = 0, float(1 << 63)
    if value >= threshold:
        value -= threshold
        if value < threshold:
            offset = 1 << 63
    signed = (math.trunc(value) if math.isfinite(value) and -(1 << 63) <= value < (1 << 63)
              else -(1 << 63))
    return (signed + offset) & ((1 << 64) - 1)


def _rounded(value):
    try:
        return f32(value)
    except OverflowError:
        return math.copysign(math.inf, value)


def _vector(value, size):
    if not isinstance(value, tuple) or len(value) != size:
        raise ValueError("spatial vector is unavailable")
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in value):
        raise ValueError("spatial vector requires finite numbers")
    result = tuple(_rounded(v) for v in value)
    if not all(math.isfinite(v) for v in result):
        raise ValueError("spatial vector exceeds finite float32")
    return result


def _sphere(value):
    value = _vector(value, 4)
    if value[3] < 0:
        raise ValueError("spatial radius must be nonnegative")
    return value


def _mask(value):
    if type(value) is not int or not 0 <= value < 1 << 64:
        raise ValueError("spatial mask must be a native uint64")
    return value


def _negative(value):
    return bool(struct.unpack("<I", struct.pack("<f", value))[0] & 0x80000000)


def overlaps(minimum, maximum, query_minimum, query_maximum):
    """Native SIMD subtraction sign bits, including negative zero boundaries."""
    return not any(_negative(_rounded(qhi - lo)) or _negative(_rounded(hi - qlo))
                   for lo, hi, qlo, qhi in zip(minimum, maximum, query_minimum, query_maximum, strict=True))


def leaf_accepts(mask, sphere, query, any_bits, excluded_bits):
    """C73160 mask gates then strict float32 sphere overlap; no final actor filter."""
    mask, any_bits, excluded_bits = _mask(mask), _mask(any_bits), _mask(excluded_bits)
    if not mask & any_bits or mask & excluded_bits:
        return False
    sphere, query = _sphere(sphere), _sphere(query)
    delta = tuple(_rounded(sphere[i] - query[i]) for i in range(3))
    squares = tuple(_rounded(v * v) for v in delta)
    distance = _rounded(_rounded(squares[0] + squares[1]) + squares[2])
    radius = _rounded(sphere[3] + query[3])
    return distance < _rounded(radius * radius)


@dataclass(frozen=True)
class SpatialLeaf:
    mask: int
    sphere: tuple
    payload: object


@dataclass(frozen=True)
class SpatialNode:
    minimum: tuple
    maximum: tuple
    children: tuple | None = None  # None is a leaf; pair is an internal node
    leaf: object = NotImplemented


@dataclass(frozen=True)
class SpatialTree:
    root: object = NotImplemented  # -1 declares a known empty tree
    nodes: tuple = ()

    def query_sphere(self, sphere, any_bits, excluded_bits):
        """C76220/C6FE50 first-root wrapper: derive bounds from the query sphere."""
        sphere = _sphere(sphere)
        minimum = tuple(f32(v - sphere[3]) for v in sphere[:3])
        maximum = tuple(f32(v + sphere[3]) for v in sphere[:3])
        return self.query(minimum, maximum, sphere, any_bits, excluded_bits)

    def query(self, minimum, maximum, sphere, any_bits, excluded_bits):
        """Native LIFO order: push left then right; preserve repeated leaf payloads."""
        minimum, maximum = _vector(minimum, 3), _vector(maximum, 3)
        sphere = _sphere(sphere)
        any_bits, excluded_bits = _mask(any_bits), _mask(excluded_bits)
        results = []
        for leaf in self._walk(minimum, maximum):
            if not isinstance(leaf, SpatialLeaf):
                raise ValueError("spatial leaf is unavailable")
            if leaf_accepts(leaf.mask, leaf.sphere, sphere, any_bits, excluded_bits):
                results.append(leaf.payload)
        return tuple(results)

    def _walk(self, minimum, maximum):
        if type(self.root) is not int:
            raise ValueError("spatial tree root is unavailable")
        if self.root == -1:
            return
        stack = [(self.root, frozenset())]
        while stack:
            index, ancestors = stack.pop()
            if type(index) is not int or not 0 <= index < len(self.nodes) or index in ancestors:
                raise ValueError("invalid spatial tree node or cycle")
            node = self.nodes[index]
            if not isinstance(node, SpatialNode):
                raise ValueError("spatial node is unavailable")
            lo, hi = _vector(node.minimum, 3), _vector(node.maximum, 3)
            if not overlaps(lo, hi, minimum, maximum):
                continue
            if node.children is None:
                yield node.leaf
            else:
                if not isinstance(node.children, tuple) or len(node.children) != 2:
                    raise ValueError("spatial internal node requires two children")
                path = ancestors | {index}
                stack.extend((child, path) for child in node.children)


@dataclass(frozen=True)
class TemporalLeaf:
    """C73000 payload record with its own broadphase bounds, distinct from tree bounds."""

    mask: int
    minimum: tuple
    maximum: tuple
    payload: object


@dataclass(frozen=True)
class TemporalQuery:
    """Explicit second-root context; native relationship/history services stay required."""

    sphere: tuple
    any_bits: int
    excluded_bits: int
    history_flag_bits: int
    first_tick: int
    second_tick: int
    subject: object

    def scan(self, tree, *, related, history):
        sphere = _sphere(self.sphere)
        any_bits = _mask(self.any_bits)
        excluded_bits = _mask(self.excluded_bits)
        history_flag_bits = _mask(self.history_flag_bits)
        first_tick, second_tick = _mask(self.first_tick), _mask(self.second_tick)
        minimum = tuple(f32(v - sphere[3]) for v in sphere[:3])
        maximum = tuple(f32(v + sphere[3]) for v in sphere[:3])
        results = []
        for leaf in tree._walk(minimum, maximum):
            if not isinstance(leaf, TemporalLeaf):
                raise ValueError("temporal spatial leaf is unavailable")
            mask = _mask(leaf.mask)
            if not mask & any_bits or mask & excluded_bits:
                continue
            lo, hi = _vector(leaf.minimum, 3), _vector(leaf.maximum, 3)
            if not overlaps(lo, hi, minimum, maximum):
                continue
            relationship = related(leaf.payload, self.subject)
            if type(relationship) is not bool:
                raise ValueError("temporal relationship service is unavailable")
            tick = second_tick if relationship else first_tick
            historical = history(leaf.payload, tick, not bool(history_flag_bits & mask))
            if historical is NotImplemented:
                raise ValueError("temporal sphere service is unavailable")
            # Native BD1BC0 false bypasses only this sphere test, not the later actor filter.
            if historical is None or leaf_accepts(1, historical, sphere, 1, 0):
                results.append(leaf.payload)
        return tuple(results)
