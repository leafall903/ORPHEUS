"""104319 query entry and native mask/tag stages; full admission is a host service."""

import math
from contextlib import nullcontext
from dataclasses import dataclass

from ow174.game.script import expr, play

CLASS = 'STU_AC829876'
TARGET_PARAMETER = 8  # Expression opcode 13 uses the authored parameter identifier.
MASK_FIELDS = ('m_84065FD1', 'm_1E14CCA8', 'm_4EA686DB',
               'm_27CF71D5', 'm_66747E7D', 'm_89831602')
TAG_FIELDS = ('m_9C7BD8B8', 'm_B486603E', 'm_8B2A4DF3')


@dataclass(frozen=True)
class Target:
    """Host view of an entity. None bits declares component25 absence."""

    entity: int
    gate61: bool
    bits: int | None


@dataclass(frozen=True)
class RelationResult:
    """Inputs from BCAAE0's opaque relation service, not inferred team labels.

    The caller must supply the actual source/target identity comparison and
    C1CF60 (or spectator C1C690) result. None instead means no source context.
    Native relation lookup/filter-component provisioning remain host services.
    """

    same_entity: bool
    predicate: bool

    def __post_init__(self):
        if type(self.same_entity) is not bool or type(self.predicate) is not bool:
            raise ValueError('native relation inputs must be bool')


def matches_masks(bits, cfg, *, relation=None):
    """BCAAE0 bit/relationship stage only; NOT full D0DA20 entity admission.

    None bits declares known component absence. An absent source skips all
    relation masks. Higher relationship-mask bits are ignored by native code.
    Valid-target flag/tag/condition stages must still execute in the host.
    """
    masks = tuple(cfg.get(field, 0) for field in MASK_FIELDS)
    if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in masks):
        raise ValueError('native filter masks must be uint64')
    if bits is None:
        bits = 0
    if type(bits) is not int or not 0 <= bits <= 0xFFFFFFFFFFFFFFFF:
        raise ValueError('native filter bits must be uint64 or known absence')
    if relation is not None and type(relation) is not RelationResult:
        raise ValueError('expected native relation result or source absence')
    any_bits, all_bits, excluded_bits, any_relation, all_relation, excluded_relation = masks
    admitted = bool(any_bits & bits) or any_bits == 0x3FFFFFFF
    required = bits & all_bits == all_bits
    excluded = bool(excluded_bits & bits)
    if relation is not None:
        first = not relation.predicate or relation.same_entity
        second = not first
        matched = int(first) | (int(second) << 1) | (int(relation.same_entity) << 2)
        admitted |= bool(any_relation & matched)
        required &= (all_relation & 7) & matched == all_relation & 7
        excluded |= bool(excluded_relation & matched)
    return admitted and required and not excluded


@dataclass(frozen=True)
class TagSet:
    """Current native runtime tag IDs; resource GUID-to-ID binding is external.

    DB4E70/DB5030 consult removals for a static tag, additions for a nonstatic
    tag. A removed static tag stays absent even if also present in additions.
    These ephemeral inputs do not introduce stored world/VM state.
    """

    static: frozenset = frozenset()
    added: frozenset = frozenset()
    removed: frozenset = frozenset()

    def __post_init__(self):
        for name in ('static', 'added', 'removed'):
            values = tuple(getattr(self, name))
            if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFF for value in values):
                raise ValueError('native runtime tag IDs must be uint32')
            object.__setattr__(self, name, frozenset(values))

    def contains(self, token):
        if type(token) is not int or not 0 <= token <= 0xFFFFFFFFFFFFFFFF:
            raise ValueError('native query tag tokens must be uint64')
        identifier = token & 0xFFFFFFFF
        return identifier not in self.removed if identifier in self.static else identifier in self.added


def matches_tags(tags, any_tags=(), all_tags=(), excluded_tags=()):
    """DB2FD0 list stage, with DB4E70/DB5030 membership semantics.

    None tags declares known component absence. Native resource/array/scalar
    evaluation, tag-ID binding and component lookup remain host responsibilities.
    """
    if tags is None:
        tags = TagSet()
    if type(tags) is not TagSet:
        raise ValueError('expected current native tag set or known component absence')
    lists = tuple(tuple(values) if values is not None else ()
                  for values in (any_tags, all_tags, excluded_tags))
    if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF
           for values in lists for value in values):
        raise ValueError('native query tag tokens must be uint64')
    any_tags, all_tags, excluded_tags = lists
    return ((not any_tags or any(tags.contains(value) for value in any_tags))
            and all(tags.contains(value) for value in all_tags)
            and not any(tags.contains(value) for value in excluded_tags))


def collect_tags(instance, cfg, *, watcher=None):
    """DB2C10: ordered nonzero Asset tokens, deduplicated by all64 bits.

    The existing native iterator adapter supplies values. Full-world component
    provisioning and conversion of resource identity to current tags are separate.
    Callback retirement checks are server safety policy, not native ownership proof.
    """
    component = instance.component

    def values(config, depth=0):
        # An empty/failed group child can retire its owner without yielding.
        # Check before the next child, rather than waiting for the next value.
        _check_owner(instance)
        if depth >= play.MAX_CONFIG_DEPTH:
            component.budget_exceeded('entity-filter tag config depth exhausted')
        if isinstance(config, dict) and config.get('$') == 'STU_06610EBC':
            component.consume_work('entity-filter tag config group')
            for child in config.get('m_configVars') or ():
                yield from values(child, depth + 1)
        else:
            iterator = play._iter_values(instance, config, _depth=depth, watcher=watcher)
            try:
                yield from iterator
            finally:
                iterator.close()
        _check_owner(instance)

    with component.operation():
        _check_owner(instance)
        result, seen = [], set()
        iterator = values(cfg)
        try:
            for value in iterator:
                _check_owner(instance)
                component.consume_work('entity-filter tag collection')
                token = play._asset(value) & 0xFFFFFFFFFFFFFFFF
                if token and token not in seen:
                    result.append(token)
                    seen.add(token)
        finally:
            iterator.close()
        _check_owner(instance)
        return tuple(result)


def _check_owner(instance):
    component = instance.component
    component.ensure_valid()
    if (instance.stopped or instance.teardown
            or component.instances.get(instance.id) is not instance):
        component.unsupported(instance, 'entity filter owner retired during callback', CLASS)


def _condition_bool(value, default, component):
    """Supported F64940 values, including the caller's empty-array default."""
    while type(value) is tuple:
        component.consume_work('filter condition array conversion')
        if not value:
            return default
        value = value[0]
    if value is None:
        return False
    if type(value) is bool:
        return value
    if type(value) is int:
        return expr.i32(value) != 0
    if type(value) in (float, expr.Vec3):
        items = (value,) if type(value) is float else value
        return any(item != 0 and not math.isnan(item) for item in map(expr.f32, items))
    if type(value) is expr.Asset:
        return bool(value.guid & 0xFFFFFFFFFFFFFFFF)
    if type(value) is expr.Entity:
        return expr.valid_entity(value.id & 0xFFFFFFFF)
    if type(value) is expr.Map:
        return bool(value.items)
    return NotImplemented


def _condition(instance, cfg, watcher, default, *, target=0):
    if cfg is None:
        return default
    component = instance.component
    scope = (component.parameter_scope({TARGET_PARAMETER: expr.Entity(target)})
             if expr.valid_entity(target) else nullcontext())
    with scope:
        value, success = play.evaluate_scalar_result(instance, cfg, initial=0, watcher=watcher)
        _check_owner(instance)
        if not success:
            return default
        result = _condition_bool(value, default, component)
        if result is NotImplemented:
            component.unsupported(instance, 'entity filter condition value', CLASS)
        return result


def admit(host, entity, cfg, *, instance, watcher):
    """D0DA20 routing for hosts that provide current components and engine services.

    Host watch handles dependency availability. Custom and post services receive
    the authored config so unresolved engine rules stay explicit.
    """
    component = instance.component

    def unsupported(feature):
        component.unsupported(instance, 'entity filter ' + feature, CLASS)

    def call(name, *args):
        callback = getattr(host, name, None)
        if not callable(callback):
            unsupported(name)
        component.consume_work('entity filter ' + name)
        result = callback(*args)
        _check_owner(instance)
        if result is NotImplemented:
            unsupported(name)
        return result

    with component.operation():
        _check_owner(instance)
        if cfg is None:
            return True
        if not isinstance(cfg, dict):
            unsupported('config')
        target = call('lookup', entity)
        if target is None:
            if watcher is not None:
                call('watch', 'missing', entity, watcher)
            return False
        if type(target) is not Target or target.entity != entity or type(target.gate61) is not bool:
            unsupported('lookup result')
        if target.bits is not None and (type(target.bits) is not int
                                        or not 0 <= target.bits <= 0xFFFFFFFFFFFFFFFF):
            unsupported('lookup filter bits')
        masks = tuple(cfg.get(field, 0) for field in MASK_FIELDS)
        if any(type(value) is not int or not 0 <= value <= 0xFFFFFFFFFFFFFFFF for value in masks):
            unsupported('mask config')
        if masks[0] != 0x3FFFFFFF and not target.gate61 and any(masks):
            return False
        if watcher is not None and target.bits is not None:
            call('watch', 'target-filter', target, watcher)
        source = call('source', instance)
        if source is not None and type(source) is not Target:
            unsupported('source result')
        if watcher is not None and source is not None and source.bits is not None:
            call('watch', 'source-filter', source, watcher)
        relation = None
        if source is not None and any(masks[3:]):
            relation = call('relation', source, target)
            if type(relation) is not RelationResult:
                unsupported('relation result')
        if any(masks) and not matches_masks(target.bits, cfg, relation=relation):
            return False
        if not _condition(instance, cfg.get('m_condition'), watcher, True, target=entity):
            return False
        tag_configs = tuple(cfg.get(field) for field in TAG_FIELDS)
        if any(config is not None for config in tag_configs):
            if watcher is not None:
                call('watch', 'tags', target, watcher)
            lists = tuple(collect_tags(instance, config, watcher=watcher) for config in tag_configs)
            tags = call('tags', target)
            if tags is not None and type(tags) is not TagSet:
                unsupported('tags result')
            if not matches_tags(tags, *lists):
                return False
        result = call('custom', target, cfg, instance)
        if type(result) is not int or result not in (0, 1, 2):
            unsupported('custom result')
        if result == 1:
            return False
        if not _condition(instance, cfg.get('m_F61726FE'), watcher, False):
            return True
        result = call('post', target, cfg, instance)
        if type(result) is not bool:
            unsupported('post result')
        return result


def evaluate(evaluation, cfg):
    if cfg.get('$') != CLASS:
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component

    with component.operation():
        _check_owner(instance)
        # CF4340 uses the invalid-entity constant0 on absent/failed providers,
        # then still calls environment+150. Invalid IDs are not an early return.
        value = play.evaluate_scalar(instance, cfg.get('m_entity'), initial=expr.Entity(0), watcher=watcher)
        _check_owner(instance)
        identifier = expr.to_entity(value) & 0xFFFFFFFF
        result = component.world.matches_entity_filter(
            identifier, cfg.get('m_filter'), instance=instance, watcher=watcher)
        _check_owner(instance)
        if result is NotImplemented:
            return NotImplemented
        if type(result) is not bool:
            component.unsupported(instance, 'entity filter host result must be bool', CLASS)
        return result
