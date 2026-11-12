"""DAB910 temporal relationship and D6E070 identity from explicit services.

This is pointer/ownership identity, not team hostility or a controller alias.
No actor/component presence or fallback owner is inferred.
"""

from dataclasses import dataclass


def _uint32(value):
    if type(value) is not int or not 0 <= value < 1 << 32:
        raise ValueError("temporal relation requires a native uint32")
    return value


@dataclass(frozen=True)
class TemporalRelationRecord:
    count: int  # component50+70; only zero/nonzero gates the identity query
    base: int  # component50+18
    override: int  # component50+240

    def __post_init__(self):
        for value in (self.count, self.base, self.override):
            _uint32(value)

    def identity(self, invalid_identity):
        invalid_identity = _uint32(invalid_identity)
        if self.override == invalid_identity or self.override & 0x1FFFFFFF == 0x1FFFFFFF:
            return self.base
        return self.override


def related(candidate, subject, *, component50, fallback_owner, identity_of, invalid_identity):
    """DAB910; opaque actor tokens compare by object identity, not equal entity IDs."""
    if subject is None:
        return candidate is None
    if candidate is None:
        raise ValueError("temporal relationship requires a candidate actor")
    record = component50(candidate)
    if record is NotImplemented:
        raise ValueError("temporal component50 presence is unavailable")
    if record is None:
        owner = fallback_owner(candidate, True)
        if owner is NotImplemented:
            raise ValueError("temporal fallback owner is unavailable")
        return owner is subject or candidate is subject
    if not isinstance(record, TemporalRelationRecord):
        raise ValueError("temporal component50 record is unavailable")
    if candidate is subject:
        return True
    if record.count == 0:
        return False
    return record.identity(invalid_identity) == _uint32(identity_of(subject))
