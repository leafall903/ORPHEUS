"""104319 BCAAE0 bit/relation stage of D0DA20's entity filter.

This primitive requires declared filter bits and an explicit native relationship
answer when used. It does not implement target lookup, filter expressions, tags,
observers or the rest of D0DA20, and does not make AC829876 available by itself.
"""


def _uint64(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("filter mask must be a native uint64")
    return value


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
