"""104319 CE4A60/CEDBD0 entity queries with explicit native lookup services.

The owner supplies the active namespace/environment and both lookup registries.
Scene bindings and the reverse primary-possessor catalog do not imply these inputs.
"""

from dataclasses import dataclass

from ow174.game.script import expr, graph, play

EXISTS = 'STU_E72F6348'
POSSESSED = 'STU_0AC15F78'
DEFINITION = 'STU_1A3FA5B0'
INDEXED_TARGET = 'STU_A37A910D'
TARGET_RECORD_SCALAR = 'STU_D379F515'
COMPONENT76_KEY = 'STU_25ECB55F'


@dataclass(frozen=True)
class Component76Keys:
    primary: frozenset[int] = frozenset()
    fallback: frozenset[int] = frozenset()
    suppressed: frozenset[int] = frozenset()

    def __post_init__(self):
        for entries in (self.primary, self.fallback, self.suppressed):
            if type(entries) is not frozenset or any(
                    type(key) is not int or not 0 <= key <= 0xFFFFFFFF for key in entries):
                raise ValueError("component76 keys must be uint32 sets")

    def contains(self, key: int) -> bool:
        return key not in self.suppressed if key in self.primary else key in self.fallback


@dataclass
class Possession:
    current: int

    def __post_init__(self):
        if type(self.current) is not int or not 0 <= self.current <= 0xFFFFFFFF:
            raise ValueError('current possession must be a native uint32 identity')


@dataclass(frozen=True)
class Target:
    entity: int
    possession: object = NotImplemented  # None declares component44 absence.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('query target must have a native uint32 identity')
        if (self.possession is not NotImplemented and self.possession is not None
                and type(self.possession) is not Possession):
            raise ValueError('expected current possession or known component44 absence')


@dataclass
class Context:
    owner: int
    namespace_present: bool
    environment_present: bool
    possession_context_present: bool
    lookup: object = NotImplemented
    watch_environment: object = NotImplemented  # None declares a missing dependency hub.
    watch_possession: object = NotImplemented
    retired: bool = False

    def __post_init__(self):
        if (type(self.owner) is not int or not 0 <= self.owner <= 0xFFFFFFFF
                or not expr.valid_entity(self.owner)):
            raise ValueError('invalid entity-query owner')
        for value in (self.namespace_present, self.environment_present, self.possession_context_present):
            if type(value) is not bool:
                raise ValueError('entity-query context availability must be known')

    def retire(self):
        self.retired = True


def evaluate(evaluation, cfg):
    """Return NotImplemented only when this world has not installed the service."""
    cls = cfg.get('$')
    if cls in (INDEXED_TARGET, TARGET_RECORD_SCALAR):
        return _target_query(evaluation, cfg, cls)
    if cls == COMPONENT76_KEY:
        instance, watcher = evaluation.instance, evaluation.watcher
        component = instance.component
        resource = cfg.get("m_88B0050A")
        key = 0 if resource is None else graph.guid(resource)
        if key is None:
            component.unsupported(instance, "component76 resource key", cls)
        value = play.evaluate_scalar(instance, cfg.get("m_entity"), initial=expr.Entity(0), watcher=watcher)
        record = component.world.component76_keys(expr.to_entity(value))
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "component76 query owner retired", cls)
        if record is NotImplemented:
            return NotImplemented
        if record is None:
            return False
        if type(record) is not Component76Keys:
            component.unsupported(instance, "component76 keys", cls)
        return record.contains(key & 0xFFFFFFFF)
    if cls == DEFINITION:
        instance, watcher = evaluation.instance, evaluation.watcher
        component = instance.component
        value, success = play.evaluate_scalar_result(instance, cfg.get("m_entity"), watcher=watcher)
        identifier = expr.to_entity(value) if success else expr.INVALID_ENTITY
        definition = component.world.entity_definition_resource(identifier)
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "entity definition lookup owner retired", cls)
        if definition is NotImplemented:
            return NotImplemented
        if type(definition) is not int or not 0 <= definition <= 0xFFFFFFFFFFFFFFFF:
            component.unsupported(instance, "entity definition resource", cls)
        return expr.Asset(definition)
    if cls not in (EXISTS, POSSESSED):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    context = component.world.entity_query_context(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, cls)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or component.world.entity_query_context(component.entity) is not context):
            unsupported('entity query owner retired during callback')
        if any(type(value) is not bool for value in (
                context.namespace_present, context.environment_present, context.possession_context_present)):
            unsupported('entity query context availability')

    def call(callback, feature, *args):
        if not callable(callback):
            unsupported(feature)
        value = callback(*args)
        check()
        return value

    def watch_environment(kind):
        if watcher is not None and context.watch_environment is not None:
            call(context.watch_environment, 'entity query environment subscription', watcher, kind)

    check()
    if cls == EXISTS:
        if not context.namespace_present:
            return expr.EVALUATION_FAILED
        if not context.environment_present:
            watch_environment(46)
            return False
        watch_environment(0)
    elif not context.possession_context_present:
        return expr.EVALUATION_FAILED

    # Native +20 selection uses the first config of 06610EBC, then F64C20
    # unwraps only the first member of nested arrays. Failure/non-entity => 0.
    value = play.evaluate_scalar(instance, cfg.get('m_entity'), initial=expr.Entity(0), watcher=watcher)
    check()
    identifier = expr.to_entity(value) & 0xFFFFFFFF
    if cls == EXISTS and not expr.valid_entity(identifier):
        return False
    target = call(context.lookup, 'entity query lookup', identifier)
    if target is not None and (type(target) is not Target or target.entity != identifier):
        unsupported('entity query lookup result')
    if cls == EXISTS:
        return target is not None
    if target is None:
        watch_environment(0 if context.environment_present else 46)
        return expr.EVALUATION_FAILED
    if target.possession is None:
        return expr.Entity(0)
    if type(target.possession) is not Possession:
        unsupported('entity query current possession')
    if watcher is not None:
        call(context.watch_possession, 'entity query possession subscription', watcher, target)
    if type(target.possession.current) is not int or not 0 <= target.possession.current <= 0xFFFFFFFF:
        unsupported('entity query current possession')
    return expr.Entity(target.possession.current)


def _target_query(evaluation, cfg, cls):
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    world = component.world

    def check_owner():
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, "target query owner retired", cls)

    def entity_input(field):
        value = play.evaluate_scalar(instance, cfg.get(field), initial=expr.Entity(0), watcher=watcher)
        return expr.to_entity(value)

    if cls == INDEXED_TARGET:
        lookup = world.indexed_target_lookup(component.entity)
        check_owner()
        if lookup is NotImplemented:
            return NotImplemented
        if lookup is None:
            return expr.EVALUATION_FAILED
        if not callable(lookup):
            component.unsupported(instance, "indexed target lookup service", cls)
        owner = entity_input("m_152B6961")
        index = expr.to_int(play.evaluate_scalar(instance, cfg.get("m_A60150E2"), watcher=watcher))
        check_owner()
        target = lookup(owner, index)
        check_owner()
        if type(target) is not int or not 0 <= target <= 0xFFFFFFFF:
            component.unsupported(instance, "indexed target identity", cls)
        return expr.Entity(target)

    target = entity_input("m_target")
    owner = entity_input("m_7826B991")
    check_owner()
    value = world.target_record_scalar(owner, target)
    check_owner()
    if value is NotImplemented:
        return NotImplemented
    if value is None:
        return expr.EVALUATION_FAILED
    if type(value) not in (float, int):
        component.unsupported(instance, "target record scalar", cls)
    return expr.f32(value)
