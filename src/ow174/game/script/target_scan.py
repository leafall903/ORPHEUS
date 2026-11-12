"""104319 category choice and explicit root-map lookup; no actor inventory is inferred."""

from dataclasses import dataclass

_UINT64 = (1 << 64) - 1


def _mask(value):
    if type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError("target scan mask must be a native uint64")
    return value


def category(any_bits, all_bits):
    """Return a native broadphase category, or0 for the general scan path.

    This only chooses a scan optimization. It does not accept a target, supply
    its category bits, map controllers to bodies or replace the full filter.
    """
    any_bits, all_bits = _mask(any_bits), _mask(all_bits)
    for required, result in ((1, 1), (0x40000, 0x40000), (0xC0, 0x40), (0x10000, 0x10000)):
        if all_bits & required:
            return result
    if any_bits.bit_count() != 1:
        return 0
    for required, result in ((1, 1), (0x40000, 0x40000), (0xC0, 0x40)):
        if any_bits & required:
            return result
    return 0


def filter_category(config):
    if config is None:
        return 0
    if not isinstance(config, dict) or config.get("$") != "STUConfigVarFilter":
        raise ValueError("target scan requires a resolved native filter config")
    return category(config.get("m_84065FD1", 0), config.get("m_1E14CCA8", 0))


def _key(value):
    if type(value) is not int or not -(1 << 31) <= value < (1 << 31):
        raise ValueError("category root key must be a native int32")
    return value & _UINT64


def root_bucket(key, count):
    """BDC210/BDC3A0's uint64 hash of a sign-extended int32 key."""
    value = _key(key)
    if type(count) is not int or count <= 0 or count & (count - 1):
        raise ValueError("category root bucket count must be a positive power of two")
    value = ((value << 18) + ~value) & _UINT64
    value = ((value ^ (value >> 31)) * 21) & _UINT64
    value = ((value ^ (value >> 11)) * 65) & _UINT64
    return ((value ^ (value >> 22)) & (count - 1)) & 0x7FFFFFFF


@dataclass(frozen=True)
class RootEntry:
    key: int  # native stored uint64, compared to sign-extended lookup key
    next: int  # native signed index; any negative value terminates the chain


@dataclass(frozen=True)
class RootMap:
    """Explicit native category-root storage; no manager or actor roots default.

    None buckets declare an absent table. NotImplemented buckets or a matched
    value mean unknown service data and fail. None values are known null roots.
    Root values are opaque identities; this class never traverses their actors.
    """

    buckets: object = NotImplemented
    entries: tuple = ()
    values: tuple = ()

    def lookup(self, category):
        key = _key(category)
        if self.buckets is None:
            return None
        if not isinstance(self.buckets, tuple):
            raise ValueError("category root buckets are unavailable")
        index = self.buckets[root_bucket(category, len(self.buckets))]
        seen = set()
        while True:
            if type(index) is not int or not -(1 << 31) <= index < (1 << 31):
                raise ValueError("category root chain index must be a native int32")
            if index < 0:
                return None
            if index in seen or index >= len(self.entries):
                raise ValueError("invalid category root collision chain")
            seen.add(index)
            entry = self.entries[index]
            if not isinstance(entry, RootEntry):
                raise ValueError("category root entry is unavailable")
            if _mask(entry.key) == key:
                if index >= len(self.values) or self.values[index] is NotImplemented:
                    raise ValueError("matched category root is unavailable")
                return self.values[index]
            index = entry.next
