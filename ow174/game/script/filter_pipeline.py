"""D0DA20 after target lookup, with explicitly resolved engine services.

Bit/condition kernels are unchanged private reuse from lane0200; tag kernels
come from the native comparisons in this lane. No namespace/cache lookup,
category/status inference, actor tag storage or production World is supplied.
"""

from dataclasses import dataclass

from ow174.game.script import expr, filter_bits, filter_condition, filter_tags, play
from ow174.game.script.entity_team import FilterBits

_MASKS = ('m_84065FD1', 'm_1E14CCA8', 'm_4EA686DB',
          'm_27CF71D5', 'm_66747E7D', 'm_89831602')
_TAGS = ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3')
_INT_MIN = -(1 << 31)


@dataclass(eq=False)
class Subject:
    """Declared object identity and component inputs, including known absence.

    Object identity models native source/target pointer equality. Two records
    with the same entity number are not silently considered the same object.
    FilterBits is mutable so a dependency callback can change its stored bits.
    """

    entity: int
    component97: bool
    bits: object = NotImplemented
    tags: object = NotImplemented

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('resolved filter entity must be a uint32')
        if type(self.component97) is not bool:
            raise ValueError('component97 availability must be explicit')


@dataclass
class Inventory:
    """Declared 1AC6E70/1AC6E80/1AC6C60 services; no engine storage is inferred."""

    count: object = NotImplemented
    value: object = NotImplemented
    contains: object = NotImplemented


@dataclass
class Services:
    relationship: object = NotImplemented
    watch_bits: object = NotImplemented
    watch_tags: object = NotImplemented
    registry: object = NotImplemented
    inventory: object = NotImplemented
    integer: object = NotImplemented
    boolean: object = NotImplemented  # optional scalar service when no Instance is supplied
    source: object = NotImplemented  # lazy namespace owner resolution after target subscription


def _call(callback, name, *args):
    if not callable(callback):
        raise ValueError(f'native filter {name} service is unavailable')
    return callback(*args)


def _component(subject):
    if subject is None:
        return None
    value = subject.bits
    if value is not None and type(value) is not FilterBits:
        raise ValueError('filter component availability must be known')
    return value


def _integer(value, name, *, short=False):
    low, high = (-(1 << 15), (1 << 15) - 1) if short else (-(1 << 31), 0xFFFFFFFF)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'native filter {name} result has an invalid integer domain')
    return value if short else expr.i32(value)


def _boolean(instance, config, watcher, services):
    if config is None:
        return False
    if instance is None:
        result = _call(services.boolean, 'boolean evaluator', config, False)
        if type(result) is not bool:
            raise ValueError('native filter boolean evaluator must return bool')
        return result
    value, success = play.evaluate_scalar_result(instance, config, initial=0, watcher=watcher)
    return filter_condition.boolean(value, False) if success else False


def resolved(instance, config, target, source, services, *, watcher=None):
    """Resolved D0DA20 stages, including lazy service requirements and native ordering.

    Entity/cache lookup, retirement checks and actual service bindings belong to
    the caller. Unknown service availability is an error, never a passing filter.
    """
    if config is None:
        return True
    if instance is not None:
        with instance.component.operation():
            return _resolved(instance, config, target, source, services, watcher=watcher)
    return _resolved(instance, config, target, source, services, watcher=watcher)


def _resolved(instance, config, target, source, services, *, watcher):
    if config is None:
        return True
    if not isinstance(config, dict) or config.get('$') != 'STUConfigVarFilter':
        return NotImplemented
    if (type(target) is not Subject
            or (source is not NotImplemented and source is not None and type(source) is not Subject)):
        raise ValueError('native filter requires declared resolved objects')
    if type(services) is not Services:
        raise ValueError('native filter requires explicit services')
    masks = tuple(filter_bits._uint64(config.get(key, 0)) for key in _MASKS)
    combined = 0
    for value in masks:
        combined |= value
    if masks[0] != 0x3FFFFFFF and not target.component97 and combined:
        return False
    # Target subscription precedes source resolution in D0DE61..D0E104. Save
    # each component pointer before its own callback, not both beforehand.
    target_bits = _component(target)
    if watcher is not None and target_bits is not None:
        _call(services.watch_bits, 'filter subscription', watcher, target, target_bits)
    if source is NotImplemented:
        source = _call(services.source, 'source lookup')
        if source is not None and type(source) is not Subject:
            raise ValueError('native filter source lookup result is invalid')
    source_bits = _component(source)
    if watcher is not None and source_bits is not None:
        _call(services.watch_bits, 'filter subscription', watcher, source, source_bits)
    if combined and not filter_bits.matches(
        target_bits.bits if target_bits is not None else None, *masks,
        source_present=source is not None, same_entity=source is target,
        relationship=services.relationship,
    ):
        return False
    if not filter_condition.evaluate(instance, config.get('m_condition'), target.entity, watcher=watcher):
        return False
    if watcher is not None and any(config.get(key) is not None for key in _TAGS):
        if target.tags is NotImplemented:
            raise ValueError('tag component availability must be known')
        if target.tags is not None:
            _call(services.watch_tags, 'tag subscription', watcher, target, target.tags)
    if not filter_tags.evaluate(instance, config, target.tags, watcher=watcher):
        return False
    registry = _integer(_call(services.registry, 'registry', target, config, instance), 'registry')
    if registry == 1:
        return False
    if not _boolean(instance, config.get('m_F61726FE'), watcher, services):
        return True
    inventory = _call(services.inventory, 'inventory lookup', target)
    if inventory is None:
        return True
    if type(inventory) is not Inventory:
        raise ValueError('inventory component availability must be known')
    identifier = _integer(_call(services.integer, 'integer evaluator',
                                config.get('m_B40362FC'), _INT_MIN), 'integer evaluator')
    if identifier == _INT_MIN:
        return False
    if _integer(_call(inventory.count, 'inventory count'), 'inventory count') <= 0:
        return False
    index = 0
    while True:
        if instance is not None:
            instance.component.consume_work('filter inventory iteration')
        value = _integer(_call(inventory.value, 'inventory value', index), 'inventory value', short=True)
        if value > 0:
            contained = _call(inventory.contains, 'inventory membership', index, identifier)
            if type(contained) is not bool:
                raise ValueError('native filter inventory membership must return bool')
            if contained:
                return True
        index += 1
        if index >= _integer(_call(inventory.count, 'inventory count'), 'inventory count'):
            return False
