"""104319 DB4E70/DB5030 tag membership and DB2FD0's resolved-component predicate.

This primitive consumes declared component data. It does not invent actor tag
storage, effect lifetimes, config iterators, dependency subscriptions or entity
lookup, and cannot supply the complete D0DA20 filter by itself.
"""

from dataclasses import dataclass

from ow174.game.script import play


def _uint(value, bits):
    if type(value) is not int or not 0 <= value < 1 << bits:
        raise ValueError(f"tag identifier must be a native uint{bits}")
    return value


def _requests(values):
    if values is None:
        return ()
    if type(values) is not tuple:
        raise ValueError("tag request must be a tuple or known absent list")
    return tuple(_uint(value, 64) & 0xFFFFFFFF for value in values)


@dataclass(frozen=True)
class Tags:
    """Resolved base/+100 additions/+110 removals; all stored keys are uint32.

    The native small and large storage forms have the same membership rules.
    Removals affect base tags; additions are checked only when a base tag is
    absent. Consequently a removed base tag is not restored by an addition.
    """

    base: tuple = ()
    added: tuple = ()
    removed: tuple = ()

    def __post_init__(self):
        for values in (self.base, self.added, self.removed):
            if type(values) is not tuple:
                raise ValueError("declared tag storage must be a tuple")
            for value in values:
                _uint(value, 32)

    def contains(self, identifier):
        key = _uint(identifier, 64) & 0xFFFFFFFF
        return key not in self.removed if key in self.base else key in self.added

    def any(self, identifiers):
        return any(self.contains(key) for key in _requests(identifiers))

    def all(self, identifiers):
        return all(self.contains(key) for key in _requests(identifiers))


def matches(tags, any_tags=None, all_tags=None, excluded_tags=None):
    """DB2FD0 after component lookup; None declares actual component absence.

    An absent component fails a nonempty any/all requirement and passes a
    purely excluding filter. Unknown component state must not be passed as None.
    """
    any_tags, all_tags, excluded_tags = map(_requests, (any_tags, all_tags, excluded_tags))
    if tags is None:
        return not any_tags and not all_tags
    if type(tags) is not Tags:
        raise ValueError("tag component must be declared Tags or known absence")
    return ((not any_tags or tags.any(any_tags)) and tags.all(all_tags)
            and not tags.any(excluded_tags))


def collect_values(values):
    """DB2C10/F65540 value arm: nonzero assets, full-key deduplication, stable order."""
    result = []
    for value in values:
        identifier = play._asset(value) & 0xFFFFFFFFFFFFFFFF
        if identifier and identifier not in result:
            result.append(identifier)
    return tuple(result)


def collect(instance, config, *, watcher=None):
    """Use the existing owned generic iterator; unknown services retain their faults."""
    with instance.component.operation():
        return collect_values(play._iter_values(instance, config, watcher=watcher))


def evaluate(instance, config, tags, *, watcher=None):
    """D0EC70's tag stage over declared resolved storage, not the entire entity filter."""
    if not isinstance(config, dict) or config.get('$') != 'STUConfigVarFilter':
        return NotImplemented
    fields = tuple(config.get(key) for key in ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3'))
    if all(field is None for field in fields):
        return True  # native bypasses collection/component lookup on this exact arm
    with instance.component.operation():
        values = tuple(collect(instance, field, watcher=watcher) for field in fields)
        return matches(tags, *values)
