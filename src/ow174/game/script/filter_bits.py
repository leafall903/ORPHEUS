"""104319 bit/relation stage and status inputs of D0DA20's entity filter.

This primitive requires declared filter bits and an explicit native relationship
answer when used. It does not implement target lookup, filter expressions, tags,
observers or the rest of D0DA20, and does not make AC829876 available by itself.
"""

import math

from ow174.game.script import expr


def _uint64(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("filter mask must be a native uint64")
    return value


def has_component(masks, identifier):
    """182C470 over captured/native uint64 presence words; unknown words reject."""
    if type(masks) not in (tuple, list) or not 1 <= len(masks) <= 4:
        raise ValueError("native component presence requires one to four mask words")
    if type(identifier) is not int or not 0 <= identifier <= 255:
        raise ValueError("native component identifier must be a byte")
    for mask in masks:
        _uint64(mask)
    word = identifier >> 6
    if word >= len(masks):
        raise NotImplementedError("native component presence word is unavailable")
    return bool(masks[word] & (1 << (identifier & 63)))


def status10_from_masks(masks, *, primary=None, health=None, flags=None):
    """Use actual presence words, requiring only the reached payload services.

    This never treats a missing query as known component absence. It is not a
    live attachment registry; callers own presence generations and lifetime.
    """
    def query(callback, name):
        if not callable(callback):
            raise NotImplementedError(f"native {name} status payload is unavailable")
        return callback()

    if has_component(masks, 57):
        value = query(primary, "component57")
        if type(value) is not bool:
            raise ValueError("native primary status query must return bool")
        return value
    value = False
    if has_component(masks, 51):
        value = query(health, "component51")
        if type(value) is not bool:
            raise ValueError("native health status query must return bool")
    if has_component(masks, 28) and not value:
        stored = query(flags, "component28")
        if type(stored) is not int or not 0 <= stored <= 0xFFFFFFFF:
            raise ValueError("native component28 status flags must be uint32")
        value = bool(stored & 0x2000)
    return value


def status51(*, value78, value70):
    """BCBF00 with explicit HealthComponent virtual78/70 float results.

    Both native getter calls use integer0/boolfalse arguments. Their detailed
    pool selection is not inferred here. NaN handling follows UCOMISS/COMISS:
    an unordered first comparison rejects; an unordered second accepts.
    """
    if any(type(value) not in (int, float) for value in (value78, value70)):
        raise ValueError("native health getter results must be floats")
    first, second = expr.f32(value78), expr.f32(value70)
    if first == 0.0 or math.isnan(first):
        return False
    return math.isnan(second) or abs(second) <= 2 ** -23


def status57(*, component4_present, collection_flags, suppress, bypass=False):
    """D8F180 over declared decoded component/config/collection inputs.

    Missing component4 or its +198 collection returns true. Otherwise the
    BC9310 config byte suppresses the result unless bypassed, then collection
    byte16D8 bit2 supplies the answer. No gameplay status name is inferred.
    """
    if any(type(value) is not bool for value in (component4_present, suppress, bypass)):
        raise ValueError("native primary status inputs must be explicit bools")
    if (collection_flags is not None and
            (type(collection_flags) is not int or not 0 <= collection_flags <= 0xFF)):
        raise ValueError("native status collection flags must be byte or known absence")
    if not component4_present or collection_flags is None:
        return True
    if suppress and not bypass:
        return False
    return bool(collection_flags & 4)


def status10(*, component57, component51, component28_flags):
    """B8BEB0 over declared component answers, without inventing status names.

    None means known absence, not unavailable data. Component57's D8F180(false)
    result takes precedence even when false; only its absence permits the
    component51 BCBF00 answer and component28 flag2000 fallback. Child predicates
    and component existence still require real engine-service evidence.
    """
    for value in (component57, component51):
        if value is not None and type(value) is not bool:
            raise ValueError("native status component answer must be bool or known absence")
    if (component28_flags is not None and
            (type(component28_flags) is not int or not 0 <= component28_flags <= 0xFFFFFFFF)):
        raise ValueError("native component28 flags must be uint32 or known absence")
    if component57 is not None:
        return component57
    return bool(component51 or (component28_flags is not None and component28_flags & 0x2000))


def project(current_bits, replicated_bits, *, status10):
    """B8B020 prefix: select the base, then recompute bit10 from B8BEB0.

    None declares no replicated component input. status10 is an explicit engine
    answer, not inferred from health/team or an invented status meaning. This
    pure projection does not perform native notifications or later propagation.
    """
    current_bits = _uint64(current_bits)
    base = current_bits if replicated_bits is None else _uint64(replicated_bits)
    if type(status10) is not bool:
        raise ValueError("native status10 answer must be explicit bool")
    return (base & ~0x10) | (0x10 if status10 else 0)


def matches(bits, any_bits, all_bits, excluded_bits, any_relation=0, all_relation=0,
            excluded_relation=0, *, source_present=False, same_entity=False, relationship=None):
    """Match the helper's exact six masks against explicit component25 inputs.

    None declares the target filter component absent. relationship(spectator)
    supplies C1CF60's result, or C1C690's result when target bit28 is set. Those
    services and their spectator policy are not inferred from team numbers.
    same_entity describes the native pointer comparison, not a guessed owner.
    """
    values = (any_bits, all_bits, excluded_bits, any_relation, all_relation, excluded_relation)
    any_bits, all_bits, excluded_bits, any_relation, all_relation, excluded_relation = (
        _uint64(value) for value in values)
    bits = 0 if bits is None else _uint64(bits)
    if type(source_present) is not bool or type(same_entity) is not bool:
        raise ValueError("filter relationship availability and identity must be explicit bools")
    if same_entity and not source_present:
        raise ValueError("same entity requires a source")
    accepted = bool(bits & any_bits) or any_bits == 0x3FFFFFFF
    required = bits & all_bits == all_bits
    excluded = bool(bits & excluded_bits)
    if source_present and (any_relation | all_relation | excluded_relation):
        if not callable(relationship):
            raise ValueError("native relationship service is unavailable")
        related = relationship(bool(bits & (1 << 28)))
        if type(related) is not bool:
            raise ValueError("native relationship service must return bool")
        # Native al = !(relationship && source != target); cl = !al.
        first = not related or same_entity
        second = not first
        accepted |= bool((any_relation & 1 and first) or (any_relation & 2 and second)
                         or (any_relation & 4 and same_entity))
        required &= not bool((all_relation & 1 and second) or (all_relation & 2 and first)
                             or (all_relation & 4 and not same_entity))
        excluded |= bool((excluded_relation & 1 and first) or (excluded_relation & 2 and second)
                         or (excluded_relation & 4 and same_entity))
    return bool(accepted and required and not excluded)
