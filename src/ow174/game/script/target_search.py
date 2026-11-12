"""104319 search routing and radial capsule predicates, not a complete search host.

C72D50/CAACF0 classify the two filter masks. C8E660's branch without angular
bounds tests radial inclusion before computing an approximate surface distance.
Enumeration, final filters, angular tests, output distances/order and VM writes
remain separate services. These primitives never create or select an entity.
"""

import math
from dataclasses import dataclass

from ow174.game.script import expr
from ow174.game.script.expr import f32

_DIRECT = frozenset((1, 0x40, 0x10000, 0x40000))
_MIXED = 0x400C1
_FILTER_MASKS = ("m_84065FD1", "m_1E14CCA8", "m_4EA686DB",
                 "m_27CF71D5", "m_66747E7D", "m_89831602")


def _mask(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("target-search mask must be uint64")
    return value


def category(first: int, second: int) -> int:
    """C72D50: preserve its priority and asymmetric handling of the two masks."""
    first, second = _mask(first), _mask(second)
    if second:
        if second & 1:
            return 1
        if second & 0x40000:
            return 0x40000
        if second & 0xC0:
            return 0x40
        if second & 0x10000:
            return 0x10000
    if first.bit_count() == 1:
        if first & 1:
            return 1
        if first & 0x40000:
            return 0x40000
        if first & 0xC0:
            return 0x40
    return 0


@dataclass(frozen=True)
class Route:
    categories: tuple[int, ...]
    spatial: bool


def route(config: dict | None) -> Route:
    """CAACF0 dispatch only; a routed candidate must still pass every final test."""
    if config is None:
        return Route((), True)
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("target-search route needs a filter config or known absence")
    first = _mask(config.get("m_84065FD1", 0))
    second = _mask(config.get("m_1E14CCA8", 0))
    direct = category(first, second)
    if direct in _DIRECT:
        return Route((direct,), False)
    return Route((0x150041,) if first & _MIXED else (), not first or bool(first & ~_MIXED))


@dataclass(frozen=True)
class Relationship:
    """Explicit inputs to BCAAE0 after the native relationship query.

    result is C1C690/C1CF60's Boolean, not a team-equality approximation.
    same records reference/candidate pointer identity independently of result.
    """

    same: bool
    result: bool

    def __post_init__(self):
        if type(self.same) is not bool or type(self.result) is not bool:
            raise ValueError("target-search relationship inputs must be known Booleans")


def mask_matches(config: dict, tags: int | None, *, relationship: Relationship | None) -> bool:
    """BCAAE0's six-mask gate, before the condition and other game predicates.

    None declares an absent component25/reference respectively. Unknown native
    tag or relationship services must not be represented as known absence.
    Native query selection, component97 gating and subscriptions are separate.
    """
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("target-search masks need an explicit filter config")
    tags = 0 if tags is None else _mask(tags)
    if relationship is not None and type(relationship) is not Relationship:
        raise ValueError("target-search relationship must be explicit or known absent")
    any_tag, all_tag, excluded_tag, any_relation, all_relation, excluded_relation = (
        _mask(config.get(field, 0)) for field in _FILTER_MASKS)
    accepted = bool(tags & any_tag) or any_tag == 0x3FFFFFFF
    complete = tags & all_tag == all_tag
    excluded = bool(tags & excluded_tag)
    if relationship is not None and any_relation | all_relation | excluded_relation:
        different = relationship.result and not relationship.same
        bits = (2 if different else 1) | (4 if relationship.same else 0)
        accepted |= bool(any_relation & bits)
        complete &= not bool(all_relation & (~bits & 7))
        excluded |= bool(excluded_relation & bits)
    return bool(accepted and complete and not excluded)


def candidate_masks(config: dict, tags: int | None, *, component97: bool,
                    relationship: Relationship | None) -> bool:
    """D0DA20's component97/mask stage after successful candidate lookup.

    Zero in all six masks bypasses BCAAE0, whose direct any-mask test would
    otherwise reject. The exact universal first mask bypasses component97.
    This does not apply the condition, auxiliary, world-rule or animation gates.
    """
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("target-search masks need an explicit filter config")
    if type(component97) is not bool:
        raise ValueError("target-search component97 presence must be known")
    masks = tuple(_mask(config.get(field, 0)) for field in _FILTER_MASKS)
    if masks[0] != 0x3FFFFFFF and not component97 and any(masks):
        return False
    if not any(masks):
        return True
    return mask_matches(config, tags, relationship=relationship)


@dataclass(frozen=True)
class CandidateParameter:
    """1B8D810 supplies only builtin0008, as a typed entity even for invalid IDs."""

    entity: int

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError("target-search candidate must be uint32")

    def lookup_guid(self, key, *, initial=None):
        if type(key) is int and key == 0x0A40000000000008:
            return True, expr.Entity(self.entity)
        return False, initial

    def lookup_parameter(self, identifier, *, initial=None):
        key = 0x0A40000000000008 if type(identifier) is int and identifier == 8 else 0
        return self.lookup_guid(key, initial=initial)


def condition(config: dict, instance, candidate: int, *, watcher=None) -> bool:
    """1B8D970's authored condition stage, not the preceding game filter gates.

    A valid candidate temporarily shadows builtin0008 across this component's
    instances. Invalid candidates leave existing providers visible. The native
    boolean evaluator's failure default is true; an evaluated null is false.
    """
    if type(config) is not dict or config.get("$") != "STUConfigVarFilter":
        raise ValueError("target-search condition needs an explicit filter config")
    provider = CandidateParameter(candidate)
    cfg = config.get("m_condition")
    if cfg is None:
        return True
    if instance is None:
        return False
    component = instance.component
    with component.operation():
        if expr.valid_entity(candidate):
            with component.parameter_scope(provider):
                value = instance.evaluate(cfg, watcher, initial=True)
        else:
            value = instance.evaluate(cfg, watcher, initial=True)
        component.ensure_valid()
        return expr.truthy(value)


def _finite(value):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("radial target search needs finite numeric inputs")
    try:
        result = f32(value)
    except OverflowError as error:
        raise ValueError("radial target-search input/intermediate exceeds float32") from error
    if not math.isfinite(result):
        raise ValueError("radial target-search input/intermediate exceeds finite float32")
    return result


def _point(value):
    if type(value) not in (tuple, list) or len(value) != 3:
        raise ValueError("radial target-search point needs three coordinates")
    return tuple(_finite(item) for item in value)


def radial_capsule(centre, outer: float, inner: float, feet, radius: float, height: float,
                   *, horizontal: bool = False) -> bool:
    """C8E660 radial accept/reject, with angular bounds disabled by the caller.

    Outer contact is inclusive, inner rejection is exclusive. This deliberately
    does not substitute Python sqrt for the native RSQRT-based output distance.
    Negative dimensions, nonfinite values and overflowing intermediates remain
    outside this explicit finite-input primitive.
    """
    centre, feet = _point(centre), _point(feet)
    outer, inner, radius, height = (_finite(item) for item in (outer, inner, radius, height))
    if min(outer, inner, radius, height) < 0 or type(horizontal) is not bool:
        raise ValueError("radial target-search dimensions/height policy are invalid")
    half = _finite(height * 0.5)
    if horizontal:
        y = centre[1]
    else:
        low = _finite(feet[1] + min(radius, half))
        high = _finite(feet[1] + max(_finite(height - radius), half))
        y = max(low, min(centre[1], high))
    delta = tuple(_finite(a - b) for a, b in zip((feet[0], y, feet[2]), centre, strict=True))
    squares = tuple(_finite(item * item) for item in delta)
    squared = _finite(_finite(squares[0] + squares[1]) + squares[2])
    limit = _finite(radius + outer)
    if squared > _finite(limit * limit):
        return False
    hole = max(0.0, _finite(inner - max(radius, half)))
    return squared >= _finite(hole * hole)
