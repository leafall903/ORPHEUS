"""104319 entity queries with explicit native lookup and possession services.

The owner supplies the active namespace/environment and both lookup registries.
Scene bindings and the reverse primary-possessor catalog do not imply these inputs.
"""

from dataclasses import dataclass

from ow174.game.script import expr, play

EXISTS = 'STU_E72F6348'
POSSESSED = 'STU_0AC15F78'
POSSESSOR = 'STU_F7262B36'  # CDBDE0 -> D15520: component43's primary possessor
BODY = 'STU_00707E1E'  # CE7660 -> BD4A90: component44 +D0
TARGET_RECORD_SCALAR = 'STU_D379F515'  # CE7EA0: component46 target record +0x114


@dataclass
class Possession:
    current: int

    def __post_init__(self):
        if type(self.current) is not int or not 0 <= self.current <= 0xFFFFFFFF:
            raise ValueError('current possession must be a native uint32 identity')


@dataclass
class BodyField:
    entity: int

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('component44 body field must be a native uint32 identity')


@dataclass(frozen=True)
class Target:
    entity: int
    possession: object = NotImplemented  # None declares component44 absence.
    possessor: object = NotImplemented  # None declares component43 absence.
    body_field: object = NotImplemented  # None declares component44 absence.

    def __post_init__(self):
        if type(self.entity) is not int or not 0 <= self.entity <= 0xFFFFFFFF:
            raise ValueError('query target must have a native uint32 identity')
        if (self.possession is not NotImplemented and self.possession is not None
                and type(self.possession) is not Possession):
            raise ValueError('expected current possession or known component44 absence')
        if (self.possessor is not NotImplemented and self.possessor is not None
                and type(self.possessor) is not Possession):
            raise ValueError('expected primary possessor or known component43 absence')
        if (self.body_field is not NotImplemented and self.body_field is not None
                and type(self.body_field) is not BodyField):
            raise ValueError('expected body field or known component44 absence')


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
    watch_possessor: object = NotImplemented

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
    if cls == TARGET_RECORD_SCALAR:
        return _target_record_scalar(evaluation, cfg)
    if cls not in (EXISTS, POSSESSED, POSSESSOR, BODY):
        return NotImplemented
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component
    provider = (component.world.entity_possessor_context if cls == POSSESSOR
                else component.world.entity_query_context)
    context = provider(component.entity)
    if context is NotImplemented:
        return NotImplemented

    def unsupported(feature):
        component.unsupported(instance, feature, cls)

    def check():
        component.ensure_valid()
        if (type(context) is not Context or context.owner != component.entity or context.retired
                or instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance
                or provider(component.entity) is not context):
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
    elif cls != BODY and not context.possession_context_present:
        return expr.EVALUATION_FAILED

    # Native +20 selection uses the first config of 06610EBC, then F64C20
    # unwraps only the first member of nested arrays. Failure/non-entity => 0.
    selector = {POSSESSOR: 'm_801B298F', BODY: 'm_00903303'}.get(cls, 'm_entity')
    value = play.evaluate_scalar(instance, cfg.get(selector), initial=expr.Entity(0), watcher=watcher)
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
        return expr.Entity(0) if cls == BODY else expr.EVALUATION_FAILED
    if cls == BODY:
        field = target.body_field
        if field is None:
            return expr.Entity(0)
        if type(field) is not BodyField:
            unsupported('entity query component44 body field')
        if watcher is not None:
            call(context.watch_possession, 'entity query possession subscription', watcher, target)
        if type(field.entity) is not int or not 0 <= field.entity <= 0xFFFFFFFF:
            unsupported('entity query component44 body field')
        return expr.Entity(field.entity)
    relation = target.possessor if cls == POSSESSOR else target.possession
    if relation is None:
        return expr.Entity(0)
    if type(relation) is not Possession:
        unsupported('entity query primary possessor' if cls == POSSESSOR
                    else 'entity query current possession')
    if watcher is not None:
        callback = context.watch_possessor if cls == POSSESSOR else context.watch_possession
        call(callback, 'entity query possession subscription', watcher, target)
    if type(relation.current) is not int or not 0 <= relation.current <= 0xFFFFFFFF:
        unsupported('entity query current possession')
    return expr.Entity(relation.current)


def _target_record_scalar(evaluation, cfg):
    instance, watcher = evaluation.instance, evaluation.watcher
    component = instance.component

    def entity(field):
        value = play.evaluate_scalar(instance, cfg.get(field), initial=expr.Entity(0), watcher=watcher)
        return expr.to_entity(value) & 0xFFFFFFFF

    def check_owner():
        component.ensure_valid()
        if (instance.stopped or instance.teardown
                or component.instances.get(instance.id) is not instance):
            component.unsupported(instance, 'target record query owner retired', TARGET_RECORD_SCALAR)

    target = entity('m_target')
    owner = entity('m_7826B991')
    check_owner()
    value = component.world.target_record_scalar(owner, target)
    check_owner()
    if value is NotImplemented:
        return NotImplemented
    if value is None:
        return expr.EVALUATION_FAILED
    if type(value) not in (float, int):
        component.unsupported(instance, 'target record scalar', TARGET_RECORD_SCALAR)
    return expr.f32(value)
