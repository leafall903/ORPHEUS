"""104319 BCAAE0 component25 masks and relationship contribution.

This is the mask stage of D0DA20, not a complete entity-filter query.
"""

from dataclasses import dataclass

ALL = 0x3FFFFFFF


def _mask(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError('filter mask must be a native uint64')


@dataclass(frozen=True)
class Masks:
    any_bits: int = 0  # m_84065FD1
    all_bits: int = 0  # m_1E14CCA8
    no_bits: int = 0  # m_4EA686DB
    any_relation: int = 0  # m_27CF71D5
    all_relation: int = 0  # m_66747E7D
    no_relation: int = 0  # m_89831602

    def __post_init__(self):
        for value in (self.any_bits, self.all_bits, self.no_bits,
                      self.any_relation, self.all_relation, self.no_relation):
            _mask(value)

    @property
    def combined(self):
        return (self.any_bits | self.all_bits | self.no_bits
                | self.any_relation | self.all_relation | self.no_relation)


def matches(bits, masks, *, owner_present, same_entity=False, relation_enemy=NotImplemented):
    """BCAAE0: combine raw bits and explicit native relationship service results.

    Missing component25 has bits0. The relation callback receives whether bit28
    selects the spectator relation path. It supplies the C1C690/C1CF60 result;
    this helper does not manufacture native owner/team/inheritance inputs.
    """
    if bits is None:
        bits = 0
    _mask(bits)
    if type(masks) is not Masks or type(owner_present) is not bool or type(same_entity) is not bool:
        raise ValueError('expected declared filter masks and pointer relationships')
    any_match = bool(bits & masks.any_bits) or masks.any_bits == ALL
    all_match = bits & masks.all_bits == masks.all_bits
    no_match = bool(bits & masks.no_bits)
    if owner_present and (masks.any_relation | masks.all_relation | masks.no_relation):
        if not callable(relation_enemy):
            raise ValueError('native filter relationship service unknown')
        hostile = relation_enemy(bool(bits & (1 << 28)))
        if type(hostile) is not bool:
            raise ValueError('native filter relationship result must be boolean')
        enemy = hostile and not same_entity
        friendly = not enemy

        def selected(flags):
            return bool((flags & 1 and friendly) or (flags & 2 and enemy)
                        or (flags & 4 and same_entity))

        any_match |= selected(masks.any_relation)
        all_match &= not ((masks.all_relation & 1 and enemy)
                          or (masks.all_relation & 2 and friendly)
                          or (masks.all_relation & 4 and not same_entity))
        no_match |= selected(masks.no_relation)
    return bool(any_match and all_match and not no_match)
